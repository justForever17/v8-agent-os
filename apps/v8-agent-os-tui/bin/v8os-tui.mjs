#!/usr/bin/env node
const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) {
  console.log(`V8OS 终端界面
用法：v8os-tui [--session id] [--screen-reader] [--no-color]
需要 Node.js 22+ 和本机 V8OS Engine。安装本包不会下载或启动 Engine。
已有服务：v8os service start
首次安装：从 V8OS Release 下载 server 压缩包，解压后运行 ./install.sh。
进入后 Ctrl+P 操作菜单 · F1 帮助 · F8 多行 · F9 发送 · Ctrl+D 退出终端
非交互请使用 v8os chat / v8os sessions / v8os inbox --json。
--screen-reader 使用线性输出和编号菜单，兼容 TERM=dumb。`);
} else if (!process.stdin.isTTY || !process.stdout.isTTY || (process.env.TERM === 'dumb' && !args.includes('--screen-reader'))) {
  const diagnostic = 'TUI 需要交互终端；请使用 v8os chat 或 v8os sessions list --json。读屏终端可加 --screen-reader。';
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
