param([switch]$NoBrowser, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$taskCandidates = @()
$taskLocal = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $taskLocal) { $taskCandidates += ,@($taskLocal) }
$taskCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($taskCommand -and $taskCommand.Source -notlike '*WindowsApps*') { $taskCandidates += ,@($taskCommand.Source) }
$taskPy = Get-Command py.exe -ErrorAction SilentlyContinue
if ($taskPy) { $taskCandidates += ,@($taskPy.Source, '-3') }
$taskFallback = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (Test-Path -LiteralPath $taskFallback) { $taskCandidates += ,@($taskFallback) }
foreach ($taskCandidate in $taskCandidates) {
    $taskPython = $taskCandidate[0]
    $taskPrefix = @($taskCandidate | Select-Object -Skip 1)
    & $taskPython @taskPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null
    if ($LASTEXITCODE -ne 0) { continue }
    $taskArguments = @($taskPrefix) + @('-B', (Join-Path $PSScriptRoot 'portable_launcher.py'))
    if ($Stop) { $taskArguments += '--stop' }
    if ($NoBrowser) { $taskArguments += '--no-browser' }
    & $taskPython @taskArguments
    exit $LASTEXITCODE
}
throw 'Python 3.10+ is required. See docs/INSTALL.md for source setup or download the Windows portable ZIP.'
