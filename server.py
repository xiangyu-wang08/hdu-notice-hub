"""Local HDU notice reader. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import base64
import zlib
import concurrent.futures
import copy
import datetime as dt
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as BaseThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ASSET_ROOT = Path(__file__).resolve().parent
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ASSET_ROOT
CACHE_FILE = ROOT / 'data' / 'notices.json'
TZ = dt.timezone(dt.timedelta(hours=8))
MAX_PAGES = 5
MAX_WORKERS = 8
from sources import SOURCES


class ThreadingHTTPServer(BaseThreadingHTTPServer):
    """Keep an occupied Windows port exclusive to its owning server."""
    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ''.join(c if isinstance(c, str) else c.text() for c in self.children)

    def find(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                    yield child
                yield from child.find(tag, cls)


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def safe_url(base, href):
    value = urlsplit(urljoin(base, href))
    if value.scheme not in ('https', 'http') or not value.hostname or value.username or value.password:
        raise ValueError('原文链接格式不受支持')
    return urlunsplit((value.scheme, value.netloc, value.path, value.query, ''))


def decode_career(html):
    # The public page embeds its list as zlib/base64 text. Decode data only; never eval JavaScript.
    pattern = r'Base64.decode\(unzip\("([A-Za-z0-9+/=]+)"\)\.substr\((\d+)\)\)\.substr\((\d+)\)'
    match = re.search(pattern, html)
    if not match:
        return html
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(base64.b64decode(match[1], validate=True), 3_000_001)
        if len(raw) > 3_000_000 or not inflater.eof:
            raise ValueError('就业网列表超出读取大小限制')
        return base64.b64decode(raw[int(match[2]):], validate=True).decode('utf-8')[int(match[3]):]
    except (ValueError, zlib.error, UnicodeError) as exc:
        raise ValueError('就业网列表编码变化，无法读取') from exc


def parse_page(html, source, page_url):
    adapter = source.get('parser', 'webplus')
    if adapter == 'career':
        html = decode_career(html)
    tree = Document(html).root
    if adapter == 'school':
        tree = next(tree.find(cls='col_news_list'), Node())
    row_class = {'webplus': 'jzlb', 'school': 'news', 'career': 'newsList'}[adapter]
    rows = list(tree.find(cls=row_class))
    if not rows:
        # Only an explicit empty marker in the known list template counts as success.
        if adapter != 'career' and any(n.text().strip() == '0' for n in tree.find(tag='em', cls='all_count')):
            return [], None
        if adapter == 'career' and next(tree.find(cls='empty'), None) is not None and re.search('暂无|没有找到', tree.text()):
            return [], None
        raise ValueError('未找到通知列表，网站可能变更或暂时返回了验证页面')
    items = []
    for row in rows:
        title_class, date_class = {'webplus': ('btt3', 'fbsj4'), 'school': ('news_title', 'news_month'), 'career': ('span1', 'span2')}[adapter]
        title_node = next(row.find(cls=title_class), None)
        date_node = next(row.find(cls=date_class), None)
        anchor = next(title_node.find(tag='a'), None) if title_node else None
        if not anchor or not date_node:
            raise ValueError('通知条目缺少标题或日期，请核对官网结构')
        title = ' '.join(anchor.text().split())
        if adapter == 'school' and re.search(r'(?:\.{3}|…+)$', title):
            full_title = ' '.join((anchor.attrs.get('title') or '').split())
            prefix = re.sub(r'(?:\.{3}|…+)$', '', title).rstrip()
            if prefix and full_title.startswith(prefix) and len(full_title) > len(prefix) and not re.search(r'(?:\.{3}|…+)$', full_title):
                title = full_title
        date_text = date_node.text().strip()
        if adapter == 'school':
            day = next(row.find(cls='news_day'), None)
            if not day:
                raise ValueError('通知条目缺少日期')
            date_text = date_text.replace('.', '-') + '-' + day.text().strip().zfill(2)
        date = dt.date.fromisoformat(date_text).isoformat()
        if not title or not anchor.attrs.get('href'):
            raise ValueError('通知标题或原文地址为空')
        url = safe_url(page_url, anchor.attrs['href'])
        items.append({'id': source['id'] + ':' + url, 'source_id': source['id'], 'title': title, 'date': date, 'url': url})
    next_anchor = next(tree.find(tag='a', cls='next'), None)
    if adapter == 'career':
        next_node = next(tree.find(tag='li', cls='next'), None)
        next_anchor = next(next_node.find(tag='a'), None) if next_node and 'disabled' not in next_node.attrs.get('class', '').split() else None
    next_url = None
    if next_anchor and not next_anchor.attrs.get('href', '').startswith('javascript:'):
        candidate = safe_url(page_url, next_anchor.attrs.get('href', ''))
        expected = urlsplit(source['url'])
        parsed = urlsplit(candidate)
        valid_path = re.fullmatch(re.escape(expected.path.rsplit('/', 1)[0]) + r'/list\d*\.htm', parsed.path)
        if adapter == 'career':
            valid_path = re.fullmatch(r'/news/index/(?:do1/job\.hdu\.edu\.cn/domain/hdu/)?tag/tzgg/(?:keyword//)?page/[1-9]\d*', parsed.path)
            valid_path = valid_path or re.fullmatch(r'/news/index/page/[1-9]\d*/do1/job\.hdu\.edu\.cn/domain/hdu/tag/tzgg/keyword', parsed.path)
        if parsed.hostname != expected.hostname or parsed.port != expected.port or not valid_path or parsed.query:
            raise ValueError('翻页地址异常，已停止读取')
        next_url = candidate
        if adapter == 'career':
            # Use the page's public search form pagination; its legacy do1 link redirects to SSO.
            next_url = source['url'] + '?page=' + re.search(r'/page/([1-9]\d*)', parsed.path)[1]
    return items, next_url


def fetch_page(url, timeout=15):
    request = Request(url, headers={'User-Agent': 'HDU-Notice-Local/1.0 (personal notice reader)', 'Accept': 'text/html'})
    with urlopen(request, timeout=timeout) as response:
        if urlsplit(response.url).hostname != urlsplit(url).hostname:
            raise ValueError('官网跳转到其他域名，已停止读取')
        raw = response.read(3_000_001)
        if len(raw) > 3_000_000:
            raise ValueError('网页超出读取大小限制')
        return raw.decode('utf-8-sig')


def error_text(exc):
    if isinstance(exc, HTTPError):
        return f'官网返回 HTTP {exc.code}，请稍后重试'
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return '连接超时，请检查网络后重试'
    if isinstance(exc, URLError):
        return '无法连接官网，请检查网络或 DNS 后重试'
    if isinstance(exc, (ValueError, UnicodeError)):
        return str(exc)
    return '读取官网时发生异常，请稍后重试'


def collect_source(source, fetcher=fetch_page):
    items, pages, visited = {}, 0, set()
    url, started = source['url'], time.monotonic()
    error = None
    try:
        while url and pages < MAX_PAGES:
            if url in visited:
                raise ValueError('官网翻页出现循环，已停止读取')
            remaining = 60 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError()
            visited.add(url)
            rows, next_url = parse_page(fetcher(url, timeout=min(15, remaining)), source, url)
            for row in rows:
                items[row['id']] = row
            pages += 1
            url = next_url
    except Exception as exc:
        error = error_text(exc)
    return {'items': list(items.values()), 'pages': pages, 'limited': bool(url and pages >= MAX_PAGES), 'error': error}


class Store:
    @staticmethod
    def empty_record():
        return {'items': [], 'status': 'never', 'last_result': 'unknown', 'from_cache': False,
                'pages': 0, 'limited': False, 'last_success': None, 'last_complete_success': None,
                'last_attempt': None, 'error': None}

    def __init__(self, cache_file=CACHE_FILE):
        self.cache_file = cache_file
        self.lock = threading.RLock()
        self.refreshing = False
        self.last_started = 0.0
        self.warning = None
        self.records = {s['id']: self.empty_record() for s in SOURCES}
        try:
            saved = json.loads(cache_file.read_text(encoding='utf-8'))
            if saved['version'] not in (1, 2):
                raise ValueError('Unsupported cache')
            for source in SOURCES:
                record = saved['sources'].get(source['id'])
                if record:
                    if not isinstance(record['items'], list):
                        raise ValueError('Invalid cache')
                    for item in record['items']:
                        if item['source_id'] != source['id'] or not isinstance(item['title'], str):
                            raise ValueError('Invalid item')
                        dt.date.fromisoformat(item['date'])
                        safe_url(source['url'], item['url'])
                    restored = self.records[source['id']]
                    restored.update({k: v for k, v in record.items() if k in restored})
                    result = record.get('last_result', record.get('status'))
                    if result not in ('ok', 'partial', 'error'):
                        result = ('partial' if record['items'] else 'error') if record.get('error') else 'unknown'
                    restored.update(status=result, last_result=result, from_cache=True)
                    if record.get('status') == 'never' and not record['items'] and not record.get('last_attempt'):
                        restored.update(status='never', last_result='unknown', from_cache=False)
                    if saved['version'] == 1 and result == 'ok':
                        restored['last_complete_success'] = record.get('last_success')
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError):
            self.warning = '本地缓存无法读取，请点击一键获取重新读取官网。'
            self.records = {s['id']: self.empty_record() for s in SOURCES}

    def snapshot(self):
        with self.lock:
            sources, notices = [], []
            for source in SOURCES:
                record = copy.deepcopy(self.records[source['id']])
                notices.extend(record.pop('items'))
                dates = sorted(n['date'] for n in self.records[source['id']]['items'])
                record.update(latest_date=dates[-1] if dates else None, earliest_date=dates[0] if dates else None)
                sources.append({**source, **record, 'count': len(self.records[source['id']]['items'])})
            return {'app': 'hdu-notice-local', 'today': dt.datetime.now(TZ).date().isoformat(), 'refreshing': self.refreshing,
                    'max_pages': MAX_PAGES, 'warning': self.warning, 'sources': sources,
                    'notices': sorted(notices, key=lambda n: (n['date'], n['id']), reverse=True)}

    def start_refresh(self, source_ids=None):
        if source_ids is not None and (not isinstance(source_ids, list) or not source_ids or
                any(not isinstance(s, str) or s not in self.records for s in source_ids)):
            raise ValueError('来源列表无效')
        selected = list(dict.fromkeys(source_ids)) if source_ids is not None else list(self.records)
        with self.lock:
            if self.refreshing:
                return 'running'
            if self.last_started and time.monotonic() - self.last_started < 30:
                return 'cooldown'
            self.last_started = time.monotonic()
            self.refreshing = True
            for source_id in selected:
                record = self.records[source_id]
                record['status'] = 'loading'
                record['last_attempt'] = now()
            threading.Thread(target=self.refresh, args=(selected,), daemon=True).start()
            return 'started'

    def apply_result(self, source_id, result):
        with self.lock:
            record = self.records[source_id]
            completed_at = now()
            if result['items']:
                # Keep older successful data if a later page failed; mark the source partial.
                if result['error']:
                    merged = {n['id']: n for n in record['items']}
                    merged.update({n['id']: n for n in result['items']})
                    record['items'] = list(merged.values())
                else:
                    record['items'] = result['items']
                record['last_success'] = completed_at
            elif not result['error']:
                record['items'] = []
                record['last_success'] = completed_at
            if not result['error']:
                record['last_complete_success'] = completed_at
            status = ('partial' if result['items'] else 'error') if result['error'] else 'ok'
            record.update(status=status, last_result=status, from_cache=False, pages=result['pages'],
                          limited=result['limited'], error=result['error'])

    def refresh(self, source_ids=None):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                pending = {pool.submit(collect_source, s): s['id'] for s in SOURCES if source_ids is None or s['id'] in source_ids}
                for future in concurrent.futures.as_completed(pending):
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {'items': [], 'pages': 0, 'limited': False, 'error': error_text(exc)}
                    self.apply_result(pending[future], result)
            with self.lock:
                try:
                    self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.cache_file.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'version': 2, 'sources': self.records}, ensure_ascii=False, indent=2), encoding='utf-8')
                    temporary.replace(self.cache_file)
                    self.warning = None
                except OSError:
                    self.warning = '本次结果已显示，但无法保存本地缓存；重启后可能丢失。'
        finally:
            with self.lock:
                self.refreshing = False


class Handler(BaseHTTPRequestHandler):
    store: Store
    _ai_setup_lock = threading.Lock()

    def get_ai_service(self):
        # One service per HTTP server; tests and previews can inject isolated services.
        with self._ai_setup_lock:
            if not hasattr(self.server, 'ai_service'):
                from ai_service import AIService
                self.server.ai_service = AIService(self.store, ROOT / 'data' / 'ai-settings.json')
        return self.server.ai_service

    def ai_response(self, operation, code=200):
        try:
            return self.respond(operation(self.get_ai_service()), code)
        except Exception as exc:
            from ai_service import AIError
            if isinstance(exc, AIError):
                return self.respond({'error': str(exc)}, exc.status)
            # Never include raw upstream exceptions, credentials or request bodies.
            return self.respond({'error': 'AI 服务暂时不可用，请检查设置后重试。'}, 503)

    def respond(self, payload, code=200, mime='application/json; charset=utf-8'):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if not isinstance(payload, bytes) else payload
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local_request(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.local_request():
            return self.respond({'error': '仅限本机访问'}, 403)
        path = urlsplit(self.path).path
        if path == '/api/state':
            return self.respond(self.store.snapshot())
        if path == '/api/ai/settings':
            return self.ai_response(lambda ai: ai.get_settings())
        if path.startswith('/api/ai/tasks/'):
            task_id = path.removeprefix('/api/ai/tasks/')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
                return self.respond({'error': '未找到 AI 任务'}, 404)
            return self.ai_response(lambda ai: ai.get_task(task_id))
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
        if path not in files:
            return self.respond({'error': '未找到页面'}, 404)
        filename, mime = files[path]
        self.respond((ASSET_ROOT / 'web' / filename).read_bytes(), mime=mime + '; charset=utf-8')

    def do_POST(self):
        expected = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.local_request() or self.headers.get('Origin') not in expected or self.headers.get('Content-Type') != 'application/json':
            return self.respond({'error': '请从本机网页操作'}, 403)
        ai_routes = {'/api/ai/settings', '/api/ai/test', '/api/ai/tasks'}
        if self.path != '/api/refresh' and self.path not in ai_routes:
            return self.respond({'error': '未找到接口'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= (65536 if self.path in ai_routes else 1024):
                raise ValueError()
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError()
            if self.path == '/api/ai/settings':
                return self.ai_response(lambda ai: ai.save_settings(payload))
            if self.path == '/api/ai/test':
                return self.ai_response(lambda ai: ai.test_connection(payload))
            if self.path == '/api/ai/tasks':
                return self.ai_response(lambda ai: ai.create_task(payload.get('notice_ids'), payload.get('question', '')), 202)
            status = self.store.start_refresh(payload.get('source_ids'))
        except (ValueError, UnicodeError):
            return self.respond({'error': '请求格式错误'}, 400)
        self.respond({'status': status}, 429 if status == 'cooldown' else 202)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    Handler.store = Store()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'HDU Notice: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
