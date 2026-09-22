#!/usr/bin/env node
const args = process.argv.slice(2);
const english = args.includes('--lang') && args[args.indexOf('--lang') + 1] === 'en' || String(process.env.V8OS_LANG || '').toLowerCase().startsWith('en');
if (args.includes('--lang') && args[args.indexOf('--lang') + 1]) process.env.V8OS_LANG = args[args.indexOf('--lang') + 1];
if (args.includes('--help') || args.includes('-h')) {
  console.log(english ? `V8OS terminal UI
Usage: v8os-tui [--session id] [--screen-reader] [--lang zh|en] [--no-color]
Requires Node.js 22+ and a local V8OS Engine. Use v8os for automatic first start.
v8os-tui opens only the UI; v8os start prepares and starts the Engine.
Inside: Ctrl+P commands · F1 help · F8 multiline · F9 send · Ctrl+D exit
Non-interactive clients use v8os chat/sessions/config/... --json from this package.
--screen-reader uses linear output and numbered actions.` : `V8OS 终端界面
用法：v8os-tui [--session id] [--screen-reader] [--lang zh|en] [--no-color]
需要 Node.js 22+ 和本机 V8OS Engine；首次使用直接运行 v8os 自动准备服务。
v8os-tui 仅打开界面；v8os start 负责准备并启动 Engine。
进入后 Ctrl+P 操作菜单 · F1 帮助 · F8 多行 · F9 发送 · Ctrl+D 退出终端
非交互请使用本包 v8os chat/sessions/config/... --json。
--screen-reader 使用线性输出和编号菜单，兼容 TERM=dumb。`);
} else if (!process.stdin.isTTY || !process.stdout.isTTY || (process.env.TERM === 'dumb' && !args.includes('--screen-reader'))) {
  const diagnostic = english ? 'The UI requires an interactive terminal; use v8os chat/sessions/... --json for scripts. Use --screen-reader in a TTY.' : '界面需要交互终端；脚本请使用 v8os chat/sessions/... --json。读屏终端请在 TTY 中加 --screen-reader。';
  if (args.includes('--json')) console.log(JSON.stringify({ ok: false, error: 'tty_required', message: diagnostic }));
  else console.error(diagnostic);
  process.exitCode = 2;
} else {
  if (Number(process.versions.node.split('.')[0]) < 22) {
    console.error('V8OS TUI 需要 Node.js 22+；普通 v8os CLI 仍支持 Node.js 20。');
    process.exitCode = 2;
  } else {
  process.env.NODE_ENV ||= 'production';
  if (args.includes('--no-color') || process.env.NO_COLOR !== undefined) process.env.FORCE_COLOR = '0';
  try { await (await import('../dist/main.js')).start(args); }
  catch { console.error('终端无法启动。请核对 Node.js 22+、安装包完整性及 Engine 状态。'); process.exitCode = 1; }
  }
}
