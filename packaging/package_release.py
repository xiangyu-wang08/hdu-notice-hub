"""Build clean, self-contained release assets from this checkout only."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = (ROOT / 'VERSION').read_text(encoding='utf-8').strip() if (ROOT / 'VERSION').exists() else 'V1.0.0'
OUT = ROOT / 'release' / TAG
BUNDLE = OUT / '免安装程序'
QA_ROOT = ROOT / 'qa'
SOURCE_FILES = ('VERSION', 'version.py', 'server.py', 'sources.py', 'portable_launcher.py',
                'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'CHANGELOG.md',
                'start.cmd', 'start.ps1', 'stop.cmd', 'stop.ps1', '.gitignore')
SOURCE_DIRS = ('web', 'tests', 'packaging', 'docs', '.github')
EXCLUDED_PARTS = {'__pycache__', 'data', 'logs', 'qa', 'build', 'dist', 'release', '.git', '.superpowers'}


def safe_source(file, root):
    parts = file.relative_to(root).parts
    return (not any(part in EXCLUDED_PARTS or part.startswith(('.venv', '.env')) for part in parts)
            and file.suffix.lower() not in {'.pyc', '.log', '.pid', '.tmp'})


def zip_files(path, files, root, prefix):
    temporary = path.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(files):
                archive.write(file, prefix + '/' + file.relative_to(root).as_posix())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export_release(root=ROOT):
    root = Path(root).resolve()
    tag = (root / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'V\d+\.\d+\.\d+', tag):
        raise ValueError('VERSION must contain a tag such as V1.0.0')
    out = root / 'release' / tag
    bundle = out / '免安装程序'
    dist = root / 'dist' / 'HDUNotice'
    for directory in (dist, bundle):
        if directory.is_symlink():
            raise RuntimeError('Refusing a symlinked release directory')
        for file in directory.rglob('*') if directory.exists() else ():
            if file.is_symlink():
                raise RuntimeError('Refusing a symlink in release files')
            if not safe_source(file, directory):
                raise RuntimeError('Build or release contains runtime data; use a clean directory (运行数据).')
    sources = [root / name for name in SOURCE_FILES]
    for directory in SOURCE_DIRS:
        sources.extend(file for file in (root / directory).rglob('*') if file.is_file() and safe_source(file, root))
    sources.extend(root / name for name in ('docs/INSTALL.md', 'docs/INSTALL.pdf') if root / name not in sources)
    for file in sources:
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise RuntimeError('Source files must stay inside this checkout')
        if not file.is_file():
            raise FileNotFoundError(f'Required release file missing: {file.relative_to(root)}')
    if not (dist / 'HDUNotice.exe').is_file():
        raise FileNotFoundError('Build dist/HDUNotice/HDUNotice.exe first with packaging/build.ps1')
    out.mkdir(parents=True, exist_ok=True)
    if bundle.exists():
        if not bundle.resolve().is_relative_to(out.resolve()) or bundle.resolve() == out.resolve():
            raise RuntimeError('Unsafe output path')
        shutil.rmtree(bundle)
    shutil.copytree(dist, bundle)
    (bundle / 'HDUNotice.exe').replace(bundle / '杭电通知集.exe')
    shutil.copytree(root / 'packaging' / 'licenses', bundle / 'licenses', dirs_exist_ok=True)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'VERSION'):
        shutil.copy2(root / name, bundle / name)
    shutil.copy2(root / 'packaging' / '关闭服务.cmd', bundle)
    shutil.copy2(root / 'packaging' / '免安装版使用说明.txt', bundle / '先读我_使用说明.txt')
    shutil.copy2(root / 'docs' / 'INSTALL.pdf', bundle / '本地安装部署说明书.pdf')
    binary_name = f'hdu-notice-hub-{tag}-windows-x64'
    source_name = f'hdu-notice-hub-{tag}-source'
    zip_files(out / (binary_name + '.zip'), [file for file in bundle.rglob('*') if file.is_file()], bundle, binary_name)
    zip_files(out / (source_name + '.zip'), sources, root, source_name)
    manual = out / f'hdu-notice-hub-{tag}-install.pdf'
    shutil.copy2(root / 'docs' / 'INSTALL.pdf', manual)
    assets = (out / (binary_name + '.zip'), out / (source_name + '.zip'), manual)
    manifest = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in assets}
    (out / 'SHA256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Release assets:', out)
    return out


if __name__ == '__main__':
    export_release()
