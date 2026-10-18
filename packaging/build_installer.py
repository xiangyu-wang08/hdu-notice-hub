"""Build a current-user NSIS Setup.exe from the clean portable bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def payload_manifest(bundle):
    bundle = Path(bundle).resolve()
    files = []
    directories = set()
    for file in bundle.rglob('*'):
        relative = file.relative_to(bundle)
        if file.is_symlink() or any(part in {'data', '__pycache__', '.git', 'qa'} or part.startswith('.env') for part in relative.parts):
            raise RuntimeError('Installer payload contains runtime/private data')
        if file.is_file():
            files.append(relative.as_posix())
            directories.update(p.as_posix() for p in relative.parents if p != Path('.'))
    if '杭电通知集.exe' not in files:
        raise FileNotFoundError('Build the clean portable bundle first')
    return sorted(files), sorted(directories, key=lambda name: (-name.count('/'), name))


def quote(value):
    return value.replace('$', '$$').replace('"', '$\\"').replace('/', '\\')


def render_payload(files, directories):
    install = []
    for name in files:
        path = Path(name)
        parent = '' if path.parent == Path('.') else '\\' + quote(path.parent.as_posix())
        install += [f'SetOutPath "$INSTDIR{parent}"', f'File "${{BUNDLE_DIR}}\\{quote(name)}"']
    uninstall = [f'Delete "$INSTDIR\\{quote(name)}"' for name in files]
    uninstall += [f'RMDir "$INSTDIR\\{quote(name)}"' for name in directories]
    return '\n'.join(install), '\n'.join(uninstall)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--makensis', default=shutil.which('makensis') or shutil.which('makensis.exe'))
    args = parser.parse_args()
    if not args.makensis:
        parser.error('Install Unicode NSIS 3 and put makensis on PATH, or pass --makensis')
    tag = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    out = ROOT / 'release' / tag
    bundle = out / '免安装程序'
    files, directories = payload_manifest(bundle)
    generated = ROOT / 'build' / 'installer'
    generated.mkdir(parents=True, exist_ok=True)
    install, uninstall = render_payload(files, directories)
    (generated / 'install-files.nsh').write_text(install, encoding='utf-8-sig')
    (generated / 'uninstall-files.nsh').write_text(uninstall, encoding='utf-8-sig')
    setup = out / f'hdu-notice-hub-{tag}-windows-x64-Setup.exe'
    command = [args.makensis, '/INPUTCHARSET', 'UTF8', '/V2', f'/DPROJECT_ROOT={ROOT}',
               f'/DBUNDLE_DIR={bundle}', f'/DGENERATED_DIR={generated}', f'/DOUTPUT_FILE={setup}',
               f'/DVERSION_NUMBER={tag[1:]}', str(ROOT / 'packaging' / 'installer.nsi')]
    subprocess.run(command, check=True)
    manifest_path = out / 'SHA256.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest[setup.name] = hashlib.sha256(setup.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Installer:', setup)


if __name__ == '__main__':
    main()
