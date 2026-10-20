"""A version query must succeed without starting a service."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LauncherTests(unittest.TestCase):
    def test_version_query_exits_without_creating_runtime_files(self):
        launcher = Path(__file__).resolve().parents[1] / 'portable_launcher.py'
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-B', str(launcher), '--version'], cwd=folder,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'V1.0.1')
            self.assertEqual(list(Path(folder).iterdir()), [])
