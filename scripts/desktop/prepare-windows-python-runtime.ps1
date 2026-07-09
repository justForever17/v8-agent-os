param(
  [string]$PythonVersion = "3.11.9",
  [string]$Architecture = "amd64",
  [string]$EngineDir = "",
  [string]$BrowserDir = ""
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
  $process = [System.Diagnostics.Process]::Start($psi)
  $stdout = $process.StandardOutput.ReadToEnd()
  $stderr = $process.StandardError.ReadToEnd()
  $process.WaitForExit()
  if ($stdout.Trim()) { Write-Host $stdout.Trim() }
  if ($stderr.Trim()) { Write-Host $stderr.Trim() }
  if ($process.ExitCode -ne 0) {
    throw "Command failed with exit code $($process.ExitCode): $FilePath $($Arguments -join ' ')"
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

if (Test-Path $workDir) {
  Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

if (Test-Path $runtimeDir) {
  Remove-Item -LiteralPath $runtimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
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
$hasSitePackages = $false
$hasImportSite = $false
foreach ($line in $pthLines) {
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
if (-not $hasSitePackages) {
  $insertAt = [Math]::Max(0, $updatedLines.Count - 1)
  $updatedLines.Insert($insertAt, "Lib\site-packages")
}
if (-not $hasImportSite) {
  $updatedLines.Add("import site")
}
Set-Content -LiteralPath $pthFile.FullName -Value $updatedLines -Encoding ascii

Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath
Invoke-Checked -FilePath $pythonExe -Arguments @($getPipPath, "--no-warn-script-location")
Invoke-Checked -FilePath $pythonExe -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
Invoke-Checked -FilePath $pythonExe -Arguments @("-m", "pip", "install", "-r", (Join-Path $EngineDir "requirements.txt"))

New-Item -ItemType Directory -Force -Path $BrowserDir | Out-Null
Invoke-Checked -FilePath $pythonExe -Arguments @("-m", "playwright", "install", "chromium") -Environment @{
  PLAYWRIGHT_BROWSERS_PATH = $BrowserDir
}

Invoke-Checked -FilePath $pythonExe -Arguments @("-V")
Invoke-Checked -FilePath $pythonExe -Arguments @("-c", "import sys; print(sys.executable); assert 'hostedtoolcache' not in sys.executable.lower(); assert '.venv' not in sys.executable.lower()")

Remove-Item -LiteralPath $getPipPath -Force -ErrorAction SilentlyContinue
Write-Host "Portable Python runtime is ready."
