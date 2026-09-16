import React, { useEffect, useReducer, useSyncExternalStore } from 'react';
import { render, Box, Text, useCursor } from 'ink';
import stringWidth from 'string-width';
import { Client } from './client.js';
import { Surface } from './surface.js';
import { clip, dimensions, graphemes, InputDecoder, safeText, wrap, type Input } from './terminal.js';

export function messageText(message: any): string {
  const text = typeof message.content === 'string' ? message.content : (message.nodes || []).filter((n: any) => n.kind === 'narrative').map((n: any) => n.content || '').join('\n');
  const tools = (message.nodes || []).filter((n: any) => n.kind === 'execution' && n.executionType !== 'reasoning');
  return `${message.role === 'user' ? '你' : message.agentName || '主理人'} · ${message.state || message.status || ''}\n${text}`
    + tools.map((n: any) => `\n▸ ${n.toolName || n.title || n.executionType || '任务'} · ${n.status || n.state || ''}`).join('') + '\n';
}
const lineCache = new Map<string, { text: string; width: number; lines: string[] }>();
export function transcriptLines(messages: any[], width: number): string[] {
  const ids = new Set(messages.map(m => m.id));
  for (const id of lineCache.keys()) if (!ids.has(id)) lineCache.delete(id);
  return messages.flatMap(m => {
    const text = messageText(m), cached = lineCache.get(m.id);
    if (cached?.text === text && cached.width === width) return cached.lines;
    const lines = wrap(text, width); lineCache.set(m.id, { text, width, lines }); return lines;
  });
}
const Pad = ({ lines, height, width }: { lines: string[]; height: number; width: number }) => <Box width={width} height={height} flexDirection="column" overflow="hidden">{Array.from({ length: height }, (_, i) => <Text key={i} wrap="truncate-end">{clip(lines[i] || ' ', width)}</Text>)}</Box>;

function App({ client, surface, dispatch }: { client: Client; surface: Surface; dispatch: (event: Input) => void }) {
  useSyncExternalStore(client.subscribe, client.getRevision);
  const [, redraw] = useReducer(n => n + 1, 0);
  const { setCursorPosition } = useCursor();
  surface.onChange = redraw;
  useEffect(() => { const resize = () => redraw(); process.stdout.on('resize', resize); return () => { process.stdout.off('resize', resize); }; }, []);
  const columns = process.stdout.columns || 80, rows = process.stdout.rows || 24;
  const size = dimensions(columns, rows, client.view.sidebar, client.view.detail);
  const editing = surface.page?.fields ? surface.formEditor : surface.input;
  const field = surface.page?.fields?.[surface.page.fieldIndex || 0];
  const secret = Boolean(field?.secret);
  const inputText = secret ? '•'.repeat(graphemes(editing.text).length) : safeText(editing.text);
  const inputLines = wrap(inputText || ' ', Math.max(1, columns - 2));
  const inputHeight = size.small ? 1 : Math.max(1, Math.min(8, Math.floor(rows / 3), Math.max(3, inputLines.length)));
  const historyHeight = Math.max(1, rows - inputHeight - 7);
  const prefix = graphemes(editing.text).slice(0, editing.cursor).join('');
  const cursorLines = wrap(secret ? '•'.repeat(editing.cursor) : prefix, Math.max(1, columns - 2));
  const inputOffset = Math.max(0, cursorLines.length - inputHeight);
  useEffect(() => {
    if (surface.page && !surface.page.fields) { setCursorPosition(undefined); return; }
    setCursorPosition({ x: Math.min(columns - 1, 2 + stringWidth(cursorLines.at(-1) || '')), y: 5 + historyHeight + cursorLines.length - 1 - inputOffset });
  });
  let body: string[] = [];
  const page = surface.page;
  if (page) {
    const content = wrap([page.title, ...page.lines].join('\n'), columns);
    page.offset = Math.max(0, Math.min(page.offset, Math.max(0, content.length - Math.max(1, historyHeight - 5))));
    const actionCount = Math.min(Math.max(2, Math.floor(historyHeight / 2)), page.actions.length);
    const actionStart = Math.max(0, page.selected - actionCount + 1);
    const actions = page.actions.slice(actionStart, actionStart + actionCount).map((a, i) => `${page.selected === actionStart + i ? '›' : ' '} ${actionStart + i + 1}. ${a.label}${a.disabled ? '（不可用）' : ''}`);
    const fields = page.fields?.map((f, i) => `${page.fieldIndex === i ? '›' : ' '} ${f.label}：${f.secret ? (f.value ? '已输入' : '未输入') : clip(f.value, Math.max(10, columns - stringWidth(f.label) - 5))}`) || [];
    body = [...content.slice(page.offset, page.offset + Math.max(1, historyHeight - actions.length - fields.length)), ...fields, ...actions];
  } else {
    const lines = transcriptLines(client.messages, size.chat);
    const end = Math.max(0, lines.length - historyHeight);
    if (surface.following) surface.anchor = end;
    if (surface.anchor >= end) { surface.anchor = end; surface.following = true; surface.unread = 0; }
    body = lines.slice(surface.anchor, surface.anchor + historyHeight);
    if (!body.length) body = [size.small ? '小窗口模式' : 'V8OS · 开始对话', '', client.view.workspace ? `工作区：${client.view.workspace}` : '先按 F3 连接模型并选择工作区。', '输入消息，或按 / 查看操作。'];
  }
  const label = `${client.instance.name || 'V8OS'} · ${client.connection} · 待处理 ${client.inbox.length} · ${client.view.workspace || '未选择工作区'}`;
  const hint = page?.fields ? 'Tab 切换字段 · F9 保存/预览 · Esc 返回' : page ? '↑↓/Tab 选择 · Enter 执行 · PgUp/PgDn 阅读 · Esc 返回' : 'Enter 发送 · F8 多行 · Ctrl+P 操作 · F2 待处理 · Ctrl+D 退出';
  return <Box flexDirection="column" width={columns} height={rows}>
    <Text bold>{clip(label, columns)}</Text><Text dimColor>{'─'.repeat(columns)}</Text>
    <Box height={historyHeight}>
      {!page && size.sidebar > 0 && <Pad width={size.sidebar} height={historyHeight} lines={['会话 · Ctrl+B', ...client.sessions.map(s => `${s.id === client.view.sessionId ? '●' : ' '} ${s.title || '未命名'} · ${s.status || ''}`)]} />}
      <Pad width={page ? columns : size.chat} height={historyHeight} lines={body} />
      {!page && size.detail > 0 && <Pad width={size.detail} height={historyHeight} lines={['任务 · Ctrl+T', `状态：${client.run.status || client.snapshot.runtimeStatus || '未运行'}`, ...client.outputs.map(x => `${x.name}\n${x.path || ''}`)]} />}
    </Box>
    <Text color="yellow">{clip(surface.following ? client.notice : '已暂停跟随 · PageDown / 菜单“回到底部”恢复 · ' + client.notice, columns)}</Text>
    <Text>{clip(page?.fields ? field!.label : `${client.run.status || '对话'} · 多行：${surface.multiline ? '开（F9发送）' : '关'} · 附件 ${client.draft.attachments.length}${client.draft.unknown ? ' · 发送结果待确认' : ''}${surface.busy || client.busy ? ' · 正在处理' : ''}`, columns)}</Text>
    <Text dimColor>{'─'.repeat(columns)}</Text>
    <Pad width={columns} height={inputHeight} lines={inputLines.slice(inputOffset, inputOffset + inputHeight).map((l, i) => `${i === 0 ? '> ' : '  '}${l}`)} />
    <Text dimColor>{clip(hint, columns)}</Text>
    <Text dimColor>{clip(size.small ? '小窗口模式 · / 操作 · F1 帮助' : `${client.messages.length} 条已加载 · ${client.page.hasMore || client.page.hasOlder ? '菜单可加载更早历史' : '当前历史'} · F1 帮助`, columns)}</Text>
  </Box>;
}

export async function start(args: string[]) {
  const client = new Client();
  const requested = args.indexOf('--session'); if (requested >= 0) client.view.sessionId = args[requested + 1] || '';
  const surface = new Surface(client);
  const reader = args.includes('--screen-reader') || process.env.INK_SCREEN_READER === 'true';
  const decoder = new InputDecoder(); let timer: NodeJS.Timeout | undefined, pasteTimer: NodeJS.Timeout | undefined;
  let app: ReturnType<typeof render> | undefined; let done = false; let resolveExit: () => void = () => {};
  const exited = new Promise<void>(resolve => { resolveExit = resolve; });
  const echo = () => {
    if (!reader) return;
    const page = surface.page;
    if (page) {
      process.stdout.write('\n' + safeText([page.title, ...page.lines, ...(page.fields || []).map(f => `${f.label}：${f.secret ? '隐藏输入' : f.value}`), ...page.actions.map((a, i) => `${i + 1}. ${a.label}${a.disabled ? '（不可用）' : ''}`)].join('\n')) + '\n');
      process.stdout.write(page.fields ? `${page.fields[page.fieldIndex || 0].label} > ` : '选择编号，Enter确认；Esc返回 > ');
    } else process.stdout.write(`\n${safeText(client.notice)}\n输入 > `);
  };
  let number = '';
  const dispatch = (event: Input) => {
    if (done) return;
    if (surface.busy || client.busy) return;
    if (reader && surface.page && !surface.page.fields) {
      if (event.key === 'text' && /^\d+$/.test(event.text || '')) { number += event.text; process.stdout.write(event.text!); return; }
      if (event.key === 'enter' && number) { surface.page.selected = Math.max(0, Number(number) - 1); number = ''; }
    }
    const previous = surface.page;
    // Editing remains synchronous while network operations have one owner.
    const actionKey = ['enter', 'f9', 'f1', 'f2', 'f3', 'f4', 'ctrl-p', 'ctrl-b', 'ctrl-t', 'ctrl-n', 'escape', 'ctrl-c'].includes(event.key);
    const work = actionKey ? surface.execute(() => surface.handle(event)) : surface.handle(event);
    void work.finally(() => {
      if (reader) {
        const secret = surface.page?.fields?.[surface.page.fieldIndex || 0]?.secret;
        if (event.key === 'text' || event.key === 'paste') process.stdout.write(secret ? '' : safeText(event.text));
        else if (event.key === 'backspace') process.stdout.write(secret ? '' : '\n当前输入 > ' + safeText(surface.page?.fields ? surface.formEditor.text : surface.input.text));
        if (previous !== surface.page || actionKey || event.key === 'tab') echo();
      }
    });
  };
  const onData = (chunk: Buffer) => {
    clearTimeout(timer); clearTimeout(pasteTimer);
    for (const event of decoder.push(chunk)) dispatch(event);
    timer = setTimeout(() => { for (const event of decoder.flush()) dispatch(event); }, 120);
    pasteTimer = setTimeout(() => { for (const event of decoder.finishPaste()) dispatch(event); }, 2000);
  };
  const cleanup = () => {
    if (done) return; done = true;
    clearTimeout(timer); clearTimeout(pasteTimer);
    process.stdin.off('data', onData); process.stdin.off('end', cleanup);
    process.off('SIGTERM', cleanup); process.off('SIGINT', cleanup); process.off('SIGHUP', cleanup);
    app?.unmount(); process.stdin.setRawMode(false); process.stdin.pause();
    process.stdout.write('\x1b[?2004l\x1b[?25h');
    try { client.stop(); } catch { process.exitCode = 1; }
    process.stdout.write('\n终端已退出，后台服务继续运行。\n'); resolveExit();
  };
  surface.onExit = cleanup;
  if (!reader) app = render(<App client={client} surface={surface} dispatch={dispatch} />, { exitOnCtrlC: false, patchConsole: false, maxFps: 20, incrementalRendering: true, alternateScreen: true });
  process.stdin.setRawMode(true); process.stdin.resume(); process.stdin.on('data', onData); process.stdin.on('end', cleanup);
  process.stdout.write('\x1b[?2004h');
  process.on('SIGTERM', cleanup); process.on('SIGINT', cleanup); process.on('SIGHUP', cleanup);
  let lastMessages = new Map<string, string>();
  const unsubscribe = reader ? client.subscribe(() => {
    for (const m of client.messages) {
      const next = safeText(messageText(m)), previous = lastMessages.get(m.id);
      if (next !== previous) { process.stdout.write(`\n${previous && next.startsWith(previous) ? next.slice(previous.length) : next}\n`); lastMessages.set(m.id, next); }
    }
  }) : () => {};
  try {
    await client.initialize(); echo();
    void client.runLoop();
    await exited;
  } finally { unsubscribe(); cleanup(); }
}
