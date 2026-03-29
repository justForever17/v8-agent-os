param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 9530,
    [string]$PythonPath = "",
    [string]$LogDir = "",
    [switch]$UseSystemPython,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$engineRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..\\..")).Path
$defaultPython = Join-Path $engineRoot ".venv\\Scripts\\python.exe"

function Resolve-EnginePythonPath {
    param(
        [string]$RequestedPythonPath,
        [string]$CanonicalPythonPath,
        [switch]$AllowSystemPython
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedPythonPath)) {
        return $RequestedPythonPath
    }

    if (Test-Path $CanonicalPythonPath) {
        return $CanonicalPythonPath
    }

    if (-not $AllowSystemPython) {
        throw ("Canonical engine interpreter not found: {0}. Create v8-agent-os-engine/.venv or pass -PythonPath explicitly." -f $CanonicalPythonPath)
    }

    Write-Warning "[v8chat] -UseSystemPython is for debugging only and may cause interpreter drift."
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python not found. Create v8-agent-os-engine/.venv or pass -PythonPath."
    }
    return $pythonCommand.Source
}

function Write-LaunchSummary {
    param(
        [string]$EffectivePythonPath,
        [string]$EffectiveLogDir,
        [string]$EffectiveLogFile
    )

    $stateDb = Join-Path $HOME ".v8-agent-os\\state.db"
    $checkpointDb = Join-Path $HOME ".v8-agent-os\\checkpoints.db"
    $configJson = Join-Path $HOME ".v8-agent-os\\config.json"

    Write-Host ""
    Write-Host "[v8chat] Starting Engine in prod-like mode"
    Write-Host ("  Repo root: {0}" -f $repoRoot)
    Write-Host ("  Engine root: {0}" -f $engineRoot)
    Write-Host ("  Python: {0}" -f $EffectivePythonPath)
    Write-Host "  Launch mode: prod-like foreground single-process"
    Write-Host ("  Host: {0}" -f $BindHost)
    Write-Host ("  Port: {0}" -f $Port)
    Write-Host "  Reload: disabled"
    Write-Host ("  Bytecode cache: redirected to {0}" -f $env:V8_AGENT_OS_PYCACHE_PREFIX)
    Write-Host ("  Log dir: {0}" -f $EffectiveLogDir)
    if (-not [string]::IsNullOrWhiteSpace($EffectiveLogFile)) {
        Write-Host ("  Log file: {0}" -f $EffectiveLogFile)
    }
    Write-Host ("  Config file: {0}" -f $configJson)
    Write-Host ("  Runtime ledger DB: {0}" -f $stateDb)
    Write-Host ("  Checkpoint DB: {0}" -f $checkpointDb)
    Write-Host ""
}

$PythonPath = Resolve-EnginePythonPath -RequestedPythonPath $PythonPath -CanonicalPythonPath $defaultPython -AllowSystemPython:$UseSystemPython

if (-not (Test-Path $PythonPath)) {
    throw ("Python executable not found: {0}" -f $PythonPath)
}

if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path $HOME ".v8-agent-os\\logs\\engine"
}

$env:ENGINE_HOST = $BindHost
$env:ENGINE_PORT = [string]$Port
$env:ENGINE_RELOAD = "0"
$env:PYTHONUNBUFFERED = "1"
$env:V8_AGENT_OS_DISABLE_BYTECODE = "0"
$env:V8_AGENT_OS_PYCACHE_PREFIX = Join-Path $HOME ".v8-agent-os\\cache\\pycache\\engine"
$env:PYTHONPYCACHEPREFIX = $env:V8_AGENT_OS_PYCACHE_PREFIX

$engineArgs = @(".\\main.py")

if ($CheckOnly) {
    Write-LaunchSummary -EffectivePythonPath $PythonPath -EffectiveLogDir $LogDir -EffectiveLogFile ""
    Write-Host "[v8chat] CheckOnly passed. Canonical prod-like launch prerequisites look valid."
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $LogDir ("engine-prod-like-{0}.log" -f $timestamp)

Write-LaunchSummary -EffectivePythonPath $PythonPath -EffectiveLogDir $LogDir -EffectiveLogFile $logFile

Push-Location $engineRoot
try {
    $quotedArgs = @($PythonPath) + $engineArgs | ForEach-Object {
        $arg = [string]$_
        if ($arg.Contains('"')) {
            $arg = $arg.Replace('"', '\"')
        }
        if ($arg -match '\s') {
            '"{0}"' -f $arg
        } else {
            $arg
        }
    }
    $commandLine = ($quotedArgs -join " ")
    & $env:ComSpec /d /s /c $commandLine 2>&1 | Tee-Object -FilePath $logFile
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($null -ne $exitCode -and $exitCode -ne 0) {
    exit $exitCode
}
