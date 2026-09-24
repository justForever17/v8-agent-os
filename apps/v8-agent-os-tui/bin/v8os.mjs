#!/usr/bin/env node
// Validate diagnostics and terminal availability before importing the renderer.
const args = process.argv.slice(2);
const command = args[0] && !args[0].startsWith('-') ? args[0] : 'tui';
const json = args.includes('--json');
const english = (args.includes('--lang') && args[args.indexOf('--lang') + 1] === 'en') || /^en/i.test(process.env.V8OS_LANG || '');
const t = (zh, en) => english ? en : zh;
const print = value => console.log(JSON.stringify(value, null, json ? undefined : 2));
const controller = new AbortController();
const abort = () => controller.abort(new Error('Cancelled / 已取消'));
const progress = text => { if (!json) process.stderr.write(text + '\n'); };

const TUI_FLAGS = new Set(['--help', '-h', '--screen-reader', '--no-color', '--no-install', '--json']);
const LIFECYCLE_FLAGS = new Set(['--help', '-h', '--json', '--no-install']);

function optionError(message) {
  throw new Error(message);
}

function requireOptionValue(args, index, flag) {
  const value = args[index + 1];
  if (!value || value.startsWith('-')) optionError(`${flag} requires a value / ${flag} 需要参数`);
  return value;
}

function validateTuiArgs(args) {
  for (let index = args[0] === 'tui' ? 1 : 0; index < args.length; index += 1) {
    const flag = args[index];
    if (flag === '--session' || flag === '--lang') {
      const value = requireOptionValue(args, index, flag);
      if (flag === '--lang' && !['zh', 'en', 'zh-CN', 'en-US'].includes(value)) {
        optionError(`--lang accepts zh, en, zh-CN or en-US / --lang 只接受 zh、en、zh-CN 或 en-US`);
      }
      index += 1;
      continue;
    }
    if (!TUI_FLAGS.has(flag)) optionError(`Unknown TUI option: ${flag} / 未知 TUI 参数：${flag}`);
  }
}

function validateLifecycleArgs(command, args) {
  const allowNoInstall = command === 'start' || command === 'restart';
  const allowed = new Set(LIFECYCLE_FLAGS);
  if (!allowNoInstall) allowed.delete('--no-install');
  for (let index = 1; index < args.length; index += 1) {
    const flag = args[index];
    if (flag === '--only') {
      const value = requireOptionValue(args, index, flag);
      const components = [...new Set(value.split(',').map(item => item.trim()).filter(Boolean))];
      if (components.length !== 1 || components[0] !== 'engine') {
        optionError('The npm Core Base supports only --only engine; use the Server or desktop package for Web/Admin / npm Core Base 只支持 --only engine；Web/Admin 请使用 Server 或桌面发行物');
      }
      index += 1;
      continue;
    }
    if (flag === '--all' || flag === '--with') {
      if (flag === '--with') requireOptionValue(args, index, flag);
      optionError(`${flag} is not available in the npm Core Base; it will not spawn Web/Admin / npm Core Base 不支持 ${flag}，不会启动 Web/Admin`);
    }
    if (!allowed.has(flag)) optionError(`Unknown ${command} option: ${flag} / 未知 ${command} 参数：${flag}`);
  }
}

function validateEntryArgs() {
  if (command === 'tui') validateTuiArgs(args);
  else if (['start', 'stop', 'restart', 'status', 'install'].includes(command)) validateLifecycleArgs(command, args);
}

function help() {
  console.log(t(`V8OS 终端版 · 统一 CLI + Engine
用法：
  v8os                         准备 Engine 并打开对话终端
  v8os tui [--lang zh|en]       对话终端（F3 首次配置）
  v8os start [--no-install]     启动后台 Engine
  v8os stop|restart|status      管理当前状态目录的 Engine
  v8os install                 下载并校验当前版本 Engine
  v8os doctor [--json]          检查安装与进程状态
  v8os chat|sessions|config|inbox|workspace|service|logs ...

  Node.js 22+；首次运行会按当前平台下载并校验便携 Engine（Linux glibc x64、Windows x64/arm64、macOS x64/arm64）。
首次启动自动下载对应版本，不需要宿主 Python。v8os tui 自己拉起的 Engine 会在终端退出时释放；已有 daemon/service 由原控制面继续管理。
离线资产：V8OS_ENGINE_MANIFEST_URL=file:///...json 与 V8OS_ENGINE_ARCHIVE=/...tar.gz
`, `V8OS terminal · Unified CLI + Engine
Usage:
  v8os                         Prepare Engine and open the conversation terminal
  v8os tui [--lang zh|en]       Conversation terminal (F3 first-run setup)
  v8os start [--no-install]     Start the Engine daemon
  v8os stop|restart|status      Manage this state directory's Engine
  v8os install                 Download and verify this release's Engine
  v8os doctor [--json]          Inspect installation and process state
  v8os chat|sessions|config|inbox|workspace|service|logs ...

  Requires Node.js 22+. The first run downloads and verifies a portable Engine for Linux glibc x64, Windows x64/arm64, or macOS x64/arm64.
  First start downloads the exact runtime; host Python is not required. A runtime started by v8os tui is released on exit; an existing daemon/service remains owned by its control plane.
Offline: V8OS_ENGINE_MANIFEST_URL=file:///...json and V8OS_ENGINE_ARCHIVE=/...tar.gz
`));
}

async function main() {
  const helpRequested = args.includes('--help') || args.includes('-h');
  validateEntryArgs();
  if (command === 'help' || (helpRequested && ['tui', 'start', 'install', 'stop', 'restart', 'status', 'doctor'].includes(command))) { help(); return 0; }
  if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('V8OS requires Node.js 22+ / 需要 Node.js 22+');
  if (command === 'tui' && (!process.stdin.isTTY || !process.stdout.isTTY || (process.env.TERM === 'dumb' && !args.includes('--screen-reader')))) {
    print({ ok: false, error: 'tty_required', message: t('请在交互终端运行 v8os；脚本请用 v8os chat/sessions/... --json。', 'Use v8os in a TTY; scripts should use v8os chat/sessions/... --json.') }); return 2;
  }
  const { installEngine, runEngineCli, startEngine, statusEngine, stopEngine } = await import('./engine-bootstrap.mjs');
  const options = { install: !args.includes('--no-install'), signal: controller.signal, progress };
  if (command === 'status' || command === 'doctor') {
    const status = await statusEngine();
    if (command === 'doctor' && status.runtimeRoot) return runEngineCli(args, options);
    print(command === 'doctor' ? { node: process.versions.node, ...status } : status); return 0;
  }
  if (command === 'stop' || command === 'restart') {
    const result = await stopEngine();
    if (command === 'stop' || !['stopped', 'stale_state_removed', 'not_managed'].includes(result.status)) {
      print(result); return ['stopped', 'stale_state_removed', 'not_managed'].includes(result.status) ? 0 : 1;
    }
  }
  if (command === 'install') { print(await installEngine(options)); return 0; }
  if (command === 'start' || command === 'restart') { print(await startEngine(options)); return 0; }
  if (command === 'tui') {
    let started;
    let tuiError;
    let releaseError;
    try {
      started = await startEngine({ ...options, lifecycle: 'desktop' });
      process.removeListener('SIGINT', abort); process.removeListener('SIGTERM', abort);
      process.env.NODE_ENV ||= 'production';
      if (args.includes('--no-color') || process.env.NO_COLOR !== undefined) process.env.FORCE_COLOR = '0';
      const tuiArgs = (args[0] === 'tui' ? args.slice(1) : args).filter(arg => arg !== '--no-install');
      await (await import('../dist/main.js')).start(tuiArgs);
    } catch (error) { tuiError = error; }
    finally {
      // A TUI-owned Engine process is a short-lived lease.  A daemon/service
      // started by another control-plane owner is retained by expected identity
      // fencing and therefore cannot be stopped by this surface.
      if (started?.release) {
        try {
          const released = await started.release();
          if (!['stopped', 'lease_retained', 'not_owned'].includes(released?.status)) {
            releaseError = new Error(`Engine lifecycle release incomplete: ${released?.status || 'unknown'}`);
          }
        } catch (error) {
          releaseError = error instanceof Error ? error : new Error(String(error));
        }
      }
    }
    if (tuiError) {
      if (releaseError) tuiError = new Error(`${tuiError instanceof Error ? tuiError.message : String(tuiError)}; ${releaseError.message}`);
      throw tuiError;
    }
    if (releaseError) throw releaseError;
    return 0;
  }
  // Service installation owns its lifecycle; help must never boot a daemon.
  if (!helpRequested && ['chat', 'acp', 'sessions', 'inbox', 'workspace', 'config'].includes(command)
      && !(command === 'config' && args[1] === 'credentials')) await startEngine(options);
  return runEngineCli(args, options);
}

process.on('SIGINT', abort); process.on('SIGTERM', abort);
try { process.exitCode = await main(); }
catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  if (json) print({ ok: false, error: message }); else console.error(t('V8OS：', 'V8OS: ') + message);
  process.exitCode = controller.signal.aborted ? 130 : 1;
} finally { process.removeListener('SIGINT', abort); process.removeListener('SIGTERM', abort); }
