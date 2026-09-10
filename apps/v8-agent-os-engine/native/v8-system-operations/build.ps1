[CmdletBinding()]
param(
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64',
    [string]$BuildTools = ''
)
$ErrorActionPreference = 'Stop'
if (!$BuildTools) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio Installer discovery is unavailable; specify -BuildTools.' }
    $BuildTools = & $vswhere -latest -products '*' -property installationPath
    if (!$BuildTools) { throw 'No installed Visual Studio C++ toolchain was found.' }
}
$vcVersion = (Get-ChildItem -LiteralPath (Join-Path $BuildTools 'VC/Tools/MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1).FullName
$sdkRoot = 'C:/Program Files (x86)/Windows Kits/10'
$sdkVersion = (Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Include') -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'um/credentialprovider.h') } | Sort-Object Name -Descending | Select-Object -First 1).Name
$compilerHost = if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64') { 'Hostarm64' } else { 'Hostx64' }
$compiler = Join-Path $vcVersion "bin/$compilerHost/$Arch/cl.exe"
if (!(Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $vcVersion "bin/Hostx64/$Arch/cl.exe" }
if (!(Test-Path -LiteralPath $compiler) -or !$sdkVersion) { throw 'MSVC target compiler or Windows SDK is unavailable.' }
$output = Join-Path $PSScriptRoot "build/$Arch"
New-Item -ItemType Directory -Path $output -Force | Out-Null
$previousInclude = $env:INCLUDE
$previousLib = $env:LIB
$previousPath = $env:PATH
try {
    $env:INCLUDE = @((Join-Path $vcVersion 'include'), (Join-Path $sdkRoot "Include/$sdkVersion/ucrt"), (Join-Path $sdkRoot "Include/$sdkVersion/shared"), (Join-Path $sdkRoot "Include/$sdkVersion/um"), (Join-Path $sdkRoot "Include/$sdkVersion/winrt")) -join ';'
    $env:LIB = @((Join-Path $vcVersion "lib/$Arch"), (Join-Path $sdkRoot "Lib/$sdkVersion/ucrt/$Arch"), (Join-Path $sdkRoot "Lib/$sdkVersion/um/$Arch")) -join ';'
    $env:PATH = (Split-Path $compiler) + ';' + $env:PATH
    Push-Location $output
    try {
        $common = @('/nologo', '/std:c++17', '/EHsc', '/W4', '/WX', '/O2', '/MT', '/utf-8', '/DUNICODE', '/D_UNICODE', '/D_WIN32_WINNT=0x0A00', '/guard:cf')
        $libraries = @('/guard:cf', 'advapi32.lib', 'wtsapi32.lib', 'ole32.lib', 'secur32.lib', 'credui.lib', 'shlwapi.lib', 'uuid.lib', 'user32.lib')
        & $compiler @common (Join-Path $PSScriptRoot 'client.cpp') (Join-Path $PSScriptRoot 'protocol.cpp') (Join-Path $PSScriptRoot 'service.cpp') (Join-Path $PSScriptRoot 'worker.cpp') (Join-Path $PSScriptRoot '../v8-native-common/windows.cpp') /Fe:v8-system-operations.exe /link /DYNAMICBASE /NXCOMPAT userenv.lib @libraries
        if ($LASTEXITCODE -ne 0) { throw 'System operations component build failed.' }
        & $compiler @common (Join-Path $PSScriptRoot 'protocol_tests.cpp') (Join-Path $PSScriptRoot 'protocol.cpp') (Join-Path $PSScriptRoot '../v8-native-common/windows.cpp') /Fe:protocol-tests.exe /link /DYNAMICBASE /NXCOMPAT shell32.lib @libraries
        if ($LASTEXITCODE -ne 0) { throw 'Protocol tests build failed.' }
    } finally { Pop-Location }
    Write-Output "Built $Arch system operations client/service and non-installing protocol tests in $output"
} finally {
    $env:INCLUDE = $previousInclude
    $env:LIB = $previousLib
    $env:PATH = $previousPath
}
