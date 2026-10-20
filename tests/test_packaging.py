"""Release contracts: standalone output, complete sources, no runtime state."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('release_package', Path(__file__).resolve().parents[1] / 'packaging' / 'package_release.py')
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class ReleaseTests(unittest.TestCase):
    def fixture(self, root):
        files = {
            'VERSION': 'V1.0.0\n', 'version.py': 'APP_VERSION = "V1.0.0"\n',
            'server.py': '# server', 'sources.py': '# sources', 'portable_launcher.py': '# launcher',
            'ai_service.py': '# offline AI service', 'notice_content.py': '# public notice reader',
            'README.md': '# Readme', 'LICENSE': 'MIT fixture', 'THIRD_PARTY_NOTICES.md': '# Notices',
            'CONTRIBUTING.md': '# Contributing', 'CHANGELOG.md': '# Changes', '.gitignore': 'data/',
            'start.cmd': '@echo off', 'start.ps1': '# start', 'stop.cmd': '@echo off', 'stop.ps1': '# stop',
            'web/index.html': '<h1>app</h1>', 'tests/test_server.py': '# test',
            'packaging/package_release.py': '# package', 'packaging/build.ps1': '# build',
            'packaging/licenses/Python-LICENSE.txt': 'PSF fixture',
            'packaging/关闭服务.cmd': '@echo off', 'packaging/免安装版使用说明.txt': 'instructions',
            'docs/INSTALL.md': '# Install', 'docs/INSTALL.pdf': '%PDF-fixture',
            'docs/images/desktop.png': 'PNG fixture', '.github/workflows/tests.yml': 'name: tests',
            'dist/HDUNotice/HDUNotice.exe': 'EXE fixture', 'dist/HDUNotice/_internal/python312.dll': 'DLL fixture',
            'data/portable-runtime.json': 'SECRET runtime', 'packaging/__pycache__/unsafe.pyc': 'SECRET pyc',
            'data/ai-settings.json': 'SECRET ai-settings', 'data/ai-key.dpapi': 'SECRET protected-key',
            'logs/server.log': 'SECRET runtime-log',
            'docs/.env': 'SECRET credential', 'docs/data/private.json': 'SECRET cache',
        }
        for name, content in files.items():
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding='utf-8')

    def test_standalone_archives_include_licenses_docs_and_ci_without_runtime_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'project with spaces'
            self.fixture(root)
            self.assertTrue(callable(getattr(package, 'export_release', None)), 'standalone export_release is missing')
            out = package.export_release(root)
            # Windows TEMP can use an 8.3 alias while export_release returns a resolved path.
            self.assertEqual(out, (root / 'release' / 'V1.0.0').resolve())
            with zipfile.ZipFile(out / 'hdu-notice-hub-V1.0.0-source.zip') as archive:
                names = {name.split('/', 1)[1] for name in archive.namelist()}
                for required in ('LICENSE', 'version.py', 'VERSION', 'docs/INSTALL.md', 'docs/INSTALL.pdf', 'docs/images/desktop.png', '.github/workflows/tests.yml'):
                    self.assertIn(required, names)
                self.assertFalse(any('data/' in name or '__pycache__' in name or '.env' in name for name in names))
            with zipfile.ZipFile(out / 'hdu-notice-hub-V1.0.0-windows-x64.zip') as archive:
                names = {name.split('/', 1)[1] for name in archive.namelist()}
                self.assertIn('杭电通知集.exe', names)
                self.assertIn('_internal/python312.dll', names)
                self.assertIn('LICENSE', names)
                self.assertIn('licenses/Python-LICENSE.txt', names)
                self.assertIn('本地安装部署说明书.pdf', names)
            manifest = json.loads((out / 'SHA256.json').read_text(encoding='utf-8'))
            self.assertEqual(len(manifest), 3)
            for name, digest in manifest.items():
                self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)

    def test_ai_modules_ship_without_runtime_settings_keys_or_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            out = package.export_release(root)
            with zipfile.ZipFile(out / 'hdu-notice-hub-V1.0.0-source.zip') as archive:
                names = {name.split('/', 1)[1] for name in archive.namelist()}
                self.assertIn('ai_service.py', names)
                self.assertIn('notice_content.py', names)
                self.assertFalse(any(name.startswith(('data/', 'logs/')) for name in names))
                exported = b'\n'.join(archive.read(name) for name in archive.namelist())
                for private in (b'SECRET ai-settings', b'SECRET protected-key', b'SECRET runtime-log'):
                    self.assertNotIn(private, exported)

    def test_dirty_build_cannot_leak_runtime_data_into_portable_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            cache = root / 'dist/HDUNotice/data/portable-runtime.json'
            cache.parent.mkdir()
            cache.write_text('private token', encoding='utf-8')
            self.assertTrue(callable(getattr(package, 'export_release', None)), 'standalone export_release is missing')
            with self.assertRaisesRegex(RuntimeError, 'runtime|运行'):
                package.export_release(root)
            self.assertFalse(list(root.rglob('*.zip')))

    def test_previous_recipient_data_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            cache = root / 'release/V1.0.0/免安装程序/data/notices.json'
            cache.parent.mkdir(parents=True)
            cache.write_text('keep me', encoding='utf-8')
            self.assertTrue(callable(getattr(package, 'export_release', None)), 'standalone export_release is missing')
            with self.assertRaisesRegex(RuntimeError, 'runtime|运行'):
                package.export_release(root)
            self.assertEqual(cache.read_text(encoding='utf-8'), 'keep me')


if __name__ == '__main__':
    unittest.main()
