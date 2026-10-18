"""Portable Windows entry point; no installed Python or PowerShell required."""
import argparse
import errno
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

from server import Handler, ROOT, Store, ThreadingHTTPServer
from version import APP_VERSION

RUNTIME_FILE = ROOT / 'data' / 'portable-runtime.json'
LOCAL_HTTP = build_opener(ProxyHandler({}))


class InstanceLock:
    """OS-owned per-directory lock, released even if a process is killed."""
    def __init__(self):
        RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.file = (RUNTIME_FILE.parent / 'launcher.lock').open('a+b')
        if self.file.seek(0, os.SEEK_END) == 0:
            self.file.write(b'\0')
            self.file.flush()

    def __enter__(self):
        return self

    def acquire(self):
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

    def __exit__(self, *args):
        self.file.close()


def local_json(port, path, token=None):
    headers = {}
    payload = None
    if token:
        headers = {'Content-Type': 'application/json', 'X-Local-Token': token}
        payload = b'{}'
    request = Request(f'http://127.0.0.1:{port}{path}', data=payload, headers=headers)
    with LOCAL_HTTP.open(request, timeout=3) as response:
        return json.load(response)


def running_instance():
    try:
        record = json.loads(RUNTIME_FILE.read_text(encoding='utf-8'))
        if not isinstance(record['port'], int) or not 8765 <= record['port'] <= 8775:
            return None
        state = local_json(record['port'], '/api/state')
        if state.get('app') == 'hdu-notice-local' and state.get('instance') == record['instance']:
            return record
    except (OSError, ValueError, KeyError, TypeError, URLError):
        pass
    return None


class PortableHandler(Handler):
    def log_message(self, fmt, *args):
        # Keep the user's control window readable; failures are shown on the webpage.
        pass

    def do_GET(self):
        if self.path == '/api/state' and self.local_request():
            return self.respond({**self.store.snapshot(), 'instance': self.server.instance})
        return super().do_GET()

    def do_POST(self):
        if self.path == '/api/shutdown':
            token = self.headers.get('X-Local-Token', '')
            if not self.local_request() or not secrets.compare_digest(token, self.server.shutdown_token):
                return self.respond({'error': '缺少本机关闭凭据'}, 403)
            self.respond({'status': 'stopping'})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return super().do_POST()


def stop():
    record = running_instance()
    if not record:
        print('服务未运行，无需关闭。', flush=True)
        return 0
    local_json(record['port'], '/api/shutdown', record['token'])
    for _ in range(40):
        if running_instance() is None:
            print('服务已关闭，可以关闭网页。', flush=True)
            return 0
        time.sleep(.15)
    raise RuntimeError('服务仍在关闭中，请稍后重试，或关闭启动时的服务窗口。')


def launch(open_browser=True):
    with InstanceLock() as lock:
        deadline = time.monotonic() + 10
        while not lock.acquire():
            existing = running_instance()
            if existing:
                url = f"http://127.0.0.1:{existing['port']}/"
                print('服务已经运行：' + url, flush=True)
                if open_browser:
                    webbrowser.open(url)
                return 0
            if time.monotonic() >= deadline:
                raise RuntimeError('服务正在启动或关闭，请稍后重试。')
            time.sleep(.1)
        return launch_locked(open_browser)


def launch_locked(open_browser=True):
    existing = running_instance()
    if existing:
        url = f"http://127.0.0.1:{existing['port']}/"
        print('服务已经运行：' + url, flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0
    RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    PortableHandler.store = Store()
    httpd = None
    for port in range(8765, 8776):
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), PortableHandler)
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError('8765 至 8775 端口均不可用，请关闭其他实例后重试。')
    httpd.instance = secrets.token_hex(16)
    httpd.shutdown_token = secrets.token_hex(32)
    record = {'port': httpd.server_port, 'instance': httpd.instance, 'token': httpd.shutdown_token}
    try:
        temp = RUNTIME_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(record), encoding='utf-8')
        temp.replace(RUNTIME_FILE)
        url = f'http://127.0.0.1:{httpd.server_port}/'
        print(f'\n杭电通知集 {APP_VERSION}\n', flush=True)
        print('网页地址：' + url, flush=True)
        print('请保持此窗口运行，可将窗口最小化。', flush=True)
        print('结束使用：双击“关闭服务.cmd”，或在此窗口按 Ctrl+C。', flush=True)
        print('直接关闭此窗口也会停止服务；只关闭网页不会停止服务。\n', flush=True)
        if open_browser:
            threading.Timer(.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass
    finally:
        httpd.server_close()
        try:
            saved = json.loads(RUNTIME_FILE.read_text(encoding='utf-8'))
            if saved.get('instance') == httpd.instance:
                RUNTIME_FILE.unlink()
        except (OSError, ValueError):
            pass
    print('服务已停止。', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description='杭电通知集免安装启动器')
    parser.add_argument('--version', action='version', version=APP_VERSION)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        return stop() if args.stop else launch(not args.no_browser)
    except Exception as exc:
        print(f'无法启动或关闭服务：{exc}', file=sys.stderr, flush=True)
        print('请完整解压到有写入权限的文件夹，并保留 _internal 文件夹。', file=sys.stderr, flush=True)
        if sys.stdin and sys.stdin.isatty() and not args.no_browser:
            input('按回车键关闭窗口……')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
