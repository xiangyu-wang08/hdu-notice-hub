param([string]$BuilderPython)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskBuilder = if ($BuilderPython) { $BuilderPython } else { Join-Path $taskRoot '.venv-build\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $taskBuilder)) { throw 'Create .venv-build and install PyInstaller 6.22.0 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'docs\INSTALL.pdf'))) { throw 'Generate docs/INSTALL.pdf with packaging/build_manual.py first.' }
& $taskBuilder -m PyInstaller --noconfirm --onedir --console --noupx --name HDUNotice --add-data 'web;web' --distpath dist --workpath build portable_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
& $taskBuilder (Join-Path $PSScriptRoot 'package_release.py')
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
