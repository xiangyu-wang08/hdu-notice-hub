"""Offline integration checks in a disposable Chinese/space path; no site requests."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Store
from version import APP_VERSION

HTTP = build_opener(ProxyHandler({}))
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def request(port, path, payload=None, headers=None):
    with HTTP.open(Request(f'http://127.0.0.1:{port}{path}', data=payload, headers=headers or {}), timeout=3) as response:
        return response.read()


def check(bundle=None, scripts=False, source_root=ROOT, concurrent=False):
    qa = ROOT / 'qa'
    qa.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='中文 空格 smoke ', dir=qa) as folder:
        target = Path(folder)
        if bundle:
            shutil.copytree(bundle, target, dirs_exist_ok=True)
            command = [str(target / '杭电通知集.exe')]
        else:
            for name in ('server.py', 'sources.py', 'portable_launcher.py', 'version.py', 'start.cmd', 'start.ps1', 'stop.cmd', 'stop.ps1'):
                shutil.copy2(source_root / name, target / name)
            shutil.copytree(source_root / 'web', target / 'web')
            command = [sys.executable, '-B', str(target / 'portable_launcher.py')]
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        if bundle:
            env = {k: v for k, v in env.items() if not k.upper().startswith('PYTHON')}
            env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        runtime = target / 'data/portable-runtime.json'
        assert not runtime.exists()
        version = subprocess.run(command + ['--version'], cwd=target, env=env, capture_output=True, timeout=10, creationflags=FLAGS)
        assert version.returncode == 0 and version.stdout.strip() == APP_VERSION.encode(), version.stderr
        assert not runtime.exists()
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        launch_command = ([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target / 'start.ps1'), '-NoBrowser']
                          if scripts else command + ['--no-browser'])
        stop_command = ([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target / 'stop.ps1')]
                        if scripts else command + ['--stop'])
        occupied = socket.socket()
        try:
            occupied.bind(('127.0.0.1', 8765))
            occupied.listen()
        except OSError:
            occupied.close()
            occupied = None  # Another app already exercises fallback; never stop it.
        proc = None
        log = (target / 'smoke-process.log').open('wb')

        def launch():
            group = [subprocess.Popen(launch_command, cwd=target, env=env, stdout=log, stderr=log, creationflags=FLAGS)
                     for _ in range(4 if concurrent else 1)]
            for _ in range(100):
                alive = [process for process in group if process.poll() is None]
                if not alive or any(process.returncode not in (None, 0) for process in group):
                    for process in alive:
                        process.kill()
                        process.wait(timeout=10)
                    raise AssertionError(f'Launch exited unexpectedly: {(target / "smoke-process.log").read_bytes()!r}')
                try:
                    record = json.loads(runtime.read_text(encoding='utf-8'))
                    state = json.loads(request(record['port'], '/api/state'))
                    if state.get('instance') == record['instance'] and len(alive) == 1:
                        return alive[0], record, state
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(.1)
            for process in group:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            raise AssertionError(f'Runtime file/server readiness timeout: {(target / "smoke-process.log").read_bytes()!r}')

        def stop(process):
            result = subprocess.run(stop_command, cwd=target, env=env, capture_output=True, timeout=15, creationflags=FLAGS)
            assert result.returncode == 0, result.stderr
            process.wait(timeout=15)
            assert not runtime.exists(), 'Runtime record was not removed'

        try:
            proc, record, state = launch()
            port = record['port']
            assert port != 8765
            assert len(state['sources']) == 25 and state['notices'] == []
            for path in ('/', '/app.js', '/style.css', '/favicon.svg'):
                assert request(port, path)
            duplicate = subprocess.run(launch_command, cwd=target, env=env, capture_output=True, timeout=10, creationflags=FLAGS)
            assert duplicate.returncode == 0, duplicate.stderr
            assert json.loads(request(port, '/api/state'))['instance'] == record['instance']
            for path, payload, headers, status in (
                ('/api/shutdown', b'{}', {'X-Local-Token': 'wrong'}, 403),
                ('/api/refresh', b'{}', {'Content-Type': 'application/json', 'Origin': 'https://example.com'}, 403),
                ('/api/refresh', b'{"source_ids":["missing"]}', {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'}, 400),
            ):
                try:
                    request(port, path, payload, headers)
                    raise AssertionError(f'{path} accepted invalid input')
                except HTTPError as error:
                    assert error.code == status
            stop(proc)
            proc = None
            # Synthetic data is kept inside this disposable QA copy, never distributed.
            cache = target / 'data/notices.json'
            store = Store(cache)
            store.apply_result('jwc', dict(items=[dict(id='qa-offline-fixture', source_id='jwc', source_name='教务处', title='离线验收样例', date='2026-10-18', url='https://jwc.hdu.edu.cn/qa.htm')], pages=1, limited=False, error=None))
            cache.write_text(json.dumps({'version': 2, 'sources': store.records}, ensure_ascii=False), encoding='utf-8')
            proc, record, state = launch()
            assert len(state['notices']) == 1
            assert next(s for s in state['sources'] if s['id'] == 'jwc')['from_cache']
            stop(proc)
            proc = None
            if bundle:
                # Exercise the recipient's actual CMD close entry too.
                proc, _, _ = launch()
                result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(target / '关闭服务.cmd')], cwd=target, env=env, capture_output=True, timeout=15, creationflags=FLAGS)
                assert result.returncode == 0, result.stderr
                proc.wait(timeout=15)
                assert not runtime.exists()
                proc = None
            print(json.dumps(dict(mode='scripts' if scripts else ('portable' if bundle else 'source'), version=APP_VERSION, pass_=True, concurrent_launches=4 if concurrent else 1,
                                  checks=['version', 'clean-first-run', '25-sources', 'assets', 'port-fallback', 'repeat-launch', 'origin-and-shutdown-token', 'invalid-selection', 'cache-restart', 'graceful-stop']), ensure_ascii=False))
        finally:
            if proc and proc.poll() is None:
                subprocess.run(stop_command, cwd=target, env=env, capture_output=True, timeout=15, creationflags=FLAGS)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if occupied:
                occupied.close()
            log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='store_true', help='test source without external network')
    parser.add_argument('--source-dir', type=Path, default=ROOT, help='source checkout or extracted source ZIP')
    parser.add_argument('--scripts', action='store_true', help='test Windows PowerShell start/stop entries')
    parser.add_argument('--bundle', type=Path, help='directory containing 杭电通知集.exe')
    parser.add_argument('--concurrent', action='store_true', help='start four simultaneous copies and require one owner')
    args = parser.parse_args()
    check(args.bundle, args.scripts, args.source_dir, args.concurrent)
