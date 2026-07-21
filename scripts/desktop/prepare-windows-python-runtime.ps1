param(
  [string]$PythonVersion = "3.11.9",
  [string]$Architecture = "amd64",
  [string]$EngineDir = "",
  [string]$BrowserDir = "",
  [string]$RequirementsPath = "",
  [switch]$SkipPlaywrightBrowsers
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  $scriptPath = $PSCommandPath
  if (-not $scriptPath) {
    $scriptPath = $MyInvocation.MyCommand.Path
  }
  return (Resolve-Path (Join-Path (Split-Path -Parent $scriptPath) "..\..")).Path
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @(),
    [hashtable]$Environment = @{}
  )
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $FilePath
  foreach ($arg in $Arguments) {
    [void]$psi.ArgumentList.Add($arg)
  }
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  foreach ($entry in $Environment.GetEnumerator()) {
    $psi.Environment[$entry.Key] = [string]$entry.Value
  }
  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $psi
  [void]$process.Start()
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  $process.WaitForExit()
  $stdout = $stdoutTask.GetAwaiter().GetResult()
  $stderr = $stderrTask.GetAwaiter().GetResult()
  if ($stdout) {
    Write-Host $stdout.TrimEnd()
  }
  if ($stderr) {
    Write-Host $stderr.TrimEnd()
  }
  if ($process.ExitCode -ne 0) {
    throw "Command failed with exit code $($process.ExitCode): $FilePath $($Arguments -join ' ')"
  }
}

function Invoke-WithRetry {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Script,
    [Parameter(Mandatory = $true)][string]$Description,
    [int]$Attempts = 3
  )
  for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    try {
      if ($attempt -gt 1) {
        Write-Host "Retrying $Description (attempt $attempt/$Attempts)..."
      }
      & $Script
      return
    } catch {
      if ($attempt -ge $Attempts) {
        throw
      }
      $delaySeconds = [Math]::Min(20, 5 * $attempt)
      Write-Host "$Description failed: $($_.Exception.Message)"
      Write-Host "Waiting $delaySeconds seconds before retry..."
      Start-Sleep -Seconds $delaySeconds
    }
  }
}

function Invoke-WebRequestWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Uri,
    [Parameter(Mandatory = $true)][string]$OutFile
  )
  Invoke-WithRetry -Description "Download $Uri" -Script {
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile
  }
}

function Invoke-CheckedWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @(),
    [hashtable]$Environment = @{},
    [string]$Description = "",
    [int]$Attempts = 2
  )
  if (-not $Description) {
    $Description = "$FilePath $($Arguments -join ' ')"
  }
  Invoke-WithRetry -Description $Description -Attempts $Attempts -Script {
    Invoke-Checked -FilePath $FilePath -Arguments $Arguments -Environment $Environment
  }
}

$repoRoot = Resolve-RepoRoot
if (-not $EngineDir) {
  $EngineDir = Join-Path $repoRoot "apps\v8-agent-os-engine"
}
$EngineDir = (Resolve-Path $EngineDir).Path
if (-not (Test-Path (Join-Path $EngineDir "main.py"))) {
  throw "EngineDir does not look like apps/v8-agent-os-engine: $EngineDir"
}

if ($RequirementsPath) {
  $requirementsFile = (Resolve-Path $RequirementsPath).Path
} else {
  $requirementsFile = Join-Path $EngineDir "requirements.txt"
}
if (-not (Test-Path $requirementsFile)) {
  throw "Requirements file was not found: $requirementsFile"
}

$runtimeDir = Join-Path $EngineDir ".python"
if (-not $BrowserDir) {
  $BrowserDir = Join-Path $EngineDir ".playwright-browsers"
}

$tmpRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDir = Join-Path $tmpRoot "v8os-python-runtime-$PythonVersion"
$zipPath = Join-Path $workDir "python-embed.zip"
$getPipPath = Join-Path $workDir "get-pip.py"
$zipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-$Architecture.zip"

Write-Host "Preparing portable Python $PythonVersion ($Architecture)"
Write-Host "EngineDir: $EngineDir"
Write-Host "RuntimeDir: $runtimeDir"
Write-Host "Requirements: $requirementsFile"

if (Test-Path $workDir) {
  Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

if (Test-Path $runtimeDir) {
  Remove-Item -LiteralPath $runtimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

Invoke-WebRequestWithRetry -Uri $zipUrl -OutFile $zipPath
Expand-Archive -LiteralPath $zipPath -DestinationPath $runtimeDir -Force

$pythonExe = Join-Path $runtimeDir "python.exe"
$pythonwExe = Join-Path $runtimeDir "pythonw.exe"
if (-not (Test-Path $pythonExe)) {
  throw "Portable python.exe was not found after extracting $zipUrl"
}
if (-not (Test-Path $pythonwExe)) {
  throw "Portable pythonw.exe was not found after extracting $zipUrl"
}

$pthFile = Get-ChildItem -LiteralPath $runtimeDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pthFile) {
  throw "Cannot find python ._pth file in $runtimeDir"
}

$pthLines = Get-Content -LiteralPath $pthFile.FullName
$updatedLines = New-Object System.Collections.Generic.List[string]
$hasEngineRoot = $false
$hasSitePackages = $false
$hasImportSite = $false
foreach ($line in $pthLines) {
  if ($line.Trim() -eq "..") {
    $hasEngineRoot = $true
  }
  if ($line.Trim() -eq "Lib\site-packages") {
    $hasSitePackages = $true
  }
  if ($line.Trim() -eq "import site") {
    $hasImportSite = $true
  }
  if ($line -match "^\s*#\s*import site\s*$") {
    $updatedLines.Add("import site")
    $hasImportSite = $true
  } else {
    $updatedLines.Add($line)
  }
}
if (-not $hasEngineRoot) {
  $updatedLines.Insert(1, "..")
}
if (-not $hasSitePackages) {
  $insertAt = [Math]::Max(0, $updatedLines.Count - 1)
  $updatedLines.Insert($insertAt, "Lib\site-packages")
}
if (-not $hasImportSite) {
  $updatedLines.Add("import site")
}
Set-Content -LiteralPath $pthFile.FullName -Value $updatedLines -Encoding ascii

Invoke-WebRequestWithRetry -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath
Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @($getPipPath, "--no-warn-script-location") -Description "Install pip into portable Python"
Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip", "setuptools", "wheel") -Description "Upgrade portable Python packaging tools"
Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--prefer-binary", "-r", $requirementsFile) -Description "Install desktop preview Engine requirements"

New-Item -ItemType Directory -Force -Path $BrowserDir | Out-Null
if ($SkipPlaywrightBrowsers) {
  $markerPath = Join-Path $BrowserDir "DEGRADED.txt"
  Set-Content -LiteralPath $markerPath -Value "Playwright browsers were intentionally skipped. V8OS discovers an installed Edge, Chrome, or Chromium at runtime." -Encoding utf8
  Write-Host "Skipping Playwright Chromium install. Agent Browser will discover a compatible system browser at runtime."
} else {
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @("-m", "playwright", "install", "chromium") -Description "Install Playwright Chromium" -Environment @{
    PLAYWRIGHT_BROWSERS_PATH = $BrowserDir
  }
}

Invoke-Checked -FilePath $pythonExe -Arguments @("-V")
Invoke-Checked -FilePath $pythonExe -Arguments @("-c", "import sys; print(sys.executable); assert 'hostedtoolcache' not in sys.executable.lower(); assert '.venv' not in sys.executable.lower()")
$probeHome = Join-Path $workDir "engine-import-probe"
New-Item -ItemType Directory -Force -Path $probeHome | Out-Null
Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", "import main; print('V8OS_ENGINE_IMPORT_OK')") -Environment @{
  V8_AGENT_OS_HOME = $probeHome
  V8_AGENT_OS_DISABLE_BYTECODE = "1"
}

Remove-Item -LiteralPath $getPipPath -Force -ErrorAction SilentlyContinue
Write-Host "Portable Python runtime is ready."
