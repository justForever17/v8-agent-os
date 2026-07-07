param(
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$current = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $current) { $current = "" }
$parts = $current -split ";" | Where-Object { $_ -ne "" }
$repoNormalized = $repoRoot.Path.TrimEnd([char]'\')
$alreadyPresent = $parts | Where-Object { [String]::Equals($_.TrimEnd([char]'\'), $repoNormalized, [StringComparison]::OrdinalIgnoreCase) }

if ($alreadyPresent) {
  Write-Host "V8OS repo root is already on the user PATH:"
  Write-Host "  $($repoRoot.Path)"
  exit 0
}

Write-Host "V8OS PATH helper"
Write-Host "Will add this directory to the user PATH:"
Write-Host "  $($repoRoot.Path)"

if (-not $Apply) {
  Write-Host ""
  Write-Host "Dry run only. Re-run with -Apply to update the user PATH."
  exit 0
}

$next = if ($current.Trim()) { "$current;$($repoRoot.Path)" } else { $repoRoot.Path }
[Environment]::SetEnvironmentVariable("Path", $next, "User")
Write-Host "Updated user PATH. Open a new terminal and run: v8os status"
