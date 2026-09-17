param(
    [Parameter(Mandatory = $true)][string]$PublicTestCa
)
$ErrorActionPreference = 'Stop'
$phoneRoot = Split-Path -Parent $PSScriptRoot
$ca = (Resolve-Path -LiteralPath $PublicTestCa).Path
$contents = [IO.File]::ReadAllText($ca)
if ($contents -notmatch 'BEGIN CERTIFICATE' -or $contents -match 'PRIVATE KEY') {
    throw 'Supply only the public PEM CA certificate from the isolated live fixture.'
}
$target = Join-Path $phoneRoot 'modules/v8-device-executor/android/src/androidTest/res/raw'
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -LiteralPath $ca -Destination (Join-Path $target 'v8_executor_test_ca.pem')
Write-Output 'Public test CA prepared for the Android instrumentation APK only. Production trust is unchanged.'
