"""Install/upgrade/uninstall the actual Setup.exe in an isolated QA directory."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import winreg
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
TAG = (ROOT / 'VERSION').read_text().strip()
SETUP = ROOT / 'release' / TAG / f'hdu-notice-hub-{TAG}-windows-x64-Setup.exe'
REG = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub'
QA = ROOT / 'qa' / TAG
TARGET = QA / ('安装 验证 ' + str(time.time_ns()))
FLAGS = subprocess.CREATE_NO_WINDOW
HTTP = build_opener(ProxyHandler({}))
TEST_KEY = 'installer-qa-dummy-never-real'
TEST_SETTINGS = {'base_url': 'https://installer-qa.invalid/v1', 'model': 'installer-qa-offline', 'api_key': TEST_KEY}
EMPTY_SETTINGS = {'base_url': '', 'model': '', 'key_saved': False, 'configured': False, 'warning': None}

sys.path.insert(0, str(ROOT))
from ai_service import Settings
from server import Store


def registry():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return {name: winreg.QueryValueEx(key, name)[0] for name in ('InstallLocation', 'DisplayVersion', 'UninstallString')}
    except FileNotFoundError:
        return None


def shell_folder(csidl):
    buffer = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0
    return Path(buffer.value)


def wait_state():
    for _ in range(120):
        try:
            runtime = json.loads((TARGET / 'data/portable-runtime.json').read_text(encoding='utf-8'))
            with HTTP.open(f'http://127.0.0.1:{runtime["port"]}/api/state', timeout=2) as response:
                state = json.load(response)
            if runtime['instance'] == state['instance']:
                return state
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.1)
    raise AssertionError('Installed app failed readiness')


def ai_settings(payload=None):
    """Only local settings persistence is allowed here; never probe a model endpoint."""
    runtime = json.loads((TARGET / 'data/portable-runtime.json').read_text(encoding='utf-8'))
    base = f'http://127.0.0.1:{runtime["port"]}'
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = Request(base + '/api/ai/settings', data=data,
                      headers={'Content-Type': 'application/json', 'Origin': base})
    with HTTP.open(request, timeout=3) as response:
        result = json.load(response)
    assert 'api_key' not in result and TEST_KEY not in json.dumps(result), 'The settings API exposed a key'
    return result


def assert_configuration(settings):
    assert settings == dict(base_url=TEST_SETTINGS['base_url'], model=TEST_SETTINGS['model'],
                            key_saved=True, configured=True, warning=None), settings


def version(exe):
    result = subprocess.run([str(exe), '--version'], capture_output=True, text=True,
                            creationflags=FLAGS, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def assert_installation(expected_tag, desktop, menu, current_manual=False):
    registration = registry()
    assert registration is not None
    assert Path(registration['InstallLocation']).resolve() == TARGET.resolve()
    assert registration['DisplayVersion'] == expected_tag.removeprefix('V')
    assert registration['UninstallString'] == f'"{TARGET / "Uninstall.exe"}"'
    assert desktop.is_file()
    assert all((menu / name).is_file() for name in ('杭电通知集.lnk', '关闭服务.lnk', '安装与使用手册.lnk', '卸载.lnk'))
    if current_manual:
        assert (TARGET / '本地安装部署说明书.pdf').read_bytes() == (ROOT / 'docs/INSTALL.pdf').read_bytes()


def main(upgrade_from=None):
    if os.name != 'nt':
        raise RuntimeError('Windows only')
    assert SETUP.is_file(), f'Current installer is missing: {SETUP}'
    first_setup = Path(upgrade_from).resolve() if upgrade_from else SETUP
    assert first_setup.is_file(), f'Initial installer is missing: {first_setup}'
    # Avoid changing an existing real installation or shortcuts.
    assert registry() is None, 'A real installation exists; do not overwrite it during QA'
    desktop = shell_folder(0x10) / '杭电通知集.lnk'
    menu = shell_folder(0x02) / '杭电通知集'
    assert not desktop.exists() and not menu.exists(), 'Existing user shortcuts must be preserved; QA stopped'
    assert TARGET.resolve().is_relative_to(QA.resolve()) and not TARGET.exists()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    foreign = TARGET.with_name(TARGET.name + ' existing files')
    foreign.mkdir()
    sentinel = foreign / 'personal.txt'
    sentinel.write_text('preserve unrelated folder', encoding='utf-8')
    refused = subprocess.run(f'"{SETUP}" /S /D={foreign}', creationflags=FLAGS, timeout=30)
    assert refused.returncode != 0 and sentinel.read_text(encoding='utf-8') == 'preserve unrelated folder'
    assert not (foreign / '杭电通知集.exe').exists() and registry() is None
    proc = None
    installed = False
    try:
        result = subprocess.run(f'"{first_setup}" /S /D={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        installed = True
        exe = TARGET / '杭电通知集.exe'
        initial_version = version(exe)
        assert initial_version.startswith('V')
        assert initial_version != TAG if upgrade_from else initial_version == TAG
        assert_installation(initial_version, desktop, menu, current_manual=not upgrade_from)
        proc = subprocess.Popen([str(exe), '--no-browser'], creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        initial_state = wait_state()
        assert len(initial_state['sources']) == 25 and initial_state['notices'] == []
        settings_file = TARGET / 'data/ai-settings.json'
        assert not settings_file.exists(), 'A clean install contains AI runtime settings'
        if not upgrade_from:
            assert ai_settings() == EMPTY_SETTINGS
            assert not settings_file.exists(), 'Reading empty AI settings persisted a configuration'
            assert_configuration(ai_settings(TEST_SETTINGS))
        else:
            # V1.0.0 has no AI routes; seed only this isolated future-format fixture.
            assert_configuration(Settings(settings_file).save(TEST_SETTINGS))
        protected_settings = settings_file.read_bytes()
        assert TEST_KEY.encode() not in protected_settings, 'The test Key was stored as plaintext'
        assert_configuration(Settings(settings_file).public())
        assert Settings(settings_file).resolve({})['api_key'] == TEST_KEY
        # A real upgrade must stop the old process and preserve non-payload data.
        cache = TARGET / 'data/notices.json'
        store = Store(cache)
        store.apply_result('jwc', dict(items=[dict(id='installer-qa-fixture', source_id='jwc', source_name='教务处',
                          title='安装验收离线通知', date='2026-10-19', url='https://jwc.hdu.edu.cn/installer-qa.htm')],
                          pages=1, limited=False, error=None))
        cache.write_text(json.dumps({'version': 2, 'sources': store.records}, ensure_ascii=False), encoding='utf-8')
        cached_notices = cache.read_bytes()
        personal = TARGET / 'my-file.txt'
        personal.write_text('keep user file', encoding='utf-8')
        result = subprocess.run(f'"{SETUP}" /S /D={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        proc.wait(timeout=15)
        proc = None
        assert cache.read_bytes() == cached_notices and personal.exists()
        assert settings_file.read_bytes() == protected_settings, 'Upgrade replaced the protected AI configuration'
        assert Settings(settings_file).resolve({})['api_key'] == TEST_KEY
        assert version(exe) == TAG
        assert_installation(TAG, desktop, menu, current_manual=True)
        proc = subprocess.Popen([str(exe), '--no-browser'], creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        upgraded_state = wait_state()
        assert len(upgraded_state['sources']) == 25
        assert len(upgraded_state['notices']) == 1 and upgraded_state['notices'][0]['id'] == 'installer-qa-fixture'
        assert next(source for source in upgraded_state['sources'] if source['id'] == 'jwc')['from_cache']
        assert_configuration(ai_settings())
        uninstaller = TARGET / 'Uninstall.exe'
        # NSIS _?= is the final unquoted remainder; no shell is involved.
        result = subprocess.run(f'"{uninstaller}" /S _?={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        proc.wait(timeout=15)
        proc = None
        installed = False
        assert registry() is None and not desktop.exists() and not menu.exists()
        assert not exe.exists() and not (TARGET / '_internal/python312.dll').exists()
        assert cache.read_bytes() == cached_notices and personal.read_text(encoding='utf-8') == 'keep user file'
        assert settings_file.read_bytes() == protected_settings
        assert Settings(settings_file).resolve({})['api_key'] == TEST_KEY
        report = dict(version=TAG, initial_version=initial_version, mode='upgrade' if upgrade_from else 'fresh',
                      rejects_unrelated_directory=True, install=True, current_user_registration=True, shortcuts=True,
                      installed_app=True, upgrade_stops_service=True, uninstall_stops_service=True,
                      cache_preserved=True, cached_notice_restored=True, ai_settings_initially_blank=not upgrade_from,
                      protected_ai_settings_preserved=True, dummy_key_dpapi_roundtrip=True, model_requests=0,
                      unowned_files_preserved=True, registration_and_shortcuts_removed=True)
        (QA / ('installer-check-upgrade.json' if upgrade_from else 'installer-check-fresh.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        if proc and proc.poll() is None:
            subprocess.run([str(TARGET / '杭电通知集.exe'), '--stop'], capture_output=True, creationflags=FLAGS, timeout=15)
            proc.wait(timeout=15)
        if installed and (TARGET / 'Uninstall.exe').exists():
            subprocess.run(f'"{TARGET / "Uninstall.exe"}" /S _?={TARGET}', creationflags=FLAGS, timeout=90)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upgrade-from', type=Path, help='install this older Setup.exe first, then upgrade to the current VERSION')
    args = parser.parse_args()
    main(args.upgrade_from)
