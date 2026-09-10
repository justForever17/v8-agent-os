[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Install', 'Uninstall')][string]$Action,
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64',
    [switch]$Unattended
)
$ErrorActionPreference = 'Stop'
# Admin has already selected the action and Windows still requires UAC. Avoid
# a second console prompt; Windows PowerShell -File cannot bind -Confirm:$false.
if ($Unattended) { $ConfirmPreference = 'None' }
$clsid = '{793345F6-C96B-472A-A780-387839440068}'
$providerKey = "HKLM:/SOFTWARE/Microsoft/Windows/CurrentVersion/Authentication/Credential Providers/$clsid"
$classKey = "HKLM:/SOFTWARE/Classes/CLSID/$clsid"
$settingsKey = 'HKLM:/SOFTWARE/V8AgentOS/SessionUnlock'
$destination = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'V8AgentOS/SessionUnlock'
$source = Join-Path $PSScriptRoot "build/$Arch"
$files = @('V8SessionUnlock.dll', 'v8-session-unlock.exe')

if (!$PSCmdlet.ShouldProcess($destination, "$Action only the V8OS SessionUnlock provider ($clsid); preserve every Windows sign-in provider")) { return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this explicit installation operation from an elevated 64-bit PowerShell.' }
if (![Environment]::Is64BitProcess) { throw 'A native 64-bit PowerShell process is required.' }
$machineArch = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
if (($machineArch -eq 'ARM64' -and $Arch -ne 'arm64') -or ($machineArch -eq 'AMD64' -and $Arch -ne 'x64')) { throw 'Provider architecture must match the native OS architecture.' }
foreach ($entry in @((Split-Path $destination), $destination)) {
    if ((Test-Path -LiteralPath $entry) -and ((Get-Item -LiteralPath $entry).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing a redirected component install directory.' }
}

if ($Action -eq 'Uninstall') {
    # Unregister first. A loaded DLL can remain on disk until LogonUI exits; do not
    # terminate LogonUI or Windows authentication processes to remove it.
    foreach ($key in @($providerKey, $classKey, $settingsKey)) {
        if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key -Recurse -Force }
    }
    $retained = @()
    foreach ($name in $files) {
        $target = Join-Path $destination $name
        if (Test-Path -LiteralPath $target) {
            try { Remove-Item -LiteralPath $target -Force } catch { $retained += $name }
        }
    }
    @{ ok = $true; status = 'unregistered'; retainedUntilLogonUiExit = $retained; systemProvidersChanged = $false } | ConvertTo-Json -Compress
    return
}

foreach ($name in $files) {
    $file = Join-Path $source $name
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing built component: $name" }
    $bytes = [IO.File]::ReadAllBytes($file)
    if ($bytes.Length -lt 256) { throw 'Invalid PE component.' }
    $pe = [BitConverter]::ToInt32($bytes, 0x3c)
    $expected = if ($Arch -eq 'arm64') { 0xaa64 } else { 0x8664 }
    if ($pe -lt 0 -or $pe + 6 -ge $bytes.Length -or [BitConverter]::ToUInt32($bytes, $pe) -ne 0x4550 -or [BitConverter]::ToUInt16($bytes, $pe + 4) -ne $expected) { throw 'PE architecture does not match requested installation.' }
}
$hadProvider = Test-Path -LiteralPath $providerKey
if ($hadProvider) {
    $changed = @()
    foreach ($name in $files) {
        $existing = Join-Path $destination $name
        if (!(Test-Path -LiteralPath $existing)) { throw 'An incomplete component is registered; uninstall it before repair.' }
        if ((Get-FileHash -LiteralPath $existing).Hash -ne (Get-FileHash -LiteralPath (Join-Path $source $name)).Hash) { $changed += $name }
    }
    if (!$changed.Count) {
        @{ ok = $true; status = 'already_installed'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
        return
    }
    $registeredDll = (Get-Item -LiteralPath "$classKey/InprocServer32").GetValue('')
    if ($registeredDll -ne (Join-Path $destination 'V8SessionUnlock.dll')) { throw 'This provider points to another component; refusing replacement.' }
    foreach ($name in $changed) {
        $backup = Join-Path $destination "$name.previous"
        if (Test-Path -LiteralPath $backup) { throw 'A prior recovery copy remains; resolve it before updating.' }
        Copy-Item -LiteralPath (Join-Path $destination $name) -Destination $backup
    }
    $replaced = @()
    try {
        foreach ($name in $changed) {
            Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $destination $name) -Force
            $replaced += $name
        }
    } catch {
        foreach ($name in $replaced) { Copy-Item -LiteralPath (Join-Path $destination "$name.previous") -Destination (Join-Path $destination $name) -Force }
        foreach ($name in $changed) { Remove-Item -LiteralPath (Join-Path $destination "$name.previous") -Force }
        throw 'Provider update failed; prior files were restored. Sign in normally before retrying if LogonUI still holds the DLL.'
    }
    foreach ($name in $changed) { Remove-Item -LiteralPath (Join-Path $destination "$name.previous") -Force }
    @{ ok = $true; status = 'updated'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
    return
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'), @('S-1-5-32-545', 'ReadAndExecute'))) {
    $sid = New-Object Security.Principal.SecurityIdentifier($rule[0])
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, $rule[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}
Set-Acl -LiteralPath $destination -AclObject $acl
try {
    foreach ($name in $files) { Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $destination $name) -Force }
    New-Item -Path "$settingsKey/Replay" -Force | Out-Null
    $registryAcl = New-Object Security.AccessControl.RegistrySecurity
    $registryAcl.SetAccessRuleProtection($true, $false)
    foreach ($sidText in @('S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object Security.Principal.SecurityIdentifier($sidText)
        $registryAcl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($sid, 'FullControl', 'ContainerInherit', 'None', 'Allow')))
    }
    $users = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
    $registryAcl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($users, 'ReadKey', 'None', 'None', 'Allow')))
    Set-Acl -LiteralPath $settingsKey -AclObject $registryAcl
    New-ItemProperty -LiteralPath $settingsKey -Name 'ClientPath' -Value (Join-Path $destination 'v8-session-unlock.exe') -PropertyType String -Force | Out-Null
    New-Item -Path "$classKey/InprocServer32" -Force | Out-Null
    Set-Item -LiteralPath "$classKey/InprocServer32" -Value (Join-Path $destination 'V8SessionUnlock.dll')
    New-ItemProperty -LiteralPath "$classKey/InprocServer32" -Name 'ThreadingModel' -Value 'Apartment' -PropertyType String -Force | Out-Null
    New-Item -Path $providerKey -Force | Out-Null
    Set-Item -LiteralPath $providerKey -Value 'V8OS Authorized Session Unlock'
    @{ ok = $true; status = 'installed'; systemProvidersChanged = $false; clientPath = (Join-Path $destination 'v8-session-unlock.exe') } | ConvertTo-Json -Compress
} catch {
    foreach ($key in @($providerKey, $classKey, $settingsKey)) {
        if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key -Recurse -Force }
    }
    foreach ($name in $files) {
        $target = Join-Path $destination $name
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
    }
    throw 'Component installation failed and its own registration was rolled back; system providers were not changed.'
}
