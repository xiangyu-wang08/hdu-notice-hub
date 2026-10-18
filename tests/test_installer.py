"""Installer payload and deletion boundaries, independent of an NSIS install."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'packaging/build_installer.py'


class InstallerTests(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), 'installer build tool is missing')
        spec = importlib.util.spec_from_file_location('installer_build', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_payload_manifest_excludes_runtime_data_and_deletes_only_owned_files(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            for name in ('杭电通知集.exe', '_internal/python312.dll', 'LICENSE'):
                file = bundle / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('fixture', encoding='utf-8')
            files, directories = module.payload_manifest(bundle)
            self.assertEqual(files, ['LICENSE', '_internal/python312.dll', '杭电通知集.exe'])
            self.assertEqual(directories, ['_internal'])
            install, uninstall = module.render_payload(files, directories)
            self.assertIn('File ', install)
            self.assertIn('Delete "$INSTDIR\\_internal\\python312.dll"', uninstall)
            self.assertNotIn('/r', uninstall)
            self.assertNotIn('data', uninstall)
            cache = bundle / 'data/portable-runtime.json'
            cache.parent.mkdir()
            cache.write_text('private', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'runtime'):
                module.payload_manifest(bundle)
