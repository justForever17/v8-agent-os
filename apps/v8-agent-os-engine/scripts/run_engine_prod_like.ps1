param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000,
    [string]$PythonPath = "",
    [string]$LogDir = "",
    [switch]$UseSystemPython
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$targetScript = Join-Path $PSScriptRoot "runtime\\run_engine_prod_like.ps1"
if (-not (Test-Path $targetScript)) {
    throw "Target script not found: $targetScript"
}

& $targetScript @PSBoundParameters
$exitCode = $LASTEXITCODE
if ($null -ne $exitCode -and $exitCode -ne 0) {
    exit $exitCode
}
