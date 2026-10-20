"""Read public notice HTML without attachments or browser credentials."""
from __future__ import annotations

import base64
from html.parser import HTMLParser
import http.client
import ipaddress
import queue
import re
import socket
import threading
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from sources import SOURCES


MAX_BYTES = 3_000_000
MAX_NODES = 50_000
MAX_DEPTH = 256
# These exact school hosts use split-horizon campus DNS on some networks. Their
# public notice URLs must continue to work through the user's system route.
TRUSTED_HDU_HOSTS = frozenset(
    urlsplit(source[key]).hostname
    for source in SOURCES for key in ('url', 'home')
    if source.get(key) and urlsplit(source[key]).hostname.endswith('.hdu.edu.cn')
) | {'hdu.edu.cn'}
BODY_NAMES = (
    'wp_articlecontent', 'js_content', 'v_news_content', 'article-content',
    'article_content', 'articlecontent', 'arti_content', 'newscontent',
    'news-content', 'news_content', 'content-detail', 'content_detail',
    'articlebody', 'article-body', 'zoom', 'acontent',
)
SKIP_TAGS = {'script', 'style', 'noscript', 'template', 'nav', 'header', 'footer',
             'aside', 'iframe', 'object', 'embed', 'svg', 'canvas', 'form', 'button',
             'input', 'select', 'textarea', 'h1'}
SKIP_NAMES = {'breadcrumb', 'breadcrumbs', 'navigation', 'article_info',
              'article-info', 'article_meta', 'article-meta', 'publish_time',
              'publish-time', 'pubtime', 'arti_metas', 'arti_title', 'article-title',
              'attachment', 'attachments', 'share', 'shares', 'comment', 'comments',
              'rich_media_tool', 'footer', 'header', 'nav', 'prev-next', 'pagination'}
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'main', 'h2', 'h3', 'h4', 'h5',
              'h6', 'ul', 'ol', 'li', 'table', 'tr', 'blockquote', 'pre', 'dl', 'dt', 'dd'}


class _Node:
    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = {name: value or '' for name, value in attrs}
        self.children = []

    def walk(self, check=lambda: None):
        check()
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk(check)

    def plain(self, check=lambda: None):
        check()
        return ''.join(child if isinstance(child, str) else child.plain(check) for child in self.children)

    def names(self):
        return {self.attrs.get('id', '').lower(), *self.attrs.get('class', '').lower().split()}


class _Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html, check=lambda: None):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.check = check
        self.nodes = 0
        self.check()
        self.feed(html)
        self.close()
        self.check()

    def handle_starttag(self, tag, attrs):
        self.check()
        self.nodes += 1
        if self.nodes > MAX_NODES or len(self.stack) > MAX_DEPTH:
            raise ContentError('原文HTML结构过于复杂，已停止解析；请打开官网原文查看')
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.check()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.check()
        self.stack[-1].children.append(data)


class ContentError(ValueError):
    """A safe, user-facing reason that notice HTML could not be read."""


def extract_content(html, url=None, *, deadline=None):
    """Return article text only; missing/images/attachments are explicit failures."""
    if not isinstance(html, str) or not html.strip():
        raise ContentError('网页没有可读取的HTML正文')
    check = deadline.remaining if deadline is not None else lambda: None
    try:
        check()
        if url and urlsplit(url).hostname == 'job.hdu.edu.cn':
            html = _career_html(html)
        root = _Document(html, check).root
        nodes = list(root.walk(check))
        page_titles = ' '.join(n.plain(check) for n in nodes if n.tag in {'title', 'h1'})
        if re.search(r'统一身份认证|用户登录|安全验证|访问验证|验证码|环境异常|access denied|just a moment', page_titles, re.I):
            raise ContentError('原文需要登录或通过访问验证，无法读取公开正文')
        if any(n.tag == 'input' and n.attrs.get('type', '').lower() == 'password' for n in nodes):
            raise ContentError('原文需要登录，无法读取公开正文')
        candidates = []
        for name in BODY_NAMES:
            check()
            candidates.extend(n for n in nodes if name in n.names())
        candidates.extend(n for n in nodes if n.tag == 'article')
        if url and urlsplit(url).hostname == 'job.hdu.edu.cn':
            candidates.extend(n for n in nodes if {'content', 'news-detail'} & n.names())
        for node in candidates:
            text = _render(node, content_root=True, check=check)
            text = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '').replace('\ufeff', '')
            lines = [re.sub(r'[ \f\v]+', ' ', line).strip(' \t\r') for line in text.splitlines()]
            text = '\n'.join(line for line in lines if line)
            check()
            if len(re.sub(r'[\W_]+', '', text)) >= 20:
                return text
        raise ContentError('未找到可读取的HTML正文，页面可能只有图片、附件或暂未开放')
    except ContentError:
        raise
    except (ValueError, RecursionError, TypeError):
        raise ContentError('原文页面结构无法读取，请打开官网原文查看') from None


def _career_html(html):
    # Employment pages embed their public article HTML using the same data-only
    # base64/zlib envelope as their public lists. Never execute the script.
    match = re.search(r'Base64\.decode\(unzip\("([A-Za-z0-9+/=]+)"\)\.substr\((\d+)\)\)\.substr\((\d+)\)', html)
    if not match:
        return html
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(base64.b64decode(match[1], validate=True), MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not inflater.eof:
            raise ContentError('就业网正文超过读取大小限制')
        fragment = base64.b64decode(raw[int(match[2]):], validate=True).decode('utf-8')[int(match[3]):]
        return '<div class="aContent">' + fragment + '</div>'
    except ContentError:
        raise
    except (ValueError, zlib.error, UnicodeError):
        raise ContentError('就业网正文编码发生变化，暂时无法读取') from None


def _render(node, content_root=False, check=lambda: None):
    check()
    names = node.names()
    if node.tag in SKIP_TAGS or names & SKIP_NAMES:
        return ''
    wechat_root = content_root and node.attrs.get('id') == 'js_content'
    hidden = ('hidden' in node.attrs or node.attrs.get('aria-hidden') == 'true' or
              re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', node.attrs.get('style', ''), re.I))
    if hidden and not wechat_root:
        return ''
    if node.tag == 'a' and re.search(r'\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z)(?:$|[?#])', unquote(node.attrs.get('href', '')), re.I):
        return ''
    if node.tag in {'br', 'hr'}:
        return '\n'
    text = ''.join(child if isinstance(child, str) else _render(child, check=check) for child in node.children)
    if node.tag in {'td', 'th'}:
        return text.strip() + '\t'
    if node.tag in BLOCK_TAGS:
        return '\n' + text + '\n'
    return text


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ContentError('读取原文超时，请稍后重试')
    return remaining


class _Deadline:
    """Abort sockets even while http.client is still parsing slow headers."""
    def __init__(self, timeout):
        self.at = time.monotonic() + timeout
        self.expired = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []
        self.timer = threading.Timer(timeout, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def remaining(self):
        if self.expired.is_set():
            raise ContentError('读取原文超时，请稍后重试')
        return _remaining(self.at)

    @staticmethod
    def _close(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _expire(self):
        with self.lock:
            self.expired.set()
            sockets = list(self.sockets)
        for sock in sockets:
            self._close(sock)

    def watch(self, sock):
        with self.lock:
            expired = self.expired.is_set() or time.monotonic() >= self.at
            if not expired:
                self.sockets.append(sock)
        if expired:
            self._close(sock)
            raise ContentError('读取原文超时，请稍后重试')

    def close(self):
        self.timer.cancel()
        with self.lock:
            sockets, self.sockets = self.sockets, []
        for sock in sockets:
            self._close(sock)


def _resolve(host, port, deadline):
    # OS DNS resolution has no Python timeout. A late DNS answer must never
    # continue into a socket connection after the caller's total deadline.
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except Exception as exc:
            result.put((False, exc))
    worker = threading.Thread(target=resolve, name='notice-dns', daemon=True)
    worker.start()
    try:
        succeeded, value = result.get(timeout=deadline.remaining())
    except queue.Empty:
        raise ContentError('读取原文超时，请稍后重试') from None
    deadline.remaining()
    if not succeeded:
        raise value
    return value


def _dial(address, timeout, source_address, deadline, records=None):
    # External records are already validated. Connect their sockaddr directly;
    # socket.create_connection would resolve the original hostname a second time.
    records = records if records is not None else _resolve(*address, deadline)
    error = None
    for family, kind, protocol, _, sockaddr in records:
        deadline.remaining()
        sock = socket.socket(family, kind, protocol)
        try:
            deadline.watch(sock)
            sock.settimeout(deadline.remaining())
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
            deadline.remaining()
    if error:
        raise error
    raise OSError('No usable address')


class _DeadlineConnection:
    def __init__(self, *args, deadline, records=None, **kwargs):
        self.deadline = deadline
        super().__init__(*args, **kwargs)
        self._create_connection = lambda address, timeout, source_address=None: _dial(address, timeout, source_address, deadline, records)


class _HTTPConnection(_DeadlineConnection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_DeadlineConnection, http.client.HTTPSConnection):
    def connect(self):
        http.client.HTTPConnection.connect(self)
        server_hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname, do_handshake_on_connect=False)
        self.deadline.watch(self.sock)
        self.sock.settimeout(self.deadline.remaining())
        self.sock.do_handshake()


class _HTTPHandler(HTTPHandler):
    def __init__(self, deadline, records):
        super().__init__()
        self.deadline, self.records = deadline, records

    def http_open(self, request):
        return self.do_open(lambda *args, **kwargs: _HTTPConnection(*args, deadline=self.deadline, records=self.records, **kwargs), request)


class _HTTPSHandler(HTTPSHandler):
    def __init__(self, deadline, records):
        super().__init__()
        self.deadline, self.records = deadline, records

    def https_open(self, request):
        return self.do_open(lambda *args, **kwargs: _HTTPSConnection(*args, deadline=self.deadline, records=self.records, **kwargs), request, context=self._context)


def _validate_url(url, previous=None):
    try:
        if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f\\]', url):
            raise ValueError()
        parsed = urlsplit(url)
        host = (parsed.hostname or '').rstrip('.').lower()
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.port not in {None, 80, 443}:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        if host == 'localhost' or host.endswith(('.localhost', '.local')):
            raise ValueError()
        if previous:
            old = urlsplit(previous)
            allowed = host == old.hostname or (old.hostname == 'hdu.edu.cn' and host == 'www.hdu.edu.cn')
            if not allowed or (old.scheme == 'https' and parsed.scheme != 'https'):
                raise ContentError('原文跳转到其他网站或不安全地址，已停止读取')
            if (parsed.port or (443 if parsed.scheme == 'https' else 80)) != (old.port or (443 if old.scheme == 'https' else 80)):
                raise ContentError('原文跳转地址异常，已停止读取')
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', parsed.query, '')), host
    except ContentError:
        raise
    except (ValueError, TypeError):
        raise ContentError('原文链接格式不受支持或不是公开网页地址') from None


def _check_external_address(host, port, deadline):
    if host in TRUSTED_HDU_HOSTS:
        return None
    records = _resolve(host, port, deadline)
    public = [record for record in records if record[0] in {socket.AF_INET, socket.AF_INET6} and ipaddress.ip_address(record[4][0]).is_global]
    if not public:
        raise ContentError('原文地址不是可访问的公开网站，已停止读取')
    return public


class _NoRedirect(HTTPRedirectHandler):
    # Inspect each destination ourselves, before urllib makes the next request.
    def http_error_302(self, request, response, code, message, headers):
        return response

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _decode(raw, headers):
    if raw.startswith((b'%PDF-', b'PK\x03\x04')) or b'\x00' in raw[:8192]:
        raise ContentError('原文是附件或非HTML文档，本版只读取网页正文')
    declared = headers.get_content_charset()
    meta = re.search(br'<meta\b[^>]*charset\s*=\s*[\"\x27]?\s*([a-zA-Z0-9_-]+)', raw[:8192], re.I)
    encodings = [declared, meta[1].decode('ascii') if meta else None, 'utf-8-sig', 'gb18030']
    allowed = {'utf-8', 'utf8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312', 'big5', 'windows-1252', 'iso-8859-1'}
    for encoding in encodings:
        if not encoding or encoding.lower() not in allowed:
            continue
        try:
            return raw.decode(encoding)
        except UnicodeError:
            pass
    raise ContentError('原文网页编码无法读取，请打开官网原文查看')


def read_notice(notice, timeout=15):
    """Read one known notice URL, with no cookies, credentials, files or OCR."""
    deadline = None
    try:
        if not isinstance(notice, dict) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ContentError('原文读取参数无效')
        url, host = _validate_url(notice.get('url'))
        deadline = _Deadline(timeout)
        for attempt in range(4):
            deadline.remaining()
            parsed = urlsplit(url)
            records = _check_external_address(host, parsed.port or (443 if parsed.scheme == 'https' else 80), deadline)
            handlers = [_NoRedirect(), _HTTPHandler(deadline, records), _HTTPSHandler(deadline, records)]
            if records is not None:
                # A proxy would resolve external hosts itself, defeating address
                # pinning. Exact configured school hosts retain system proxies.
                handlers.append(ProxyHandler({}))
            opener = build_opener(*handlers)
            request = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity',
            })
            response = opener.open(request, timeout=deadline.remaining())
            try:
                status = response.status
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get('Location')
                    if attempt == 3 or not location:
                        raise ContentError('原文跳转过多或跳转地址缺失，无法读取')
                    url, host = _validate_url(urljoin(url, location), previous=url)
                    continue
                if status != 200:
                    raise ContentError('官网暂未提供可读取的公开正文，请打开原文查看')
                if response.headers.get_content_type() not in {'text/html', 'application/xhtml+xml'}:
                    raise ContentError('原文是附件或非HTML文档，本版只读取网页正文')
                if response.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                    raise ContentError('原文返回了暂不支持的压缩网页，请打开原文查看')
                length = response.headers.get('Content-Length', '')
                if length.isdigit() and int(length) > MAX_BYTES:
                    raise ContentError('原文网页超过3MB读取限制，请打开官网原文查看')
                raw = bytearray()
                while len(raw) <= MAX_BYTES:
                    remaining = deadline.remaining()
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise ContentError('原文网页超过3MB读取限制，请打开官网原文查看')
                deadline.remaining()
                return {'text': extract_content(_decode(bytes(raw), response.headers), url, deadline=deadline)}
            finally:
                response.close()
        raise ContentError('原文跳转过多，无法读取')
    except ContentError:
        raise
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ContentError('原文需要登录或暂时限制访问，无法读取公开正文') from None
        raise ContentError('官网暂时无法提供原文，请稍后重试') from None
    except (TimeoutError, socket.timeout):
        raise ContentError('读取原文超时，请稍后重试') from None
    except (OSError, URLError, http.client.HTTPException):
        if deadline is not None:
            deadline.remaining()
        raise ContentError('无法连接官网原文，请检查网络后重试') from None
    except (ValueError, TypeError, RecursionError):
        raise ContentError('原文页面暂时无法读取，请打开官网查看') from None
    finally:
        if deadline is not None:
            deadline.close()
