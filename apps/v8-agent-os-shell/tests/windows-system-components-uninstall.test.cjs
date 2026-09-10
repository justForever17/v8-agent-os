const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const shellRoot = path.resolve(__dirname, '..');
const engineRoot = path.resolve(shellRoot, '../v8-agent-os-engine');
const helper = path.join(shellRoot, 'assets/windows-uninstall-system-components.ps1');
const hookFile = path.join(shellRoot, 'assets/windows-installer.nsh');
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
const powershell = path.join(process.env.SystemRoot || 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');

function runCase(body, executable = powershell) {
  const source = `
$ErrorActionPreference = 'Stop'
. ${quote(helper)} -EngineRoot ${quote(engineRoot)}
$script:registered = $true
$script:removed = 0
function Get-V8SystemComponentPresence {
  [pscustomobject]@{ Name = 'SessionUnlock'; Source = 'v8-session-unlock'; Registered = $script:registered; Files = @(); Needed = $true }
}
function Test-V8NativeAdministrator { return $true }
function Invoke-V8NativeComponentRemoval { throw 'Unexpected real-removal boundary call.' }
function Start-Process { throw 'Unexpected elevation boundary call.' }
${body}
`;
  const result = spawnSync(executable, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', source], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result.stdout;
}

test('optional-component cleanup precedes main file deletion and is skipped during upgrades', () => {
  const hook = fs.readFileSync(hookFile, 'utf8').replace(/\r\n/g, '\n');
  const macro = hook.split('!macro customUnInstall\n')[1]?.split('!macroend')[0];
  assert.ok(macro);
  assert.match(macro, /\$\{IfNot\} \$\{isUpdated\}/);
  assert.match(macro, /File \/oname=v8os-remove-system-components\.ps1/);
  assert.match(macro, /\$\{If\} \$0 != 0[\s\S]*SetErrorLevel 1603[\s\S]*Abort/);
  const template = path.join(shellRoot, 'node_modules/app-builder-lib/templates/nsis/uninstaller.nsh');
  if (fs.existsSync(template)) {
    const source = fs.readFileSync(template, 'utf8');
    assert.ok(source.indexOf('!insertmacro customUnInstall') < source.indexOf('# delete the installed files'));
  }
});

test('real installed-component dry run never elevates or removes anything', { skip: process.platform !== 'win32' }, () => {
  const result = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', helper, '-EngineRoot', engineRoot, '-DryRun'], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test('real 32-bit and 64-bit PowerShell see identical native component paths and dry-run plans', { skip: process.platform !== 'win32' }, (t) => {
  const wow64 = path.join(process.env.SystemRoot || 'C:/Windows', 'SysWOW64/WindowsPowerShell/v1.0/powershell.exe');
  if (!fs.existsSync(wow64)) { t.skip('32-bit PowerShell is unavailable'); return; }
  const snapshots = [];
  for (const executable of [wow64, powershell]) {
    // A fresh runner may cold-start the x86 CLR under WOW64/emulation. That
    // bootstrap is not the native registry/helper operation being measured.
    // Keep a bounded bootstrap ceiling and the original 15s operation budget;
    // phase receipts distinguish either timeout from a path/bitness failure.
    const source = `$ErrorActionPreference = 'Stop'
[Console]::Error.WriteLine('v8-probe:script-entered')
$probeClock = [Diagnostics.Stopwatch]::StartNew()
. ${quote(helper)} -EngineRoot ${quote(engineRoot)}
[Console]::Error.WriteLine('v8-probe:helper-loaded')
$components = @(Get-V8SystemComponentPresence)
$operationMs = $probeClock.Elapsed.TotalMilliseconds
[Console]::Error.WriteLine('v8-probe:snapshot-complete')
@{ process64 = [Environment]::Is64BitProcess; components = $components; operationMs = $operationMs } | ConvertTo-Json -Depth 4 -Compress`;
    const started = performance.now();
    const snapshot = spawnSync(executable, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', source], { encoding: 'utf8', windowsHide: true, timeout: 60000 });
    const wallMs = performance.now() - started;
    assert.equal(snapshot.error, undefined, `${executable}: ${snapshot.error?.code || 'process error'}; ${snapshot.stderr || 'no script-entry receipt'}`);
    assert.equal(snapshot.status, 0, snapshot.stderr || snapshot.stdout);
    const parsed = JSON.parse(snapshot.stdout.trim());
    assert.ok(Number.isFinite(parsed.operationMs) && parsed.operationMs < 15000,
      `native component snapshot exceeded its 15s operation budget: ${parsed.operationMs}ms`);
    t.diagnostic(`${path.relative(process.env.SystemRoot || 'C:/Windows', executable)}: wallMs=${Math.round(wallMs)}, operationMs=${Math.round(parsed.operationMs)}, bootstrapAndSerializationMs=${Math.round(wallMs - parsed.operationMs)}`);
    snapshots.push(parsed);
    const dryRun = spawnSync(executable, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', helper, '-EngineRoot', engineRoot, '-DryRun'], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
    assert.equal(dryRun.error, undefined);
    assert.equal(dryRun.status, 0, dryRun.stderr || dryRun.stdout);
  }
  assert.equal(snapshots[0].process64, false);
  assert.equal(snapshots[1].process64, true);
  assert.deepEqual(snapshots[0].components, snapshots[1].components);
  runCase(`
function Test-V8NativeAdministrator { return $false }
function Start-Process {
  $target = $args[[Array]::IndexOf($args, '-FilePath') + 1]
  if ($target -ne (Join-Path $env:SystemRoot 'Sysnative/WindowsPowerShell/v1.0/powershell.exe')) { throw '32-bit caller did not select native PowerShell' }
  $process = [pscustomobject]@{ ExitCode = 0 }
  $process | Add-Member ScriptMethod WaitForExit { param($timeout); return $true }
  $process | Add-Member ScriptMethod Dispose {}
  return $process
}
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 0) { throw '32-bit elevation routing failed' }
`, wow64);
});

const cases = [
  ['absent components need no elevation', `
function Get-V8SystemComponentPresence { @() }
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 0) { throw 'absent failed' }
`],
  ['preview never calls removal or elevation', `
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)} -Preview) -ne 0) { throw 'preview failed' }
`],
  ['UAC cancellation aborts the main uninstall', `
function Test-V8NativeAdministrator { return $false }
function Start-Process { throw [ComponentModel.Win32Exception]::new(1223) }
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 1223) { throw 'cancel became success' }
`],
  ['elevation failure does not recurse', `
function Test-V8NativeAdministrator { return $false }
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)} -RequireElevated) -ne 1603) { throw 'elevation failure became success' }
`],
  ['successful receipt is insufficient while registration remains', `
function Invoke-V8NativeComponentRemoval { return @{ ok = $true } }
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 1603) { throw 'remaining registration was ignored' }
`],
  ['confirmed cleanup uses only the matching native uninstaller', `
function Invoke-V8NativeComponentRemoval([string]$Script, [string]$Arch) {
  if ($Script -ne ${quote(path.join(engineRoot, 'native/v8-session-unlock/install.ps1'))}) { throw 'wrong remover' }
  $script:registered = $false; $script:removed++; return @{ ok = $true }
}
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 0 -or $script:removed -ne 1) { throw 'cleanup failed' }
`],
  ['locked component files stop uninstall without deferred deletion', `
function Get-V8SystemComponentPresence {
  [pscustomobject]@{ Name = 'SessionUnlock'; Source = 'v8-session-unlock'; Registered = $script:registered; Files = @(${quote(helper)}); Needed = $true }
}
function Invoke-V8NativeComponentRemoval { $script:registered = $false; return @{ ok = $true } }
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 1603) { throw 'retained file became success' }
if (!(Test-Path -LiteralPath ${quote(helper)})) { throw 'retained fixture was removed' }
`],
  ['successful elevation runs the same fixed helper once and propagates its exit', `
function Test-V8NativeAdministrator { return $false }
function Start-Process {
  if ($args -notcontains '-Verb' -or $args -notcontains 'RunAs') { throw 'missing OS authorization' }
  $arguments = $args[[Array]::IndexOf($args, '-ArgumentList') + 1]
  if (!$arguments.Contains(${quote(helper)}) -or !$arguments.EndsWith(' -Elevated')) { throw 'wrong elevated helper' }
  $process = [pscustomobject]@{ ExitCode = 0 }
  $process | Add-Member ScriptMethod WaitForExit { param($timeout); return $true }
  $process | Add-Member ScriptMethod Dispose {}
  return $process
}
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 0) { throw 'elevated result lost' }
`],
  ['failed elevated child aborts without claiming cleanup', `
function Test-V8NativeAdministrator { return $false }
function Start-Process {
  if ($args -notcontains '-Verb' -or $args -notcontains 'RunAs') { throw 'missing OS authorization' }
  $process = [pscustomobject]@{ ExitCode = 1603 }
  $process | Add-Member ScriptMethod WaitForExit { param($timeout); return $true }
  $process | Add-Member ScriptMethod Dispose {}
  return $process
}
if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 1603) { throw 'child failure became success' }
`],
  ['native ARM64 architecture selects the ARM64 component uninstaller contract', `
$priorArchitecture = $env:PROCESSOR_ARCHITECTURE
try {
  $env:PROCESSOR_ARCHITECTURE = 'ARM64'
  function Invoke-V8NativeComponentRemoval([string]$Script, [string]$Arch) {
    if ($Arch -ne 'arm64') { throw 'ARM64 was downgraded to x64' }
    $script:registered = $false; return @{ ok = $true }
  }
  if ((Invoke-V8SystemComponentCleanup -Root ${quote(engineRoot)}) -ne 0) { throw 'ARM64 dispatch failed' }
} finally { $env:PROCESSOR_ARCHITECTURE = $priorArchitecture }
`],
];
for (const [name, body] of cases) test(name, { skip: process.platform !== 'win32' }, () => runCase(body));

test('NSIS compiles the actual uninstall hook and embeds its fixed helper', { skip: process.platform !== 'win32' }, (t) => {
  const compiler = path.join(process.env.LOCALAPPDATA, 'electron-builder/Cache/nsis/nsis-3.0.4.1/makensis.exe');
  if (!fs.existsSync(compiler)) { t.skip('electron-builder NSIS compiler is not cached'); return; }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-uninstall-contract-'));
  try {
    const fixture = path.join(temporary, 'fixture.nsi');
    fs.writeFileSync(fixture, `Unicode true
!include "LogicLib.nsh"
!define BUILD_UNINSTALLER
!define isUpdated '\"\" == \"updated\"'
!include "${hookFile}"
OutFile "${path.join(temporary, 'compile-only.exe')}"
RequestExecutionLevel user
Section
WriteUninstaller "$TEMP\\never-run-v8-uninstall-fixture.exe"
SectionEnd
Section "Uninstall"
!insertmacro customUnInstall
SectionEnd
`, 'utf8');
    const result = spawnSync(compiler, ['/V2', fixture], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr || result.stdout);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
