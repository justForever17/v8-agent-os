[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Install', 'Uninstall')][string]$Action,
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64',
    [string]$ClientSid = '',
    [switch]$Unattended
)
$ErrorActionPreference = 'Stop'
if ($Unattended) { $ConfirmPreference = 'None' }
$name = 'V8SystemOperations'
$settings = 'HKLM:/SOFTWARE/V8AgentOS/SystemOperations'
$directory = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'V8AgentOS/SystemOperations'
$destination = Join-Path $directory 'v8-system-operations.exe'
$source = Join-Path $PSScriptRoot "build/$Arch/v8-system-operations.exe"
$image = '"' + $destination + '" --service'
if (!$PSCmdlet.ShouldProcess($directory, "$Action only the V8OS SystemOperations broker; bind the explicitly supplied client SID")) { return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -or ![Environment]::Is64BitProcess) { throw 'An explicitly elevated native 64-bit PowerShell is required for this installation operation.' }
foreach ($path in @((Split-Path $directory), $directory)) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing a redirected component directory.' }
}
$service = Get-Service -Name $name -ErrorAction SilentlyContinue
if ($service) {
    $registeredImage = (Get-ItemProperty -LiteralPath "HKLM:/SYSTEM/CurrentControlSet/Services/$name").ImagePath
    if ($registeredImage -ne $image) { throw 'A service with this name points elsewhere; refusing to modify it.' }
}
if ($Action -eq 'Uninstall') {
    if ($service) {
        if ($service.Status -ne 'Stopped') {
            Stop-Service -Name $name
            $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(15))
        }
        & (Join-Path $env:SystemRoot 'System32/sc.exe') delete $name | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The component service could not be deleted.' }
    }
    if (Test-Path -LiteralPath $settings) { Remove-Item -LiteralPath $settings -Recurse -Force }
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
    @{ ok = $true; status = 'uninstalled'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
    return
}
if (!$ClientSid -or $ClientSid -in @('S-1-5-18', 'S-1-5-19', 'S-1-5-20')) { throw 'Specify the non-service SID of the actual Engine user; do not infer it from the elevated installer account.' }
$boundSid = New-Object Security.Principal.SecurityIdentifier($ClientSid)
$null = $boundSid.Translate([Security.Principal.NTAccount])
$machineArch = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
if (($machineArch -eq 'ARM64' -and $Arch -ne 'arm64') -or ($machineArch -eq 'AMD64' -and $Arch -ne 'x64')) { throw 'Component architecture must match the native OS architecture.' }
if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Build the matching architecture before installation.' }
$pe = [IO.File]::ReadAllBytes($source)
if ($pe.Length -lt 256) { throw 'Invalid PE component.' }
$offset = [BitConverter]::ToInt32($pe, 0x3c)
$expected = if ($Arch -eq 'arm64') { 0xaa64 } else { 0x8664 }
if ($offset -lt 0 -or $offset + 6 -ge $pe.Length -or [BitConverter]::ToUInt32($pe, $offset) -ne 0x4550 -or [BitConverter]::ToUInt16($pe, $offset + 4) -ne $expected) { throw 'PE architecture does not match.' }
if ($service) {
    $oldSid = (Get-ItemProperty -LiteralPath $settings).ClientSid
    if ($oldSid -ne $ClientSid -or !(Test-Path -LiteralPath $destination)) { throw 'Explicitly uninstall the old account binding before replacement.' }
    if ((Get-FileHash -LiteralPath $source).Hash -eq (Get-FileHash -LiteralPath $destination).Hash) {
        Start-Service -Name $name
        @{ ok = $true; status = 'already_installed'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
        return
    }
    # Explicit repair updates only this installed service, with the previous
    # binary retained until the new service is confirmed running.
    $backup = Join-Path $directory 'v8-system-operations.previous.exe'
    if (Test-Path -LiteralPath $backup) { throw 'A previous recovery image remains; resolve it before another update.' }
    Copy-Item -LiteralPath $destination -Destination $backup
    try {
        Stop-Service -Name $name
        (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(15))
        Copy-Item -LiteralPath $source -Destination $destination -Force
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(15))
    } catch {
        $updateErrorCode = $_.Exception.HResult -band 0xffff
        Stop-Service -Name $name -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $backup -Destination $destination -Force
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(15))
        Remove-Item -LiteralPath $backup -Force
        Write-Output 'Component update failed; the previous service image was restored and started.'
        exit (65536 + $updateErrorCode)
    }
    Remove-Item -LiteralPath $backup -Force
    @{ ok = $true; status = 'updated'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
    return
}
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'), @('S-1-5-32-545', 'ReadAndExecute'))) {
    $sid = New-Object Security.Principal.SecurityIdentifier($rule[0])
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, $rule[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}
Set-Acl -LiteralPath $directory -AclObject $acl
$createdService = $false
try {
    Copy-Item -LiteralPath $source -Destination $destination -Force
    New-Item -Path "$settings/Replay" -Force | Out-Null
    $registryAcl = New-Object Security.AccessControl.RegistrySecurity
    $registryAcl.SetAccessRuleProtection($true, $false)
    foreach ($sidText in @('S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object Security.Principal.SecurityIdentifier($sidText)
        $registryAcl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($sid, 'FullControl', 'ContainerInherit', 'None', 'Allow')))
    }
    $registryAcl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($boundSid, 'ReadKey', 'None', 'None', 'Allow')))
    Set-Acl -LiteralPath $settings -AclObject $registryAcl
    New-ItemProperty -LiteralPath $settings -Name 'ClientSid' -Value $ClientSid -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $settings -Name 'ClientPath' -Value $destination -PropertyType String -Force | Out-Null
    New-Service -Name $name -BinaryPathName $image -DisplayName 'V8OS Controlled System Operations' -StartupType Automatic | Out-Null
    $createdService = $true
    Start-Service -Name $name
    (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(15))
    @{ ok = $true; status = 'installed'; systemProvidersChanged = $false } | ConvertTo-Json -Compress
} catch {
    if ($createdService) {
        Stop-Service -Name $name -ErrorAction SilentlyContinue
        & (Join-Path $env:SystemRoot 'System32/sc.exe') delete $name | Out-Null
    }
    if (Test-Path -LiteralPath $settings) { Remove-Item -LiteralPath $settings -Recurse -Force }
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue }
    throw 'Installation failed; the new component service and configuration were rolled back. Existing Windows sign-in providers were not changed.'
}
