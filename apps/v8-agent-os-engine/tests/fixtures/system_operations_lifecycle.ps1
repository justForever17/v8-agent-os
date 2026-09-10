[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$AllowSideEffects,
    [Parameter(Mandatory = $true)][string]$ClientSid,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64'
)
$ErrorActionPreference = 'Stop'
if (!$Live -or !$AllowSideEffects) { throw 'Explicit -Live -AllowSideEffects required.' }
$report = [IO.Path]::GetFullPath($ReportPath)
$temporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (!$report.StartsWith($temporary, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($report) -notlike 'v8-system-lifecycle-*.json' -or (Test-Path -LiteralPath $report)) { throw 'Use a new owned temporary report path.' }
$native = Join-Path (Split-Path (Split-Path $PSScriptRoot)) 'native'
$providerRoot = 'HKLM:/SOFTWARE/Microsoft/Windows/CurrentVersion/Authentication/Credential Providers'
$ownProvider = '{793345F6-C96B-472A-A780-387839440068}'
function SystemProviders {
    @(Get-ChildItem -LiteralPath $providerRoot | Where-Object PSChildName -NE $ownProvider | ForEach-Object { $_.PSChildName + '=' + $_.GetValue('') } | Sort-Object) -join "`n"
}
$before = SystemProviders
$checks = [ordered]@{ unregistered = $false; serviceRemoved = $false; restored = $false; systemProvidersUnchanged = $false }
try {
    & (Join-Path $native 'v8-system-operations/install.ps1') -Action Uninstall -Arch $Arch -Unattended | Out-Null
    & (Join-Path $native 'v8-session-unlock/install.ps1') -Action Uninstall -Arch $Arch -Unattended | Out-Null
    $checks.unregistered = !(Test-Path -LiteralPath "$providerRoot/$ownProvider")
    $checks.serviceRemoved = !(Get-Service -Name V8SystemOperations -ErrorAction SilentlyContinue)
    if (!$checks.unregistered -or !$checks.serviceRemoved) { throw 'Removal did not complete.' }
} finally {
    # Restore the same tested component and caller binding even if the negative
    # verification fails. Credentials are owned by Engine and never touched.
    & (Join-Path $native 'v8-system-operations/install.ps1') -Action Install -Arch $Arch -ClientSid $ClientSid -Unattended | Out-Null
    & (Join-Path $native 'v8-session-unlock/install.ps1') -Action Install -Arch $Arch -Unattended | Out-Null
    $checks.restored = (Test-Path -LiteralPath "$providerRoot/$ownProvider") -and ((Get-Service -Name V8SystemOperations).Status -eq 'Running')
    $checks.systemProvidersUnchanged = $before -eq (SystemProviders)
    $checks | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding UTF8
    $reportAcl = Get-Acl -LiteralPath $report
    $callerSid = New-Object Security.Principal.SecurityIdentifier($ClientSid)
    $reportAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($callerSid, 'ReadAndExecute', 'Allow')))
    Set-Acl -LiteralPath $report -AclObject $reportAcl
}
if ($checks.Values -contains $false) { throw 'Lifecycle verification failed; inspect the bounded report.' }
