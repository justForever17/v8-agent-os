param(
  [string]$PythonVersion = "3.11.9",
  [string]$Architecture = "amd64",
  [string]$EngineDir = "",
  [string]$BrowserDir = "",
  [string]$RequirementsPath = "",
  [switch]$SkipPlaywrightBrowsers
)

$ErrorActionPreference = "Stop"

if ($Architecture -notin @("amd64", "arm64")) {
  throw "Unsupported portable Python architecture: $Architecture"
}
if ($Architecture -eq "arm64" -and $PythonVersion -ne "3.11.9") {
  throw "Windows ARM64 build support is pinned to Python 3.11.9, received: $PythonVersion"
}

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
    [hashtable]$Environment = @{},
    [string]$WorkingDirectory = ""
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
  if ($WorkingDirectory) {
    $psi.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
  }
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
    [string]$WorkingDirectory = "",
    [string]$Description = "",
    [int]$Attempts = 2
  )
  if (-not $Description) {
    $Description = "$FilePath $($Arguments -join ' ')"
  }
  Invoke-WithRetry -Description $Description -Attempts $Attempts -Script {
    Invoke-Checked -FilePath $FilePath -Arguments $Arguments -Environment $Environment -WorkingDirectory $WorkingDirectory
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

$installRequirementsFile = $requirementsFile
if ($Architecture -eq "arm64") {
  $requirementsSourceRoot = (Resolve-Path (Join-Path $EngineDir "requirements")).Path
  $engineRequirementsFile = Join-Path $EngineDir "requirements.txt"
  $requirementsSourcePrefix = $requirementsSourceRoot.TrimEnd("\") + "\"
  $requirementsCompatRoot = Join-Path $workDir "requirements"
  Copy-Item -LiteralPath $requirementsSourceRoot -Destination $requirementsCompatRoot -Recurse

  if ($requirementsFile.Equals($engineRequirementsFile, [System.StringComparison]::OrdinalIgnoreCase)) {
    $installRequirementsFile = Join-Path $workDir "requirements.txt"
    Copy-Item -LiteralPath $requirementsFile -Destination $installRequirementsFile
  } elseif ($requirementsFile.StartsWith($requirementsSourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    $requirementsRelativePath = [System.IO.Path]::GetRelativePath($requirementsSourceRoot, $requirementsFile)
    $installRequirementsFile = Join-Path $requirementsCompatRoot $requirementsRelativePath
  } else {
    throw "Windows ARM64 compatibility requires an Engine-managed requirements file: $requirementsFile"
  }

  $compatBaseFile = Join-Path $requirementsCompatRoot "base.txt"
  $checkpointRequirementPattern = '^\s*langgraph-checkpoint-sqlite>=3\.1\.0,<4\s*$'
  $compatBaseLines = @(Get-Content -LiteralPath $compatBaseFile)
  $checkpointRequirementMatches = @($compatBaseLines | Where-Object { $_ -match $checkpointRequirementPattern })
  if ($checkpointRequirementMatches.Count -ne 1) {
    throw "Expected exactly one LangGraph SQLite checkpoint requirement, found $($checkpointRequirementMatches.Count)"
  }
  $filteredBaseLines = @($compatBaseLines | Where-Object { $_ -notmatch $checkpointRequirementPattern })
  Set-Content -LiteralPath $compatBaseFile -Value $filteredBaseLines -Encoding ascii
  Write-Host "ARM64 install requirements: $installRequirementsFile"
}

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

$expectedMachine = if ($Architecture -eq "arm64") { "arm64" } else { "amd64" }
Invoke-Checked -FilePath $pythonExe -Arguments @("-c", "import platform; machine = platform.machine().lower(); print(machine); assert machine == '$expectedMachine', (machine, '$expectedMachine')")

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
$packagingToolRequirements = if ($Architecture -eq "arm64") {
  @("pip", "setuptools==82.0.1", "wheel==0.46.3")
} else {
  @("pip", "setuptools", "wheel")
}
$packagingToolArguments = @(
  "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade"
) + $packagingToolRequirements
Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments $packagingToolArguments -Description "Upgrade portable Python packaging tools"

if ($Architecture -eq "arm64") {
  # tiktoken does not publish a win_arm64 wheel. Build the audited release with
  # the native ARM64 Rust toolchain and the matching official Python import lib.
  $pythonDevZipPath = Join-Path $workDir "pythonarm64-dev.zip"
  $pythonDevRoot = Join-Path $workDir "pythonarm64-dev"
  $pythonDevUrl = "https://api.nuget.org/v3-flatcontainer/pythonarm64/$PythonVersion/pythonarm64.$PythonVersion.nupkg"
  $pythonDevSha256 = "2F5B3BEE38850FDDE1B44227A23B8130D329839558376D2EB11099CE2B2CC33C"
  Invoke-WebRequestWithRetry -Uri $pythonDevUrl -OutFile $pythonDevZipPath
  $downloadedPythonDevSha256 = (Get-FileHash -LiteralPath $pythonDevZipPath -Algorithm SHA256).Hash
  if ($downloadedPythonDevSha256 -ne $pythonDevSha256) {
    throw "Python ARM64 development package checksum mismatch: $downloadedPythonDevSha256"
  }
  Expand-Archive -LiteralPath $pythonDevZipPath -DestinationPath $pythonDevRoot -Force
  $pythonDevTools = Join-Path $pythonDevRoot "tools"
  $pythonDevInclude = Join-Path $pythonDevTools "include"
  $pythonDevImportLib = Join-Path $pythonDevTools "libs\python311.lib"
  $pythonDevStableAbiImportLib = Join-Path $pythonDevTools "libs\python3.lib"
  if (
    -not (Test-Path (Join-Path $pythonDevInclude "Python.h")) -or
    -not (Test-Path $pythonDevImportLib) -or
    -not (Test-Path $pythonDevStableAbiImportLib)
  ) {
    throw "Python ARM64 development package is missing Python.h, python311.lib, or python3.lib"
  }
  $runtimeInclude = Join-Path $runtimeDir "include"
  $runtimeLibs = Join-Path $runtimeDir "libs"
  Copy-Item -LiteralPath $pythonDevInclude -Destination $runtimeInclude -Recurse
  New-Item -ItemType Directory -Force -Path $runtimeLibs | Out-Null
  Copy-Item -LiteralPath $pythonDevImportLib -Destination (Join-Path $runtimeLibs "python311.lib")
  Copy-Item -LiteralPath $pythonDevStableAbiImportLib -Destination (Join-Path $runtimeLibs "python3.lib")

  $wheelhouse = Join-Path $workDir "wheelhouse"
  New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "setuptools-rust==1.13.0"
  ) -Description "Install Windows ARM64 tiktoken build frontend"
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "wheel", "--disable-pip-version-check", "--no-input", "--no-deps",
    "--no-build-isolation", "--wheel-dir", $wheelhouse, "tiktoken==0.13.0"
  ) -Description "Build native Windows ARM64 tiktoken wheel"
  $tiktokenWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "tiktoken-0.13.0-*.whl")
  if ($tiktokenWheels.Count -ne 1 -or $tiktokenWheels[0].Name -notmatch '-win_arm64\.whl$') {
    throw "Expected exactly one native win_arm64 tiktoken wheel, found: $($tiktokenWheels.Name -join ', ')"
  }
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps", "--no-index",
    "--find-links", $wheelhouse, "tiktoken==0.13.0"
  ) -Description "Install native Windows ARM64 tiktoken wheel"
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-c", "import platform, tiktoken._tiktoken; assert platform.machine().lower() == 'arm64'; print('V8OS_ARM64_TIKTOKEN_OK')"
  )

  # Chroma 1.5.9 does not publish a win_arm64 wheel. Build its current Rust
  # backend from the official sdist instead of allowing pip to select 0.6.x.
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "numpy==2.4.6", "maturin==1.14.1"
  ) -Description "Install Windows ARM64 Chroma build frontends"

  $protocVersion = "35.1"
  $protocZipPath = Join-Path $workDir "protoc-$protocVersion-win64.zip"
  $protocRoot = Join-Path $workDir "protoc-$protocVersion"
  $protocUrl = "https://github.com/protocolbuffers/protobuf/releases/download/v$protocVersion/protoc-$protocVersion-win64.zip"
  $protocSha256 = "5D3FF218D7D91EEA95F7569BCB5A98F3030F8996D44151279D9772EDCFF76082"
  Invoke-WebRequestWithRetry -Uri $protocUrl -OutFile $protocZipPath
  $downloadedProtocSha256 = (Get-FileHash -LiteralPath $protocZipPath -Algorithm SHA256).Hash
  if ($downloadedProtocSha256 -ne $protocSha256) {
    throw "Protocol Buffers compiler checksum mismatch: $downloadedProtocSha256"
  }
  Expand-Archive -LiteralPath $protocZipPath -DestinationPath $protocRoot -Force
  $protocExe = Join-Path $protocRoot "bin\protoc.exe"
  $protocInclude = Join-Path $protocRoot "include"
  if (
    -not (Test-Path $protocExe) -or
    -not (Test-Path (Join-Path $protocInclude "google\protobuf\descriptor.proto"))
  ) {
    throw "Protocol Buffers compiler package is missing protoc.exe or standard includes"
  }
  Invoke-Checked -FilePath $protocExe -Arguments @("--version")

  $arm64Clang = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\*\*\VC\Tools\Llvm\ARM64\bin\clang.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $arm64Clang) {
    throw "ARM64 clang.exe was not found in the Visual Studio LLVM toolchain"
  }
  $arm64ClangDir = Split-Path -Parent $arm64Clang.FullName
  Invoke-Checked -FilePath $arm64Clang.FullName -Arguments @("--version")

  $chromaSdistPath = Join-Path $workDir "chromadb-1.5.9.tar.gz"
  $chromaSdistUrl = "https://files.pythonhosted.org/packages/92/d1/5e33b26985f0c7046a0be1cee2158ada1748ee700d2545057fde1468d74d/chromadb-1.5.9.tar.gz"
  $chromaSdistSha256 = "5C20E62A455C28BACAC927F26116A73FD8E1799E0D908BE8E8A4F02197A54731"
  Invoke-WebRequestWithRetry -Uri $chromaSdistUrl -OutFile $chromaSdistPath
  $downloadedChromaSdistSha256 = (Get-FileHash -LiteralPath $chromaSdistPath -Algorithm SHA256).Hash
  if ($downloadedChromaSdistSha256 -ne $chromaSdistSha256) {
    throw "Chroma source distribution checksum mismatch: $downloadedChromaSdistSha256"
  }
  $chromaSourceRoot = Join-Path $workDir "chromadb-1.5.9"
  Invoke-Checked -FilePath "tar.exe" -Arguments @("-xzf", $chromaSdistPath, "-C", $workDir)
  $chromaCargoLock = Join-Path $chromaSourceRoot "Cargo.lock"
  if (
    -not (Test-Path (Join-Path $chromaSourceRoot "pyproject.toml")) -or
    -not (Test-Path $chromaCargoLock)
  ) {
    throw "Chroma source distribution is missing pyproject.toml or Cargo.lock"
  }

  # Chroma 1.5.9 locks generator 0.8.8, whose published crate omits its
  # Windows ARM64 implementation. Patch only that audited lock entry to the
  # upstream 0.8.9 release, which added and tests Windows ARM64 support.
  $generatorLockLines = @(
    '[[package]]',
    'name = "generator"',
    'version = "0.8.8"',
    'source = "registry+https://github.com/rust-lang/crates.io-index"',
    'checksum = "52f04ae4152da20c76fe800fa48659201d5cf627c5149ca0b707b69d7eef6cf9"'
  )
  $generatorCompatLockLines = @(
    '[[package]]',
    'name = "generator"',
    'version = "0.8.9"',
    'source = "registry+https://github.com/rust-lang/crates.io-index"',
    'checksum = "b3b854b0e584ead1a33f18b2fcad7cf7be18b3875c78816b753639aa501513ae"'
  )
  $chromaCargoLockText = [System.IO.File]::ReadAllText($chromaCargoLock)
  $cargoLockNewLine = if ($chromaCargoLockText.Contains("`r`n")) { "`r`n" } else { "`n" }
  $generatorLockBlock = $generatorLockLines -join $cargoLockNewLine
  $generatorCompatLockBlock = $generatorCompatLockLines -join $cargoLockNewLine
  $generatorLockIndex = $chromaCargoLockText.IndexOf($generatorLockBlock, [System.StringComparison]::Ordinal)
  if (
    $generatorLockIndex -lt 0 -or
    $generatorLockIndex -ne $chromaCargoLockText.LastIndexOf($generatorLockBlock, [System.StringComparison]::Ordinal)
  ) {
    throw "Expected exactly one audited generator 0.8.8 entry in Chroma Cargo.lock"
  }
  $patchedChromaCargoLock = $chromaCargoLockText.Remove($generatorLockIndex, $generatorLockBlock.Length).Insert(
    $generatorLockIndex,
    $generatorCompatLockBlock
  )
  [System.IO.File]::WriteAllText(
    $chromaCargoLock,
    $patchedChromaCargoLock,
    [System.Text.UTF8Encoding]::new($false)
  )
  Write-Host "Pinned Chroma build dependency generator 0.8.9 for Windows ARM64"

  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-m", "pip", "wheel", "--disable-pip-version-check", "--no-input", "--no-deps",
    "--no-build-isolation", "--wheel-dir", $wheelhouse, $chromaSourceRoot
  ) -Environment @{
    PATH = "$(Join-Path $runtimeDir 'Scripts');$arm64ClangDir;$env:PATH"
    PROTOC = $protocExe
    PROTOC_INCLUDE = $protocInclude
    CARGO_NET_RETRY = "5"
    CFLAGS_aarch64_pc_windows_msvc = "-D_ARM64_=1"
  }
  $chromaWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "chromadb-1.5.9-*.whl")
  if ($chromaWheels.Count -ne 1 -or $chromaWheels[0].Name -notmatch '-win_arm64\.whl$') {
    throw "Expected exactly one native win_arm64 Chroma wheel, found: $($chromaWheels.Name -join ', ')"
  }
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps", "--no-index",
    "--find-links", $wheelhouse, "chromadb==1.5.9"
  ) -Description "Install native Windows ARM64 Chroma wheel"
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-c", "import platform, chromadb_rust_bindings; assert platform.machine().lower() == 'arm64'; print('V8OS_ARM64_CHROMA_NATIVE_OK')"
  )

  # Chroma's current dependency graph does not publish CPython 3.11 Windows
  # ARM64 wheels for grpcio, PyYAML, or httptools. Build fixed official source
  # distributions once, then force the resolver to consume only those wheels.
  $pyyamlSdistPath = Join-Path $workDir "pyyaml-6.0.3.tar.gz"
  $pyyamlSdistUrl = "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
  $pyyamlSdistSha256 = "D76623373421DF22FB4CF8817020CBB7EF15C725B9D5E45F17E189BFC384190F"
  Invoke-WebRequestWithRetry -Uri $pyyamlSdistUrl -OutFile $pyyamlSdistPath
  $downloadedPyyamlSdistSha256 = (Get-FileHash -LiteralPath $pyyamlSdistPath -Algorithm SHA256).Hash
  if ($downloadedPyyamlSdistSha256 -ne $pyyamlSdistSha256) {
    throw "PyYAML source distribution checksum mismatch: $downloadedPyyamlSdistSha256"
  }
  Invoke-Checked -FilePath "tar.exe" -Arguments @("-xzf", $pyyamlSdistPath, "-C", $workDir)
  $pyyamlSourceRoot = Join-Path $workDir "pyyaml-6.0.3"
  if (-not (Test-Path (Join-Path $pyyamlSourceRoot "pyproject.toml"))) {
    throw "PyYAML source distribution is missing pyproject.toml"
  }
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-m", "pip", "wheel", "--disable-pip-version-check", "--no-input", "--no-deps",
    "--no-build-isolation", "--wheel-dir", $wheelhouse, "."
  ) -Environment @{
    PYYAML_FORCE_LIBYAML = "0"
  } -WorkingDirectory $pyyamlSourceRoot
  $pyyamlWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "pyyaml-6.0.3-*.whl")
  if ($pyyamlWheels.Count -ne 1 -or $pyyamlWheels[0].Name -ne "pyyaml-6.0.3-py3-none-any.whl") {
    throw "Expected exactly one pure Python PyYAML 6.0.3 wheel, found: $($pyyamlWheels.Name -join ', ')"
  }

  $httptoolsSdistPath = Join-Path $workDir "httptools-0.8.0.tar.gz"
  $httptoolsSdistUrl = "https://files.pythonhosted.org/packages/43/e5/d471fcb0e14523fe1c3f4ba58ca52480e7bd70ad7109a3846bc75892f7fb/httptools-0.8.0.tar.gz"
  $httptoolsSdistSha256 = "6B2A32F18D97E16E90827D7A819FFA8DBD8CC245FC4E1FA9D1095B54EF4BD999"
  Invoke-WebRequestWithRetry -Uri $httptoolsSdistUrl -OutFile $httptoolsSdistPath
  $downloadedHttptoolsSdistSha256 = (Get-FileHash -LiteralPath $httptoolsSdistPath -Algorithm SHA256).Hash
  if ($downloadedHttptoolsSdistSha256 -ne $httptoolsSdistSha256) {
    throw "httptools source distribution checksum mismatch: $downloadedHttptoolsSdistSha256"
  }
  Invoke-Checked -FilePath "tar.exe" -Arguments @("-xzf", $httptoolsSdistPath, "-C", $workDir)
  $httptoolsSourceRoot = Join-Path $workDir "httptools-0.8.0"
  if (-not (Test-Path (Join-Path $httptoolsSourceRoot "pyproject.toml"))) {
    throw "httptools source distribution is missing pyproject.toml"
  }
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-m", "pip", "wheel", "--disable-pip-version-check", "--no-input", "--no-deps",
    "--no-build-isolation", "--wheel-dir", $wheelhouse, "."
  ) -WorkingDirectory $httptoolsSourceRoot
  $httptoolsWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "httptools-0.8.0-*.whl")
  if ($httptoolsWheels.Count -ne 1 -or $httptoolsWheels[0].Name -ne "httptools-0.8.0-cp311-cp311-win_arm64.whl") {
    throw "Expected exactly one native win_arm64 httptools 0.8.0 wheel, found: $($httptoolsWheels.Name -join ', ')"
  }

  # grpcio generates deeply nested object paths. Build from a deliberately
  # short source and TEMP root so MSVC stays below Windows path limits.
  $grpcShortRoot = Join-Path $tmpRoot "v8g"
  $grpcTempRoot = Join-Path $tmpRoot "v8t"
  if (Test-Path $grpcShortRoot) {
    Remove-Item -LiteralPath $grpcShortRoot -Recurse -Force
  }
  if (Test-Path $grpcTempRoot) {
    Remove-Item -LiteralPath $grpcTempRoot -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $grpcShortRoot, $grpcTempRoot | Out-Null
  $grpcSdistPath = Join-Path $grpcShortRoot "grpcio-1.83.0.tar.gz"
  $grpcSdistUrl = "https://files.pythonhosted.org/packages/0c/98/304898ac4e04e2d5e4e4c2eadc178b1f2a16d5f4bc2f91306c87d64680b9/grpcio-1.83.0.tar.gz"
  $grpcSdistSha256 = "7674587248FBBB2AC6E4EECF83A8A0F3D91A928F941DE571ACFD3A2F007FBC24"
  Invoke-WebRequestWithRetry -Uri $grpcSdistUrl -OutFile $grpcSdistPath
  $downloadedGrpcSdistSha256 = (Get-FileHash -LiteralPath $grpcSdistPath -Algorithm SHA256).Hash
  if ($downloadedGrpcSdistSha256 -ne $grpcSdistSha256) {
    throw "grpcio source distribution checksum mismatch: $downloadedGrpcSdistSha256"
  }
  Invoke-Checked -FilePath "tar.exe" -Arguments @("-xzf", $grpcSdistPath, "-C", $grpcShortRoot)
  $grpcSourceRoot = Join-Path $grpcShortRoot "grpcio-1.83.0"
  if (-not (Test-Path (Join-Path $grpcSourceRoot "pyproject.toml"))) {
    throw "grpcio source distribution is missing pyproject.toml"
  }
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-m", "pip", "wheel", "--disable-pip-version-check", "--no-input", "--no-deps",
    "--no-build-isolation", "--wheel-dir", $wheelhouse, "."
  ) -Environment @{
    GRPC_PYTHON_BUILD_WITH_CYTHON = "0"
    GRPC_PYTHON_BUILD_USE_SHORT_TEMP_DIR_NAME = "1"
    TEMP = $grpcTempRoot
    TMP = $grpcTempRoot
  } -WorkingDirectory $grpcSourceRoot
  $grpcWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "grpcio-1.83.0-*.whl")
  if ($grpcWheels.Count -ne 1 -or $grpcWheels[0].Name -ne "grpcio-1.83.0-cp311-cp311-win_arm64.whl") {
    throw "Expected exactly one native win_arm64 grpcio 1.83.0 wheel, found: $($grpcWheels.Name -join ', ')"
  }
  Remove-Item -LiteralPath $grpcShortRoot -Recurse -Force
  Remove-Item -LiteralPath $grpcTempRoot -Recurse -Force
  if ((Test-Path $grpcShortRoot) -or (Test-Path $grpcTempRoot)) {
    throw "Windows ARM64 grpcio build directories were not removed"
  }

  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps", "--no-index",
    "--find-links", $wheelhouse, "PyYAML==6.0.3", "httptools==0.8.0", "grpcio==1.83.0"
  )

  $arm64ConstraintsFile = Join-Path $workDir "arm64-constraints.txt"
  Set-Content -LiteralPath $arm64ConstraintsFile -Encoding ascii -Value @(
    "grpcio==1.83.0",
    "PyYAML==6.0.3",
    "httptools==0.8.0"
  )
}

$requirementsInstallArguments = @(
  "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--prefer-binary"
)
if ($Architecture -eq "arm64") {
  $requirementsInstallArguments += @(
    "--constraint", $arm64ConstraintsFile,
    "--find-links", $wheelhouse,
    "--only-binary=grpcio,PyYAML,httptools"
  )
}
$requirementsInstallArguments += @("-r", $installRequirementsFile)
Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments $requirementsInstallArguments -Description "Install desktop preview Engine requirements"
Invoke-Checked -FilePath $pythonExe -Arguments @(
  "-X", "utf8", "-c",
  "import curl_cffi; from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher; print('V8OS_RESEARCH_FETCHERS_OK')"
)

if ($Architecture -eq "arm64") {
  $arm64CompatibilityVersionProbe = @"
from importlib.metadata import version

expected = {
    "grpcio": "1.83.0",
    "PyYAML": "6.0.3",
    "httptools": "0.8.0",
}
for distribution, expected_version in expected.items():
    actual_version = version(distribution)
    assert actual_version == expected_version, (distribution, actual_version, expected_version)
print("V8OS_ARM64_COMPATIBILITY_VERSIONS_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $arm64CompatibilityVersionProbe)

  $arm64PeProbe = @"
import importlib
import platform
import struct
from pathlib import Path

assert platform.machine().lower() == "arm64"
for module_name in (
    "grpc._cython.cygrpc",
    "httptools.parser.parser",
    "httptools.parser.url_parser",
):
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__)
    with module_path.open("rb") as handle:
        handle.seek(0x3C)
        pe_offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(pe_offset)
        assert handle.read(4) == b"PE\0\0", module_path
        machine = struct.unpack("<H", handle.read(2))[0]
    assert machine == 0xAA64, (module_path, hex(machine))
print("V8OS_ARM64_COMPATIBILITY_PE_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $arm64PeProbe)

  $pyyamlProbe = @"
import yaml

payload = {"runtime": "windows-arm64", "values": [1, 2, 3]}
encoded = yaml.safe_dump(payload)
assert yaml.safe_load(encoded) == payload
assert yaml.__with_libyaml__ is False
print("V8OS_ARM64_PYYAML_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $pyyamlProbe)

  $httptoolsProbe = @"
from httptools import HttpRequestParser

class Protocol:
    def __init__(self):
        self.url = b""
        self.complete = False

    def on_message_begin(self):
        pass

    def on_url(self, url):
        self.url += url

    def on_header(self, name, value):
        pass

    def on_headers_complete(self):
        pass

    def on_body(self, body):
        pass

    def on_message_complete(self):
        self.complete = True

protocol = Protocol()
parser = HttpRequestParser(protocol)
parser.feed_data(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
assert protocol.url == b"/health"
assert protocol.complete is True
print("V8OS_ARM64_HTTPTOOLS_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $httptoolsProbe)

  $grpcProbe = @"
from concurrent import futures
import grpc

def ping(request, context):
    return b"pong:" + request

handler = grpc.unary_unary_rpc_method_handler(
    ping,
    request_deserializer=lambda value: value,
    response_serializer=lambda value: value,
)
server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
server.add_generic_rpc_handlers((
    grpc.method_handlers_generic_handler("v8os.Compatibility", {"Ping": handler}),
))
port = server.add_insecure_port("127.0.0.1:0")
assert port > 0
server.start()
try:
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        grpc.channel_ready_future(channel).result(timeout=10)
        call = channel.unary_unary(
            "/v8os.Compatibility/Ping",
            request_serializer=lambda value: value,
            response_deserializer=lambda value: value,
        )
        assert call(b"ping", timeout=10) == b"pong:ping"
finally:
    server.stop(0).wait()
print("V8OS_ARM64_GRPC_LOOPBACK_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $grpcProbe)

  Remove-Item -LiteralPath $runtimeInclude -Recurse -Force
  Remove-Item -LiteralPath $runtimeLibs -Recurse -Force
  if ((Test-Path $runtimeInclude) -or (Test-Path $runtimeLibs)) {
    throw "Windows ARM64 build-only Python development files were not removed"
  }
  Invoke-Checked -FilePath $pythonExe -Arguments @(
    "-c", "import tiktoken._tiktoken; print('V8OS_ARM64_TIKTOKEN_RUNTIME_OK')"
  )

  $chromaProbeRoot = Join-Path $workDir "chroma-persistence-probe"
  New-Item -ItemType Directory -Force -Path $chromaProbeRoot | Out-Null
  $chromaWriteProbe = @"
import sys
import chromadb

client = chromadb.PersistentClient(path=sys.argv[1])
collection = client.create_collection("arm64_probe")
vectors = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
collection.add(ids=["a", "b"], embeddings=vectors)
result = collection.query(query_embeddings=vectors, n_results=1)
assert result["ids"] == [["a"], ["b"]], result
print("V8OS_ARM64_CHROMA_WRITE_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $chromaWriteProbe, $chromaProbeRoot)
  $chromaReadProbe = @"
import sys
import chromadb

vectors = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
collection = chromadb.PersistentClient(path=sys.argv[1]).get_collection("arm64_probe")
result = collection.query(query_embeddings=vectors, n_results=1)
assert result["ids"] == [["a"], ["b"]], result
print("V8OS_ARM64_CHROMA_REOPEN_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $chromaReadProbe, $chromaProbeRoot)
  Remove-Item -LiteralPath $chromaProbeRoot -Recurse -Force

  # sqlite-vec does not publish a win_arm64 wheel. V8OS does not import that
  # extension; install the audited saver version and prove its SQLite path works.
  Invoke-CheckedWithRetry -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps",
    "langgraph-checkpoint-sqlite==3.1.1"
  ) -Description "Install Windows ARM64 LangGraph checkpoint saver compatibility pin"

  $pipCheckProbe = @"
import re
import subprocess
import sys
from packaging.utils import canonicalize_name

result = subprocess.run(
    [sys.executable, "-m", "pip", "check"],
    capture_output=True,
    text=True,
)
lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
missing_dependency = re.fullmatch(
    r"^(\S+)\s+(\S+)\s+requires\s+(\S+),\s+which\s+is\s+not\s+installed\.?$",
    lines[0],
    re.IGNORECASE,
) if len(lines) == 1 else None
known_gap = bool(
    result.returncode == 1
    and missing_dependency
    and canonicalize_name(missing_dependency.group(1)) == "langgraph-checkpoint-sqlite"
    and missing_dependency.group(2) == "3.1.1"
    and canonicalize_name(missing_dependency.group(3)) == "sqlite-vec"
)
if known_gap:
    print("V8OS_ARM64_PIP_CHECK_EXPECTED_GAP_ONLY")
else:
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    raise SystemExit("Unexpected dependency defect in Windows ARM64 Python runtime")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $pipCheckProbe)

  $checkpointProbe = @"
import asyncio
import importlib.util
import tempfile
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

assert importlib.util.find_spec("sqlite_vec") is None
config = {"configurable": {"thread_id": "v8os-arm64-probe", "checkpoint_ns": ""}}
metadata = {"source": "input", "step": 0, "parents": {}}

async def async_probe(database_path):
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        stored_config = await saver.aput(config, empty_checkpoint(), metadata, {})
        loaded = await saver.aget_tuple(stored_config)
        assert loaded and loaded.checkpoint["id"] == stored_config["configurable"]["checkpoint_id"]
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        loaded = await saver.aget_tuple(stored_config)
        assert loaded and loaded.checkpoint["id"] == stored_config["configurable"]["checkpoint_id"]

with tempfile.TemporaryDirectory(prefix="v8os-arm64-checkpoint-") as tmp_dir:
    root = Path(tmp_dir)
    with SqliteSaver.from_conn_string(str(root / "sync.db")) as saver:
        stored_config = saver.put(config, empty_checkpoint(), metadata, {})
        loaded = saver.get_tuple(stored_config)
        assert loaded and loaded.checkpoint["id"] == stored_config["configurable"]["checkpoint_id"]
    with SqliteSaver.from_conn_string(str(root / "sync.db")) as saver:
        loaded = saver.get_tuple(stored_config)
        assert loaded and loaded.checkpoint["id"] == stored_config["configurable"]["checkpoint_id"]
    asyncio.run(async_probe(root / "async.db"))

print("V8OS_ARM64_CHECKPOINT_SQLITE_OK")
"@
  Invoke-Checked -FilePath $pythonExe -Arguments @("-X", "utf8", "-c", $checkpointProbe)
}

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
