[CmdletBinding(SupportsShouldProcess = $true)]
param([Parameter(Mandatory = $true)][string]$EngineRoot, [switch]$DryRun, [switch]$Elevated)
$ErrorActionPreference = 'Stop'

function Get-V8SystemComponentPresence {
    $root = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        # A 32-bit NSIS/helper sees Program Files (x86) through SpecialFolder.
        # Component registrations and native installers use the 64-bit OS root.
        $currentVersion = $root.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion')
        if ($null -eq $currentVersion) { throw 'The native Program Files location is unavailable.' }
        try { $programFiles = [string]$currentVersion.GetValue('ProgramFilesDir') }
        finally { $currentVersion.Dispose() }
        if (!$programFiles -or ![IO.Path]::IsPathRooted($programFiles)) { throw 'The native Program Files location is invalid.' }
        $definitions = @(
            @{ Name = 'SystemOperations'; Source = 'v8-system-operations'; Keys = @('SYSTEM\CurrentControlSet\Services\V8SystemOperations', 'SOFTWARE\V8AgentOS\SystemOperations'); Files = @('v8-system-operations.exe') },
            @{ Name = 'SessionUnlock'; Source = 'v8-session-unlock'; Keys = @('SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{793345F6-C96B-472A-A780-387839440068}', 'SOFTWARE\Classes\CLSID\{793345F6-C96B-472A-A780-387839440068}', 'SOFTWARE\V8AgentOS\SessionUnlock'); Files = @('V8SessionUnlock.dll', 'v8-session-unlock.exe') }
        )
        foreach ($definition in $definitions) {
            $registered = $false
            foreach ($path in $definition.Keys) {
                $key = $null
                try { $key = $root.OpenSubKey($path); if ($null -ne $key) { $registered = $true } }
                catch [System.Security.SecurityException] { $registered = $true }
                finally { if ($null -ne $key) { $key.Dispose() } }
            }
            $directory = Join-Path $programFiles "V8AgentOS/$($definition.Name)"
            if ($definition.Name -eq 'SessionUnlock') {
                $class = $root.OpenSubKey('SOFTWARE\Classes\CLSID\{793345F6-C96B-472A-A780-387839440068}\InprocServer32')
                if ($null -ne $class) {
                    try {
                        if ($class.GetValue('') -ne (Join-Path $directory 'V8SessionUnlock.dll')) { throw 'The registered Provider points elsewhere; refusing to remove it.' }
                    } finally { $class.Dispose() }
                }
            }
            $files = @($definition.Files | ForEach-Object { Join-Path $directory $_ })
            [pscustomobject]@{ Name = $definition.Name; Source = $definition.Source; Registered = $registered; Files = $files; Needed = $registered -or @($files | Where-Object { Test-Path -LiteralPath $_ }).Count -gt 0 }
        }
    } finally { $root.Dispose() }
}

function Test-V8NativeAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return [Environment]::Is64BitProcess -and $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-V8NativeComponentRemoval([string]$Script, [string]$Arch) {
    $output = @(& $Script -Action Uninstall -Arch $Arch -Unattended)
    $receipt = $output | Select-Object -Last 1 | ConvertFrom-Json
    if ($receipt.ok -ne $true) { throw 'Native component removal was not confirmed.' }
    return $receipt
}

function Invoke-V8SystemComponentCleanup([string]$Root, [switch]$Preview, [switch]$RequireElevated) {
    try {
        $rootPath = [IO.Path]::GetFullPath($Root)
        if ($rootPath.IndexOfAny([char[]]@('"', "`r", "`n")) -ge 0) { throw 'Invalid package path.' }
        $components = @(Get-V8SystemComponentPresence | Where-Object Needed)
        if (!$components.Count) { return 0 }
        foreach ($component in $components) {
            $script = Join-Path $rootPath "native/$($component.Source)/install.ps1"
            if (!(Test-Path -LiteralPath $script -PathType Leaf)) { throw 'The packaged component uninstaller is missing.' }
        }
        if ($Preview) {
            Write-Host ('DRY RUN: remove only V8OS optional components: ' + (($components | ForEach-Object Name) -join ', '))
            return 0
        }
        if (!(Test-V8NativeAdministrator)) {
            if ($RequireElevated) { throw 'Windows did not supply a native administrator process.' }
            $powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
            if (![Environment]::Is64BitProcess) { $powershell = Join-Path $env:SystemRoot 'Sysnative/WindowsPowerShell/v1.0/powershell.exe' }
            $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -EngineRoot "' + $rootPath + '" -Elevated'
            $process = $null
            try {
                $process = Start-Process -FilePath $powershell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
                if (!$process.WaitForExit(120000)) { Write-Host 'Component cleanup did not finish; the main uninstall must stop.'; return 1603 }
                return $process.ExitCode
            } catch [System.ComponentModel.Win32Exception] {
                if ($_.Exception.NativeErrorCode -eq 1223) { Write-Host 'Windows elevation was cancelled; V8OS was not uninstalled.'; return 1223 }
                throw
            } finally { if ($null -ne $process) { $process.Dispose() } }
        }
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
        foreach ($component in $components) {
            $script = Join-Path $rootPath "native/$($component.Source)/install.ps1"
            $null = Invoke-V8NativeComponentRemoval -Script $script -Arch $arch
            $remaining = @(Get-V8SystemComponentPresence | Where-Object { $_.Name -eq $component.Name -and $_.Registered })
            if ($remaining.Count) { throw 'A component service or registration remains; main uninstall must stop.' }
            foreach ($file in $component.Files) {
                if (Test-Path -LiteralPath $file) {
                    # Do not defer deletion: reinstalling before reboot would
                    # otherwise risk deleting the new DLL at the same path.
                    Write-Host 'A component file is still in use. Sign out normally or restart Windows, then retry uninstall. The main application is preserved.'
                    return 1603
                }
            }
        }
        Write-Host 'V8OS optional services and sign-in registration were removed; user credentials and Windows sign-in providers were preserved.'
        return 0
    } catch {
        Write-Host 'V8OS optional component cleanup failed. The main application is preserved; use Admin to repair or remove its system components before retrying.'
        return 1603
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-V8SystemComponentCleanup -Root $EngineRoot -Preview:($DryRun -or $WhatIfPreference) -RequireElevated:$Elevated)
}
