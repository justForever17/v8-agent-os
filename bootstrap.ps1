$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptRoot = if ($ScriptPath) { Split-Path -Parent $ScriptPath } else { $null }
$UsingCurrentCheckout =
    $ScriptRoot -and
    (Test-Path (Join-Path $ScriptRoot "apps\v8-agent-os-engine")) -and
    (Test-Path (Join-Path $ScriptRoot "apps\v8-agent-os-admin"))
$Workspace =
    if ($UsingCurrentCheckout) {
        Join-Path $ScriptRoot ".bootstrap-workspace"
    } elseif ($env:V8_AGENT_OS_BOOTSTRAP_WORKSPACE) {
        $env:V8_AGENT_OS_BOOTSTRAP_WORKSPACE
    } elseif ($HOME) {
        Join-Path $HOME ".bootstrap-workspace"
    } else {
        Join-Path (Get-Location) ".bootstrap-workspace"
    }
$LogDir = Join-Path $Workspace "logs"

$RepoUrl = "https://github.com/justForever17/v8-agent-os.git"
$RepoDir = if ($UsingCurrentCheckout) { $ScriptRoot } else { Join-Path $Workspace "v8-agent-os" }
$RepoSource = if ($UsingCurrentCheckout) { "current checkout" } else { "bootstrap workspace clone" }

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Ensure-Command([string]$CommandName, [string]$Hint) {
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Missing required command '$CommandName'. $Hint"
    }
}

function Sync-Repo([string]$RepoUrl, [string]$TargetDir) {
    if (-not (Test-Path $TargetDir)) {
        git clone $RepoUrl $TargetDir | Out-Host
        return
    }

    git -C $TargetDir pull --ff-only | Out-Host
}

function Ensure-AdminEnv([string]$TargetDir) {
    $EnvFile = Join-Path $TargetDir ".env.local"
    if (Test-Path $EnvFile) {
        return
    }

    $Secret = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    @(
        "NEXTAUTH_URL=http://127.0.0.1:9528"
        "NEXTAUTH_SECRET=$Secret"
        "NEXT_PUBLIC_APP_VERSION=1.0.0"
    ) | Set-Content -Path $EnvFile -Encoding UTF8
}

function Start-Detached([string]$WorkingDir, [string]$FilePath, [string[]]$ArgumentList, [string]$LogName) {
    $StdOut = Join-Path $LogDir "$LogName.stdout.log"
    $StdErr = Join-Path $LogDir "$LogName.stderr.log"
    Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDir -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr | Out-Null
}

New-Item -ItemType Directory -Force -Path $Workspace, $LogDir | Out-Null

Write-Step "Checking prerequisites"
if (-not $UsingCurrentCheckout) {
    Ensure-Command git "Install Git first: https://git-scm.com/downloads"
}
Ensure-Command python "Install Python 3.11+ first."
Ensure-Command npm "Install Node.js 20+ first."

if ($UsingCurrentCheckout) {
    Write-Step "Using current checkout"
} else {
    Write-Step "Syncing repository"
    Sync-Repo $RepoUrl $RepoDir
}

$EngineDir = Join-Path $RepoDir "apps\v8-agent-os-engine"
$AdminDir = Join-Path $RepoDir "apps\v8-agent-os-admin"

if ($env:V8_AGENT_OS_BOOTSTRAP_DRY_RUN -eq "1") {
    Write-Host ""
    Write-Host "Bootstrap dry run." -ForegroundColor Yellow
    Write-Host "Repo source: $RepoSource"
    Write-Host "Repo dir   : $RepoDir"
    Write-Host "Workspace  : $Workspace"
    Write-Host "Log dir    : $LogDir"
    exit 0
}

Write-Step "Preparing engine"
if (-not (Test-Path (Join-Path $EngineDir ".venv"))) {
    python -m venv (Join-Path $EngineDir ".venv")
}
& (Join-Path $EngineDir ".venv\Scripts\python.exe") -m pip install --upgrade pip | Out-Host
& (Join-Path $EngineDir ".venv\Scripts\python.exe") -m pip install -r (Join-Path $EngineDir "requirements.txt") | Out-Host

Write-Step "Preparing admin"
npm --prefix $AdminDir install | Out-Host
Ensure-AdminEnv $AdminDir

Write-Step "Starting engine and admin"
Start-Detached $EngineDir (Join-Path $EngineDir ".venv\Scripts\python.exe") @("main.py") "engine"
Start-Detached $AdminDir "npm.cmd" @("run", "dev") "admin"

Write-Host ""
Write-Host "V8 Agent OS is starting." -ForegroundColor Green
Write-Host "Source: $RepoSource"
Write-Host "Engine: http://127.0.0.1:9530"
Write-Host "Admin : http://127.0.0.1:9528"
Write-Host "Web   : install and package separately from apps/v8-agent-os-web"
Write-Host "Logs  : $LogDir"
