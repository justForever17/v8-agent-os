$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProfileMode = "minimal"
$ServicesMode = "engine+admin"
$PlatformMode = "auto"

for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        "--profile" {
            $i++
            $ProfileMode = if ($i -lt $args.Count) { $args[$i] } else { "" }
        }
        "--services" {
            $i++
            $ServicesMode = if ($i -lt $args.Count) { $args[$i] } else { "" }
        }
        "--platform" {
            $i++
            $PlatformMode = if ($i -lt $args.Count) { $args[$i] } else { "" }
        }
        default {
            throw "Unknown argument '$($args[$i])'."
        }
    }
}

if ($ProfileMode -eq "standard") {
    $ProfileMode = "minimal"
}
if ($ProfileMode -notin @("minimal", "desktop")) {
    throw "Unsupported --profile value: $ProfileMode"
}
if ($ServicesMode -notin @("engine", "engine+admin")) {
    throw "Unsupported --services value: $ServicesMode"
}

function Resolve-Platform([string]$Requested) {
    if ($Requested -and $Requested -ne "auto") {
        if ($Requested -notin @("windows", "macos", "linux")) {
            throw "Unsupported --platform value: $Requested"
        }
        return $Requested
    }
    if ($IsWindows) { return "windows" }
    if ($IsMacOS) { return "macos" }
    return "linux"
}

$PlatformMode = Resolve-Platform $PlatformMode

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

function Get-RequirementsForProfile([string]$EngineDir) {
    $items = [System.Collections.Generic.List[string]]::new()
    $items.Add((Join-Path $EngineDir "requirements\base.txt"))
    $items.Add((Join-Path $EngineDir "requirements\minimal.txt"))
    if ($ProfileMode -eq "desktop") {
        $items.Add((Join-Path $EngineDir "requirements\desktop-common.txt"))
        $items.Add((Join-Path $EngineDir "requirements\platform-$PlatformMode.txt"))
    }
    return $items
}

function Get-InstalledRuntimeFamilies([string]$Profile) {
    if ($Profile -eq "desktop") {
        return @("chat", "memory", "extensions", "automation", "network_supervisor", "computer_use", "rpa", "desktop_live")
    }
    return @("chat", "memory", "extensions", "automation", "network_supervisor")
}

function Sync-RuntimeRegistry([string]$EngineDir, [string]$PythonExe, [string]$Profile, [string]$Platform, [bool]$BootstrapManaged) {
    $FamiliesJson = (Get-InstalledRuntimeFamilies $Profile | ConvertTo-Json -Compress)
    $Timestamp = (Get-Date).ToString("o")
    Push-Location $EngineDir
    try {
        @"
import json
from core.storage import storage

payload = storage.get_runtime_registry_config()
payload.update(
    {
        "installProfile": "$Profile",
        "installPlatform": "$Platform",
        "installedRuntimeFamilies": json.loads(r'''$FamiliesJson'''),
        "bootstrapManaged": bool($($BootstrapManaged.ToString().ToLowerInvariant())),
        "lastUpgradeAt": "$Timestamp",
        "startupProfile": "$Profile",
    }
)
storage.save_runtime_registry_config(payload)
"@ | & $PythonExe -
    } finally {
        Pop-Location
    }
}

function Stop-ExistingEngine([int]$Port) {
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
        $connections = @()
    }
    foreach ($pid in @($connections)) {
        if (-not $pid) { continue }
        try {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

function Invoke-DesktopPreflight {
    if ($ProfileMode -ne "desktop") {
        return
    }

    if ($PlatformMode -eq "macos") {
        Write-Step "macOS desktop preflight"
        $SwiftReady = [bool](Get-Command swiftc -ErrorAction SilentlyContinue)
        $OsascriptReady = [bool](Get-Command osascript -ErrorAction SilentlyContinue)
        Write-Host ("[{0}] swiftc / Xcode Command Line Tools" -f ($(if ($SwiftReady) { "ok" } else { "missing" })))
        Write-Host ("[{0}] osascript / Apple Events bridge" -f ($(if ($OsascriptReady) { "ok" } else { "missing" })))
        Write-Host "[manual] Accessibility permission"
        Write-Host "[manual] Screen Recording permission"
        Write-Host "[manual] Input Monitoring / synthetic input permission"
        if (-not $SwiftReady) {
            Write-Warning "swiftc not found. The macOS AX helper will not compile until Xcode Command Line Tools are installed."
        }
    }

    if ($PlatformMode -eq "linux") {
        Write-Step "Linux desktop preflight"
        foreach ($candidate in @("gdbus", "dbus-send", "xdotool", "wmctrl", "grim", "gnome-screenshot")) {
            $Found = [bool](Get-Command $candidate -ErrorAction SilentlyContinue)
            Write-Host ("[{0}] {1}" -f ($(if ($Found) { "ok" } else { "missing" })), $candidate)
        }
        $SessionType = if ($env:XDG_SESSION_TYPE) { $env:XDG_SESSION_TYPE } else { "unset" }
        Write-Host ("[{0}] XDG_SESSION_TYPE={1}" -f ($(if ($env:XDG_SESSION_TYPE) { "info" } else { "unknown" })), $SessionType)
        Write-Host "[manual] portal / compositor screenshot permission"
        Write-Host "[manual] AT-SPI accessibility bus availability"
        if ($env:XDG_SESSION_TYPE -eq "wayland") {
            Write-Warning "Wayland session detected. Screenshot/input fallbacks may require portal/compositor support."
        }
    }
}

function Start-Detached([string]$WorkingDir, [string]$FilePath, [string[]]$ArgumentList, [string]$LogName, [hashtable]$Environment = @{}) {
    $StdOut = Join-Path $LogDir "$LogName.stdout.log"
    $StdErr = Join-Path $LogDir "$LogName.stderr.log"
    $PreviousEnvironment = @{}
    foreach ($entry in $Environment.GetEnumerator()) {
        $PreviousEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, "Process")
        [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, "Process")
    }
    try {
        $Process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDir `
            -RedirectStandardOutput $StdOut `
            -RedirectStandardError $StdErr `
            -PassThru
    } finally {
        foreach ($entry in $PreviousEnvironment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
    }
    if (-not $Process) {
        throw "Failed to start process '$FilePath'."
    }
    Write-Host ("Started {0} (PID {1})" -f $LogName, $Process.Id)
}

New-Item -ItemType Directory -Force -Path $Workspace, $LogDir | Out-Null

Write-Step "Checking prerequisites"
if (-not $UsingCurrentCheckout) {
    Ensure-Command git "Install Git first: https://git-scm.com/downloads"
}
Ensure-Command python "Install Python 3.11+ first."
if ($ServicesMode -eq "engine+admin") {
    Ensure-Command npm "Install Node.js 20+ first."
}

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
    Write-Host "Profile    : $ProfileMode"
    Write-Host "Services   : $ServicesMode"
    Write-Host "Platform   : $PlatformMode"
    Write-Host "Requirements:"
    Get-RequirementsForProfile $EngineDir | ForEach-Object { Write-Host " - $_" }
    exit 0
}

Invoke-DesktopPreflight

Write-Step "Preparing engine"
if (-not (Test-Path (Join-Path $EngineDir ".venv"))) {
    python -m venv (Join-Path $EngineDir ".venv")
}
$PythonExe = Join-Path $EngineDir ".venv\Scripts\python.exe"
& $PythonExe -m pip install --upgrade pip | Out-Host
foreach ($RequirementFile in Get-RequirementsForProfile $EngineDir) {
    if (Test-Path $RequirementFile) {
        & $PythonExe -m pip install -r $RequirementFile | Out-Host
    }
}
$BootstrapManagedMode = $env:V8_AGENT_OS_BOOTSTRAP_MANAGED -ne "0"
Sync-RuntimeRegistry $EngineDir $PythonExe $ProfileMode $PlatformMode $BootstrapManagedMode

if ($ServicesMode -eq "engine+admin") {
    Write-Step "Preparing admin"
    npm --prefix $AdminDir install | Out-Host
    Ensure-AdminEnv $AdminDir
}

Write-Step "Starting services"
if ($env:V8_AGENT_OS_BOOTSTRAP_INSTALL_ONLY -eq "1") {
    Write-Host ""
    Write-Host "Install-only mode complete. Please restart the engine manually." -ForegroundColor Yellow
    exit 0
}
if ($env:V8_AGENT_OS_BOOTSTRAP_RESTART_ENGINE -eq "1") {
    Stop-ExistingEngine 9530
}
Start-Detached $EngineDir $PythonExe @("main.py") "engine" @{
    ENGINE_STARTUP_PROFILE = $ProfileMode
    ENGINE_INSTALL_PROFILE = $ProfileMode
    ENGINE_INSTALL_PLATFORM = $PlatformMode
}
if ($ServicesMode -eq "engine+admin") {
    Start-Detached $AdminDir "npm.cmd" @("run", "dev") "admin"
}

Write-Host ""
Write-Host "V8 Agent OS is starting." -ForegroundColor Green
Write-Host "Source  : $RepoSource"
Write-Host "Profile : $ProfileMode"
Write-Host "Platform: $PlatformMode"
Write-Host "Engine  : http://127.0.0.1:9530"
if ($ServicesMode -eq "engine+admin") {
    Write-Host "Admin   : http://127.0.0.1:9528"
} else {
    Write-Host "Admin   : skipped"
}
Write-Host "Web     : install and package separately from apps/v8-agent-os-web"
Write-Host "Logs    : $LogDir"
