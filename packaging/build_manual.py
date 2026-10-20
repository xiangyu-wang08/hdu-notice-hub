"""Build the release installation manual from docs/INSTALL.md.

Usage (run from the repository root):
    python -m pip install -r packaging/docs-requirements.txt
    python packaging/build_manual.py

The application itself has no dependency on this tool or its requirements.
This small Markdown renderer supports headings, paragraphs, ordered/unordered
lists, fenced code, tables, inline code, emphasis, links and horizontal rules.
It does not execute HTML, JavaScript, Markdown or embedded shell commands.
System fonts are embedded as subsets; no system font files are redistributed.
Use --font, --bold-font and --mono-font to select other embeddable TTF/TTC fonts.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import struct
import sys
from urllib.parse import urljoin

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    LongTable,
    PageTemplate,
    Paragraph,
    Spacer,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
if not re.fullmatch(r"V\d+\.\d+\.\d+", RELEASE_VERSION):
    raise ValueError("VERSION must use the public release format V<major>.<minor>.<patch>")
NAVY = colors.HexColor("#17314c")
BLUE = colors.HexColor("#21659a")
INK = colors.HexColor("#253547")
MUTED = colors.HexColor("#60758a")
PALE = colors.HexColor("#f1f5f9")
RULE = colors.HexColor("#d6e1eb")
FONT = "ManualSans"
BOLD = "ManualSansBold"
MONO = "ManualMono"


def embedding_flags(path: Path) -> int:
    """Read OS/2.fsType from the first face without adding fontTools."""
    data = path.read_bytes()
    offset = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
    count = struct.unpack_from(">H", data, offset + 4)[0]
    for number in range(count):
        tag, _, table_offset, _ = struct.unpack_from(
            ">4sIII", data, offset + 12 + number * 16
        )
        if tag == b"OS/2":
            return struct.unpack_from(">H", data, table_offset + 8)[0]
    raise ValueError(f"Cannot verify embedding permissions (no OS/2 table): {path}")


def load_font(name: str, path: Path) -> dict:
    flags = embedding_flags(path)
    # ReportLab subsets fonts; reject restricted, no-subset and bitmap-only fonts.
    if flags & (0x0002 | 0x0100 | 0x0200):
        raise ValueError(f"Font embedding/subsetting is restricted (fsType={flags}): {path}")
    pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
    return {"name": path.name, "embedding_fsType": flags}


def choose_font(explicit: str | None, candidates: list[str]) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for name in candidates:
        path = Path(name)
        if path.is_file():
            return path
    raise FileNotFoundError(
        "No suitable font found. Supply --font/--bold-font/--mono-font with "
        "legally embeddable TrueType-outline TTF/TTC fonts."
    )


def register_fonts(args: argparse.Namespace) -> list[dict]:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    regular = choose_font(args.font, [
        str(windows / "msyh.ttc"),
        str(windows / "simhei.ttf"),
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ])
    bold = choose_font(args.bold_font, [
        str(windows / "msyhbd.ttc"), str(regular),
    ])
    mono = choose_font(args.mono_font, [
        str(windows / "consola.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", str(regular),
    ])
    details = [load_font(FONT, regular), load_font(BOLD, bold), load_font(MONO, mono)]
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD, italic=FONT, boldItalic=BOLD)
    return details


def inline(text: str, base_url: str) -> str:
    """Translate a deliberately small, escaped inline Markdown subset."""
    pattern = re.compile(r"(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|\*[^*]+\*)")
    output = []
    cursor = 0
    for match in pattern.finditer(text):
        output.append(html.escape(text[cursor:match.start()]))
        token = match.group(0)
        if token.startswith("`"):
            output.append(
                '<font color="#244f77" backColor="#eef3f8">'
                + html.escape(token[1:-1]) + "</font>"
            )
        elif token.startswith("["):
            link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)
            assert link is not None
            target = urljoin(base_url, link.group(2))
            # PDF documentation links only; unsupported schemes are rendered plain.
            if target.startswith(("https://", "http://", "mailto:")):
                output.append(
                    '<link href="' + html.escape(target, quote=True)
                    + '" color="#21659a">' + html.escape(link.group(1)) + "</link>"
                )
            else:
                output.append(html.escape(link.group(1)))
        elif token.startswith("**"):
            output.append("<b>" + html.escape(token[2:-2]) + "</b>")
        else:
            output.append("<i>" + html.escape(token[1:-1]) + "</i>")
        cursor = match.end()
    output.append(html.escape(text[cursor:]))
    return "".join(output)


def make_styles() -> dict[str, ParagraphStyle]:
    body = dict(
        fontName=FONT, fontSize=9.6, leading=16.0, textColor=INK,
        wordWrap="CJK", alignment=TA_LEFT, spaceAfter=6.0,
        allowWidows=0, allowOrphans=0, splitLongWords=1,
    )
    styles = {"body": ParagraphStyle("Body", **body)}
    for level, size, leading, before, after in (
        (1, 21, 30, 0, 13), (2, 14.2, 22, 14, 6),
        (3, 11.1, 18, 9, 4), (4, 10.3, 17, 8, 4),
    ):
        values = dict(body)
        values.update(
            fontName=BOLD, fontSize=size, leading=leading, textColor=NAVY,
            spaceBefore=before, spaceAfter=after, keepWithNext=True,
        )
        styles[f"h{level}"] = ParagraphStyle(f"Heading{level}", **values)
    styles["list"] = ParagraphStyle(
        "List", parent=styles["body"], leftIndent=17, firstLineIndent=-15,
        spaceAfter=4.5,
    )
    styles["table"] = ParagraphStyle(
        "Table", parent=styles["body"], fontSize=8.6, leading=14.2, spaceAfter=0,
    )
    styles["tablehead"] = ParagraphStyle(
        "TableHead", parent=styles["table"], fontName=BOLD, textColor=colors.white,
    )
    return styles


class CodeBlock(Flowable):
    """Literal commands with bounded, visible line wrapping and no truncation."""

    def __init__(self, source: str, language: str):
        super().__init__()
        self.source = source
        self.language = language or "text"
        self.lines: list[str] = []
        self.font_size = 8.4
        self.leading = 13.2
        self.spaceBefore = 3
        self.spaceAfter = 8

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        usable = avail_width - 24
        self.lines = []
        for source_line in self.source.split("\n"):
            remaining = source_line.replace("\t", "    ")
            while pdfmetrics.stringWidth(remaining, MONO, self.font_size) > usable:
                # Prefer whitespace, retaining every character. Overlong tokens
                # can wrap at a character boundary, as in a text editor.
                end = 1
                while end < len(remaining) and pdfmetrics.stringWidth(
                    remaining[:end + 1], MONO, self.font_size
                ) <= usable:
                    end += 1
                boundary = remaining.rfind(" ", 1, end + 1)
                if boundary > end // 3:
                    end = boundary
                self.lines.append(remaining[:end])
                remaining = remaining[end:]
            self.lines.append(remaining)
        self.height = 26 + len(self.lines) * self.leading
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        canvas.setFillColor(PALE)
        canvas.setStrokeColor(RULE)
        canvas.roundRect(0, 0, self.width, self.height, 4, fill=1, stroke=1)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 6.9)
        canvas.drawString(12, self.height - 13, self.language.upper())
        canvas.setFillColor(INK)
        canvas.setFont(MONO, self.font_size)
        y = self.height - 28
        for line in self.lines:
            canvas.drawString(12, y, line)
            y -= self.leading


def is_table_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", line))


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_flowables(source: str, styles: dict, width: float, base_url: str) -> list:
    lines = source.splitlines()
    story: list = []
    cursor = 0
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped:
            cursor += 1
            continue
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            cursor += 1
            code = []
            while cursor < len(lines) and not lines[cursor].strip().startswith("```"):
                code.append(lines[cursor])
                cursor += 1
            if cursor == len(lines):
                raise ValueError("Unclosed fenced code block in installation Markdown")
            story.append(CodeBlock("\n".join(code), language))
            cursor += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 4)
            story.append(Paragraph(inline(heading.group(2), base_url), styles[f"h{level}"]))
            cursor += 1
            continue
        if cursor + 1 < len(lines) and "|" in line and is_table_separator(lines[cursor + 1]):
            rows = [table_cells(line)]
            cursor += 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(table_cells(lines[cursor]))
                cursor += 1
            columns = len(rows[0])
            if any(len(row) != columns for row in rows):
                raise ValueError("Markdown table has inconsistent column counts")
            if columns == 3:
                proportions = [0.25, 0.39, 0.36]
            elif columns == 2:
                first = max(len(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", row[0])) for row in rows)
                proportions = [0.65, 0.35] if first > 25 else [0.21, 0.79]
            else:
                proportions = [1 / columns] * columns
            cells = [
                [Paragraph(inline(cell, base_url), styles["tablehead" if row_index == 0 else "table"])
                 for cell in row]
                for row_index, row in enumerate(rows)
            ]
            table = LongTable(cells, colWidths=[width * p for p in proportions], repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
            ]))
            table.spaceBefore = 4
            table.spaceAfter = 10
            story.append(table)
            continue
        listing = re.match(r"^\s*([-+*]|\d+\.)\s+(.+)$", line)
        if listing:
            bullet = listing.group(1)
            marker = "•" if bullet in "-+*" else bullet
            text = inline(marker + "  " + listing.group(2), base_url)
            story.append(Paragraph(text, styles["list"]))
            cursor += 1
            continue
        if re.fullmatch(r"(?:---+|\*\*\*+)\s*", stripped):
            story.extend([HRFlowable(width="100%", color=RULE, thickness=0.6), Spacer(1, 8)])
            cursor += 1
            continue
        paragraph = [stripped]
        cursor += 1
        while cursor < len(lines) and lines[cursor].strip():
            next_line = lines[cursor].strip()
            if next_line.startswith(("#", "```", "|")) or re.match(r"^([-+*]|\d+\.)\s", next_line):
                break
            paragraph.append(next_line)
            cursor += 1
        paragraph_text = " ".join(paragraph)
        rendered = Paragraph(inline(paragraph_text, base_url), styles["body"])
        # Keep instructions introducing a command/list with the material that
        # follows, so a page never ends with a detached "run this:" prompt.
        if paragraph_text.endswith((":", "：")):
            rendered.keepWithNext = True
        story.append(rendered)
    return story


class ManualTemplate(BaseDocTemplate):
    def __init__(self, path: Path, **kwargs):
        super().__init__(str(path), pagesize=A4, leftMargin=43, rightMargin=43,
                         topMargin=49, bottomMargin=45, **kwargs)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
        self.addPageTemplates(PageTemplate(id="Manual", frames=[frame], onPage=self.decoration))

    def decoration(self, canvas, doc):
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7.3)
        canvas.drawString(self.leftMargin, page_height - 27, "杭电通知集 / 安装与使用手册")
        canvas.drawRightString(page_width - self.rightMargin, page_height - 27, RELEASE_VERSION)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(self.leftMargin, page_height - 35, page_width - self.rightMargin, page_height - 35)
        canvas.line(self.leftMargin, 33, page_width - self.rightMargin, 33)
        canvas.setFont(FONT, 7)
        canvas.drawString(self.leftMargin, 21, "hdu-notice-hub · 本机运行 · 公开通知汇集")
        canvas.drawRightString(page_width - self.rightMargin, 21, f"{doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name.startswith("Heading"):
            level = int(flowable.style.name[-1])
            if level <= 3:
                title = flowable.getPlainText()
                key = f"section-{self.seq.nextf('section')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(title, key, level=level - 1, closed=False)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "docs" / "INSTALL.md")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "INSTALL.pdf")
    parser.add_argument("--font", help="Embeddable Chinese TTF or first TTC face")
    parser.add_argument("--bold-font", help="Embeddable Chinese bold TTF/TTC")
    parser.add_argument("--mono-font", help="Embeddable monospaced TTF/TTC")
    parser.add_argument("--base-url", default=f"https://github.com/xiangyu-wang08/hdu-notice-hub/blob/{RELEASE_VERSION}/docs/")
    args = parser.parse_args()
    font_details = register_fonts(args)
    source_bytes = args.input.read_bytes()
    source = source_bytes.decode("utf-8-sig")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Stable PDF IDs/timestamps allow identical rebuilds with the same runtime/fonts.
    rl_config.invariant = 1
    document = ManualTemplate(
        args.output, title=f"杭电通知集 {RELEASE_VERSION} 安装与使用手册",
        author="hdu-notice-hub contributors", subject="Windows 安装版、免安装版与 Python 源码版使用、AI 设置和排查",
        pageCompression=1,
    )
    story = markdown_flowables(source, make_styles(), document.width, args.base_url)
    document.build(story)
    print(json.dumps({
        "version": RELEASE_VERSION,
        "input": str(args.input.resolve()), "output": str(args.output.resolve()),
        "markdown_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "pdf_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "fonts": font_details,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
