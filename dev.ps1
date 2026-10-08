param(
    [ValidateSet('setup', 'doctor', 'context', 'check', 'test', 'preview-overlays', 'run', 'quick', 'fresh', 'build', 'package', 'publish')]
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
$oldConfigDir = $env:OLEGPAINTER_CONFIG_DIR
$oldTelemetryDir = $env:OLEGPAINTER_TELEMETRY_DIR
$oldDriverPreview = $env:OLEGPAINTER_DRIVER_PREVIEW
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
        'fresh' {
            # The first start of a new user: empty settings in a throwaway folder, the real
            # configs and window settings untouched. "fresh nodriver" also shows the program
            # as on a computer without Interception, without touching the installed driver.
            $trial = Join-Path $env:TEMP 'OlegPainter-fresh'
            if (Test-Path -LiteralPath $trial) { Remove-Item -LiteralPath $trial -Recurse -Force }
            $env:OLEGPAINTER_CONFIG_DIR = Join-Path $trial 'configs'
            $env:OLEGPAINTER_TELEMETRY_DIR = Join-Path $trial 'telemetry'
            $rest = @($ExtraArgs | Where-Object { $_ -ne 'nodriver' })
            if ($ExtraArgs -contains 'nodriver') { $env:OLEGPAINTER_DRIVER_PREVIEW = 'missing' }
            Write-Host "Trial run in $trial. Close the usual OlegPainter window first: only one copy runs."
            Invoke-Python -Arguments (@('quick_main.py') + $rest)
        }
        'build' { Invoke-Python -Arguments (@('-m', 'PyInstaller', 'OlegPainter.spec') + $ExtraArgs) }
        # The archive a release carries: the program finds updates by its name (UPDATE-001).
        'package' { Invoke-Python -Arguments (@('tools/package_release.py') + $ExtraArgs) }
        'publish' {
            # Lint and document links first: a broken copy must not reach GitHub.
            Invoke-Python -Arguments @('-m', 'ruff', 'check', '.')
            Invoke-Python -Arguments @('tools/check_docs.py')
            Invoke-Python -Arguments (@('tools/publish_public.py') + $ExtraArgs)
        }
    }
} finally {
    $env:QT_QPA_PLATFORM = $oldQtPlatform
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = $oldPluginAutoload
    $env:PYTHONIOENCODING = $oldPythonEncoding
    $env:OLEGPAINTER_CONFIG_DIR = $oldConfigDir
    $env:OLEGPAINTER_TELEMETRY_DIR = $oldTelemetryDir
    $env:OLEGPAINTER_DRIVER_PREVIEW = $oldDriverPreview
    Pop-Location
}
