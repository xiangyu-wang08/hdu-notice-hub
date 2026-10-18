"""Actual socket and process regression checks for isolated launchers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from server import Handler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class InstanceTests(unittest.TestCase):
    def test_live_server_port_cannot_be_claimed_by_second_server(self):
        first = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        second = None
        try:
            try:
                second = ThreadingHTTPServer(first.server_address, Handler)
            except OSError:
                return
            self.fail('A second live HTTP server silently bound the occupied port')
        finally:
            if second:
                second.server_close()
            first.server_close()

    def test_concurrent_launches_leave_one_server_and_stop_all_own_processes(self):
        with tempfile.TemporaryDirectory(prefix='并发 launch ') as folder:
            target = Path(folder)
            for name in ('server.py', 'sources.py', 'portable_launcher.py', 'version.py'):
                shutil.copy2(ROOT / name, target / name)
            shutil.copytree(ROOT / 'web', target / 'web')
            command = [sys.executable, '-B', str(target / 'portable_launcher.py')]
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            processes = []
            logs = []
            try:
                for i in range(4):
                    log = (target / f'process-{i}.log').open('wb')
                    logs.append(log)
                    processes.append(subprocess.Popen(command + ['--no-browser'], cwd=target, env=env,
                                                     stdout=log, stderr=log, creationflags=FLAGS))
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    alive = [p for p in processes if p.poll() is None]
                    if len(alive) == 1 and all(p.returncode in (None, 0) for p in processes):
                        break
                    time.sleep(.1)
                self.assertEqual(sum(p.poll() is None for p in processes), 1, 'Concurrent launches created multiple live services')
                self.assertTrue(all(p.returncode in (None, 0) for p in processes), 'A duplicate launcher failed rather than reusing the service')
                runtime = target / 'data/portable-runtime.json'
                self.assertTrue(runtime.is_file())
                self.assertIn('instance', json.loads(runtime.read_text(encoding='utf-8')))
                stopped = subprocess.run(command + ['--stop'], cwd=target, env=env, capture_output=True, timeout=15, creationflags=FLAGS)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                for process in processes:
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                self.assertFalse(runtime.exists())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)
                for log in logs:
                    log.close()
