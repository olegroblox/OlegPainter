param(
    [ValidateSet('setup', 'doctor', 'context', 'check', 'test', 'preview-overlays', 'run', 'quick', 'build')]
    [string]$Task = 'doctor',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
# Keep large dependency trees out of OneDrive. Override for another checkout.
$venvRoot = if ($env:OLEGPAINTER_VENV) { $env:OLEGPAINTER_VENV } else {
    Join-Path $env:LOCALAPPDATA 'OlegPainter\dev-venv'
}
$pythonExe = Join-Path $venvRoot 'Scripts\python.exe'

function Invoke-Python {
    param([string[]]$Arguments)
    & $pythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python exited with code $LASTEXITCODE" }
}

$oldQtPlatform = $env:QT_QPA_PLATFORM
$oldPluginAutoload = $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
$oldPythonEncoding = $env:PYTHONIOENCODING
Push-Location $projectRoot
try {
    $env:PYTHONIOENCODING = 'utf-8'
    if ($Task -eq 'setup') {
        if (-not (Test-Path -LiteralPath $pythonExe)) {
            & py -3.13 -m venv $venvRoot
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 x64, then retry setup.' }
        }
        Invoke-Python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 13), "Python 3.13 required"')
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv pip sync --python $pythonExe requirements-dev.lock.txt
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        } else {
            Invoke-Python -Arguments @('-m', 'ensurepip')
            Invoke-Python -Arguments @('-m', 'pip', 'install', '-r', 'requirements-dev.lock.txt')
        }
        Invoke-Python -Arguments @('tools/doctor.py')
        return
    }
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Environment missing. Run: powershell -ExecutionPolicy Bypass -File .\dev.ps1 setup'
    }
    switch ($Task) {
        'doctor' { Invoke-Python -Arguments @('tools/doctor.py') }
        'context' { Invoke-Python -Arguments (@('tools/project_context.py') + $ExtraArgs) }
        'check' {
            Invoke-Python -Arguments @('tools/doctor.py')
            Invoke-Python -Arguments @('-m', 'ruff', 'check', '.')
            Invoke-Python -Arguments @('tools/check_docs.py')
        }
        'test' {
            $env:QT_QPA_PLATFORM = 'offscreen'
            $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
            Invoke-Python -Arguments (@('-m', 'pytest', '-q', '-p', 'pytest_timeout', '--timeout=60') + $ExtraArgs)
        }
        'preview-overlays' { Invoke-Python -Arguments (@('tools/preview_overlays.py') + $ExtraArgs) }
        'run' { Invoke-Python -Arguments (@('main.py') + $ExtraArgs) }
        'quick' { Invoke-Python -Arguments (@('quick_main.py') + $ExtraArgs) }
        'build' { Invoke-Python -Arguments (@('-m', 'PyInstaller', 'OlegPainter.spec') + $ExtraArgs) }
    }
} finally {
    $env:QT_QPA_PLATFORM = $oldQtPlatform
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = $oldPluginAutoload
    $env:PYTHONIOENCODING = $oldPythonEncoding
    Pop-Location
}
