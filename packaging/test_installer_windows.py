"""Install/upgrade/uninstall the actual Setup.exe in an isolated QA directory."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time
import winreg
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
TAG = (ROOT / 'VERSION').read_text().strip()
SETUP = ROOT / 'release' / TAG / f'hdu-notice-hub-{TAG}-windows-x64-Setup.exe'
REG = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub'
TARGET = ROOT / 'qa' / ('安装 验证 ' + str(time.time_ns()))
FLAGS = subprocess.CREATE_NO_WINDOW
HTTP = build_opener(ProxyHandler({}))


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


def main():
    if os.name != 'nt':
        raise RuntimeError('Windows only')
    # Avoid changing an existing real installation or shortcuts.
    assert registry() is None, 'A real installation exists; do not overwrite it during QA'
    desktop = shell_folder(0x10) / '杭电通知集.lnk'
    menu = shell_folder(0x02) / '杭电通知集'
    assert not desktop.exists() and not menu.exists(), 'Existing user shortcuts must be preserved; QA stopped'
    assert TARGET.resolve().is_relative_to((ROOT / 'qa').resolve())
    TARGET.parent.mkdir(exist_ok=True)
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
        result = subprocess.run(f'"{SETUP}" /S /D={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        installed = True
        registration = registry()
        assert Path(registration['InstallLocation']) == TARGET
        assert registration['DisplayVersion'] == '1.0.0'
        assert registration['UninstallString'] == f'"{TARGET / "Uninstall.exe"}"'
        assert desktop.is_file()
        assert all((menu / name).is_file() for name in ('杭电通知集.lnk', '关闭服务.lnk', '安装与使用手册.lnk', '卸载.lnk'))
        assert (TARGET / '本地安装部署说明书.pdf').read_bytes() == (ROOT / 'docs/INSTALL.pdf').read_bytes()
        exe = TARGET / '杭电通知集.exe'
        proc = subprocess.Popen([str(exe), '--no-browser'], creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert len(wait_state()['sources']) == 25
        # A real upgrade must stop the old process and preserve non-payload data.
        cache = TARGET / 'data/keep-cache.txt'
        cache.write_text('keep cache', encoding='utf-8')
        personal = TARGET / 'my-file.txt'
        personal.write_text('keep user file', encoding='utf-8')
        result = subprocess.run(f'"{SETUP}" /S /D={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        proc.wait(timeout=15)
        proc = None
        assert cache.read_text(encoding='utf-8') == 'keep cache' and personal.exists()
        proc = subprocess.Popen([str(exe), '--no-browser'], creationflags=FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert len(wait_state()['sources']) == 25
        uninstaller = TARGET / 'Uninstall.exe'
        # NSIS _?= is the final unquoted remainder; no shell is involved.
        result = subprocess.run(f'"{uninstaller}" /S _?={TARGET}', creationflags=FLAGS, timeout=90)
        assert result.returncode == 0, result.returncode
        proc.wait(timeout=15)
        proc = None
        installed = False
        assert registry() is None and not desktop.exists() and not menu.exists()
        assert not exe.exists() and not (TARGET / '_internal/python312.dll').exists()
        assert cache.read_text(encoding='utf-8') == 'keep cache' and personal.read_text(encoding='utf-8') == 'keep user file'
        report = dict(version=TAG, rejects_unrelated_directory=True, install=True, current_user_registration=True, shortcuts=True,
                      installed_app=True, upgrade_stops_service=True, uninstall_stops_service=True,
                      cache_preserved=True, unowned_files_preserved=True, registration_and_shortcuts_removed=True)
        (ROOT / 'qa/installer-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        if proc and proc.poll() is None:
            subprocess.run([str(TARGET / '杭电通知集.exe'), '--stop'], capture_output=True, creationflags=FLAGS, timeout=15)
            proc.wait(timeout=15)
        if installed and (TARGET / 'Uninstall.exe').exists():
            subprocess.run(f'"{TARGET / "Uninstall.exe"}" /S _?={TARGET}', creationflags=FLAGS, timeout=90)


if __name__ == '__main__':
    main()
