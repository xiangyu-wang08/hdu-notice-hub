"""Integration check of the built EXE, using a PATH without installed Python."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from package_release import BUNDLE, QA_ROOT

TEST_ROOT = QA_ROOT / ('免安装测试 ' + uuid.uuid4().hex[:8])
shutil.copytree(BUNDLE, TEST_ROOT)
EXE = TEST_ROOT / '杭电通知集.exe'
ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith('PYTHON')}
ENV['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
FLAGS = subprocess.CREATE_NO_WINDOW
HTTP = build_opener(ProxyHandler({}))


def get(path, port, payload=None, headers=None):
    req = Request(f'http://127.0.0.1:{port}{path}', data=payload, headers=headers or {})
    with HTTP.open(req, timeout=4) as response:
        return response.read()


def launch():
    proc = subprocess.Popen([str(EXE), '--no-browser'], cwd=TEST_ROOT, env=ENV, creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if proc.poll() is not None:
            raise AssertionError('Portable process exited early')
        try:
            record = json.loads((TEST_ROOT / 'data' / 'portable-runtime.json').read_text(encoding='utf-8'))
            state = json.loads(get('/api/state', record['port']))
            if state['instance'] == record['instance']:
                return proc, record, state
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.15)
    proc.kill()
    raise AssertionError('Portable launch timeout')


def stop(proc):
    result = subprocess.run([str(EXE), '--stop'], cwd=TEST_ROOT, env=ENV, creationflags=FLAGS, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    proc.wait(timeout=15)
    assert not (TEST_ROOT / 'data' / 'portable-runtime.json').exists()


def check():
    proc = None
    occupied = socket.socket()
    try:
        try:
            occupied.bind(('127.0.0.1', 8765))
            occupied.listen()
        except OSError:
            occupied.close()
            occupied = None  # An existing app already exercises fallback; do not stop it.
        proc, record, state = launch()
        port = record['port']
        assert port != 8765
        assert not state['notices']
        for asset in ['/', '/app.js', '/style.css', '/favicon.svg']:
            assert get(asset, port)
        print(json.dumps({'test': 'isolated_path_launch_and_assets', 'pass': True, 'port': port}), flush=True)
        powershell = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        modules = subprocess.check_output([str(powershell), '-NoProfile', '-Command', f"(Get-Process -Id {proc.pid}).Modules | Where-Object {{ $_.ModuleName -eq 'python312.dll' }} | Select-Object -ExpandProperty FileName"], creationflags=FLAGS).decode('utf-8', errors='replace').strip()
        # Console encoding differs by host; the DLL name and _internal are ASCII.
        assert '_internal' in modules and 'python312.dll' in modules
        print(json.dumps({'test': 'bundled_python_dll', 'pass': True}), flush=True)
        result = subprocess.run([str(EXE), '--no-browser'], cwd=TEST_ROOT, env=ENV, creationflags=FLAGS, capture_output=True, timeout=10)
        assert result.returncode == 0
        assert json.loads(get('/api/state', port))['instance'] == record['instance']
        try:
            get('/api/shutdown', port, b'{}', {'X-Local-Token': 'wrong'})
            raise AssertionError('Shutdown without token was accepted')
        except HTTPError as exc:
            assert exc.code == 403
        get('/api/refresh', port, b'{}', {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
        assert len(state['sources']) == 25
        deadline = time.monotonic() + 270
        while time.monotonic() < deadline:
            state = json.loads(get('/api/state', port))
            if not state['refreshing']:
                break
            time.sleep(.4)
        assert not state['refreshing']
        # Public sites can time out. Exercise the same bounded targeted retry offered by the UI.
        for attempt in range(2):
            failed = [s['id'] for s in state['sources'] if s['status'] in ('partial', 'error')]
            if not failed:
                break
            before = {s['id']: s['last_attempt'] for s in state['sources'] if s['id'] not in failed}
            print(json.dumps({'test': 'targeted_retry', 'attempt': attempt + 1, 'sources': failed}), flush=True)
            stop(proc); proc = None
            proc, record, cached = launch(); port = record['port']
            assert all(s['status'] in ('partial', 'error') for s in cached['sources'] if s['id'] in failed)
            assert all(s['from_cache'] for s in cached['sources'])
            get('/api/refresh', port, json.dumps({'source_ids':failed}).encode(), {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
            deadline = time.monotonic() + 270
            while time.monotonic() < deadline:
                state = json.loads(get('/api/state', port))
                if not state['refreshing']: break
                time.sleep(.4)
            assert not state['refreshing']
            assert all(s['last_attempt'] == before[s['id']] for s in state['sources'] if s['id'] not in failed)
        assert all(s['status'] == 'ok' for s in state['sources']), [(s['id'], s['error']) for s in state['sources'] if s['status'] != 'ok']
        assert all(s['count'] > 0 for s in state['sources'])
        count = len(state['notices'])
        print(json.dumps({'test': 'real_fetch_from_bundled_exe', 'sources': [{'id':s['id'], 'count':s['count'], 'status':s['status']} for s in state['sources']]}), flush=True)
        stop(proc)
        proc = None
        print(json.dumps({'test': 'authenticated_shutdown', 'pass': True}), flush=True)
        proc, record, cached = launch()
        assert len(cached['notices']) == count
        assert all(s['status'] == 'ok' and s['from_cache'] for s in cached['sources'])
        stop(proc); proc = None
        # Deterministic failure fixture in the QA copy only; never in a delivered package.
        cache_path = TEST_ROOT / 'data' / 'notices.json'
        fixture = json.loads(cache_path.read_text(encoding='utf-8'))
        fixture['sources']['library'].update(status='partial', last_result='partial', error='模拟分页超时（测试）')
        cache_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding='utf-8')
        proc, record, cached = launch(); port = record['port']
        restored = next(s for s in cached['sources'] if s['id'] == 'library')
        assert restored['status'] == 'partial' and restored['from_cache'] and restored['count'] > 0
        before = {s['id']: s['last_attempt'] for s in cached['sources'] if s['id'] != 'library'}
        get('/api/refresh', port, b'{"source_ids":["library"]}', {'Content-Type':'application/json', 'Origin':f'http://127.0.0.1:{port}'})
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            retried = json.loads(get('/api/state', port))
            if not retried['refreshing']: break
            time.sleep(.3)
        assert not retried['refreshing']
        assert all(s['last_attempt'] == before[s['id']] for s in retried['sources'] if s['id'] != 'library')
        assert next(s for s in retried['sources'] if s['id'] == 'library')['last_attempt']
        print(json.dumps({'test':'partial_restart_and_targeted_retry', 'pass':True}), flush=True)
        # Exercise the actual recipient-facing CMD, not only the EXE argument.
        cmd = str(Path(os.environ['SystemRoot']) / 'System32' / 'cmd.exe')
        result = subprocess.run([cmd, '/d', '/c', str(TEST_ROOT / '关闭服务.cmd')], cwd=TEST_ROOT, env=ENV, creationflags=FLAGS, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        proc.wait(timeout=15)
        proc = None
        assert not (TEST_ROOT / 'data' / 'portable-runtime.json').exists()
        print(json.dumps({'test': 'cache_restart_and_cmd_stop', 'pass': True, 'count':count}), flush=True)
    finally:
        if occupied:
            occupied.close()
        if proc and proc.poll() is None:
            try:
                stop(proc)
            except Exception:
                proc.kill()
                proc.wait()


if __name__ == '__main__':
    check()
