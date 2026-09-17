import React, { useEffect, useMemo, useReducer, useSyncExternalStore } from 'react';
import { render, Box, Text, useCursor } from 'ink';
import stringWidth from 'string-width';
import { Client } from './client.js';
import { Surface } from './surface.js';
import { editExternal, editorArgv } from './external-editor.js';
import { clip, dimensions, editor, editorLayout, InputDecoder, safeText, wrap, type Input } from './terminal.js';
import { messageText, statusLabel, PausedTranscriptUpdates } from './presentation.js';
import { TranscriptLayout } from './transcript-layout.js';
import { suggestionRows } from './command-suggestions.js';
export { messageText } from './presentation.js';

const Pad = ({ lines, height, width, selected = -1, titled = false }: { lines: string[]; height: number; width: number; selected?: number; titled?: boolean }) => <Box width={width} height={height} flexDirection="column" overflow="hidden">{Array.from({ length: height }, (_, i) => <Text key={i} bold={i === selected || titled && i === 0} inverse={i === selected} wrap="truncate-end">{clip(lines[i] || ' ', width)}</Text>)}</Box>;

function App({ client, surface, dispatch }: { client: Client; surface: Surface; dispatch: (event: Input) => void }) {
  useSyncExternalStore(client.subscribe, client.getRevision);
  const [, redraw] = useReducer(n => n + 1, 0);
  const { setCursorPosition } = useCursor();
  const transcript = useMemo(() => new TranscriptLayout({ textOf: messageText }), [client.instance.instanceId, client.view.sessionId]);
  const pausedUpdates = useMemo(() => new PausedTranscriptUpdates(), [client.instance.instanceId, client.view.sessionId]);
  surface.onChange = redraw;
  if (!client.busy && surface.input.text !== client.draft.text) surface.input = editor(client.draft.text);
  useEffect(() => { const resize = () => redraw(); process.stdout.on('resize', resize); return () => { process.stdout.off('resize', resize); }; }, []);
  const columns = process.stdout.columns || 80, rows = process.stdout.rows || 24;
  const size = dimensions(columns, rows, client.view.sidebar, client.view.detail);
  const menu = surface.suggestions;
  const editing = menu ? { ...menu.query, text: '/' + menu.query.text, cursor: menu.query.cursor + 1 } : surface.page?.fields ? surface.formEditor : surface.input;
  const showComposer = !surface.page || Boolean(surface.page.fields);
  const field = surface.page?.fields?.[surface.page.fieldIndex || 0];
  const secret = Boolean(field?.secret);
  surface.editorWidth = Math.max(1, columns - 2);
  const inputLayout = editorLayout(editing, surface.editorWidth, secret);
  const inputLines = inputLayout.lines;
  const inputHeight = !showComposer ? 0 : size.small ? 1 : Math.max(1, Math.min(8, Math.floor(rows / 3), inputLines.length));
  const availableHistoryHeight = Math.max(1, rows - inputHeight - (showComposer ? 6 : 4));
  const suggestions = menu ? suggestionRows(surface.commands(), menu.query.text, menu.selected, columns, Math.max(0, availableHistoryHeight - 2)) : { lines: [], selectedRow: -1 };
  const historyHeight = availableHistoryHeight - suggestions.lines.length;
  const inputOffset = Math.max(0, inputLayout.cursor.row + 1 - inputHeight);
  useEffect(() => {
    if (surface.page && !surface.page.fields) { setCursorPosition(undefined); return; }
    setCursorPosition({ x: Math.min(columns - 1, 2 + inputLayout.cursor.column), y: 5 + historyHeight + suggestions.lines.length + inputLayout.cursor.row - inputOffset });
  });
  let body: string[] = [];
  let selectedRow = -1;
  const page = surface.page;
  if (page) {
    const content = wrap([page.title, ...page.lines].join('\n'), columns);
    page.offset = Math.max(0, Math.min(page.offset, Math.max(0, content.length - Math.max(1, historyHeight - 5))));
    const actionCount = Math.min(Math.max(2, Math.floor(historyHeight / 2)), page.actions.length);
    const actionStart = Math.max(0, page.selected - actionCount + 1);
    const actions = page.actions.slice(actionStart, actionStart + actionCount).map((a, i) => `${page.selected === actionStart + i ? '›' : ' '} ${actionStart + i + 1}. ${a.label}${a.disabled ? '（不可用）' : ''}`);
    const fields = page.fields?.map((f, i) => `${page.fieldIndex === i ? '›' : ' '} ${f.label}：${f.secret ? (f.value ? '已输入' : '未输入') : clip(f.value, Math.max(10, columns - stringWidth(f.label) - 5))}`) || [];
    body = [...content.slice(page.offset, page.offset + Math.max(1, historyHeight - actions.length - fields.length)), ...fields, ...actions];
    selectedRow = page.fields ? body.length - actions.length - fields.length + (page.fieldIndex || 0) : body.length - actions.length + page.selected - actionStart;
  } else {
    const saved = client.view.scroll[client.view.sessionId];
    const viewport = transcript.window(client.messages, { width: size.chat, height: historyHeight, anchor: saved,
      following: surface.following, scrollDelta: menu ? 0 : surface.scrollDelta });
    // The temporary menu reduces visible history but never moves its saved anchor.
    if (!menu) {
      surface.scrollDelta = 0; surface.following = viewport.following;
      if (viewport.following) surface.unread = 0;
      if (viewport.anchor) client.view.scroll[client.view.sessionId] = { ...viewport.anchor, following: viewport.following };
    }
    body = viewport.rows.map(row => row.text);
    if (!body.length) body = [size.small ? '小窗口模式' : 'V8OS · 开始对话', '', client.workspace ? `工作区：${client.workspace}` : '先按 F3 连接模型并选择工作区。', '输入消息，或按 / 查看操作。'];
  }
  const label = `${client.instance.name || 'V8OS'} · ${client.connection}${client.inbox.length ? ` · 待处理 ${client.inbox.length}` : ''} · ${client.workspace || '未选择工作区'}`;
  surface.unread = pausedUpdates.update(client.messages, surface.following);
  const hint = menu ? (columns < 40 ? '↑↓选 Tab补 ↵执行 Esc返' : columns < 60 ? '↑↓选择 Tab补全 Enter执行 Esc返回' : '↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 返回草稿 · Ctrl+P 完整菜单') : page?.fields ? 'Tab 切换字段 · F9 保存/预览 · Esc 返回' : page ? '↑↓/Tab 选择 · Enter 执行 · PgUp/PgDn 阅读 · Esc 返回' : 'Enter 发送 · F8 多行 · Ctrl+P 操作 · F1 帮助 · Ctrl+D 退出';
  return <Box flexDirection="column" width={columns} height={rows}>
    <Text bold>{clip(label, columns)}</Text><Text dimColor>{'─'.repeat(columns)}</Text>
    <Box height={historyHeight}>
      {!page && size.sidebar > 0 && <Box width={size.sidebar} borderStyle="single" borderTop={false} borderLeft={false} borderBottom={false}><Pad titled width={size.sidebar - 1} height={historyHeight} lines={['会话概览 · Ctrl+B选择', ...client.sessions.map(s => `${s.id === client.view.sessionId ? '●' : ' '} ${s.title || '未命名'} · ${statusLabel(s.status)}`)]} /></Box>}
      <Pad width={page ? columns : size.chat} height={historyHeight} lines={body} selected={selectedRow} titled={Boolean(page)} />
      {!page && size.detail > 0 && <Box width={size.detail} borderStyle="single" borderTop={false} borderRight={false} borderBottom={false}><Pad titled width={size.detail - 1} height={historyHeight} lines={['任务概览 · Ctrl+T详情', `状态：${statusLabel(client.run.status || client.snapshot.runtimeStatus) || '未运行'}`, ...client.outputs.flatMap(x => [x.name, x.path || ''])]} /></Box>}
    </Box>
    <Text color={/失败|未知|未确认|未连接|中断|错误/.test(client.notice) ? 'yellow' : undefined} dimColor={!client.notice}>{clip(surface.following ? client.notice : `已暂停跟随${surface.unread ? ` · ${surface.unread} 条有更新` : ''} · 菜单“回到底部”恢复`, columns)}</Text>
    {showComposer && <Text>{clip(page?.fields ? `编辑：${field!.label}` : `${statusLabel(client.run.status) || '对话'}${surface.multiline ? ' · 多行（F9发送）' : ''}${client.draft.attachments.length ? ` · 附件 ${client.draft.attachments.length}` : ''}${client.draft.unknown ? ' · 发送结果待确认' : ''}${surface.busy || client.busy ? ' · 正在处理' : ''}`, columns)}</Text>}
    {showComposer && <Text dimColor>{'─'.repeat(columns)}</Text>}
    {menu && <Pad width={columns} height={suggestions.lines.length} lines={suggestions.lines} selected={suggestions.selectedRow} />}
    <Pad width={columns} height={inputHeight} lines={inputLines.slice(inputOffset, inputOffset + inputHeight).map((l, i) => `${i === 0 ? '> ' : '  '}${l}`)} />
    <Text dimColor>{clip(hint, columns)}</Text>
  </Box>;
}

export async function start(args: string[]) {
  const client = new Client();
  const requested = args.indexOf('--session'); const requestedSession = requested >= 0 ? args[requested + 1] || '' : '';
  const surface = new Surface(client);
  const reader = args.includes('--screen-reader') || process.env.INK_SCREEN_READER === 'true';
  const decoder = new InputDecoder(); let timer: NodeJS.Timeout | undefined, pasteTimer: NodeJS.Timeout | undefined;
  let app: ReturnType<typeof render> | undefined; let done = false; let resolveExit: () => void = () => {};
  let editingAbort: AbortController | undefined;
  const exited = new Promise<void>(resolve => { resolveExit = resolve; });
  const echo = () => {
    if (!reader) return;
    const page = surface.page;
    if (surface.suggestions) {
      const menu = surface.suggestions, rows = suggestionRows(surface.commands(), menu.query.text, menu.selected, 120, 8);
      process.stdout.write('\n' + safeText(rows.lines.join('\n')) + '\n↑↓选择，Tab补全，Enter执行，Esc返回原草稿 > /' + safeText(menu.query.text));
    } else if (page) {
      process.stdout.write('\n' + safeText([page.title, ...page.lines, ...(page.fields || []).map(f => `${f.label}：${f.secret ? '隐藏输入' : f.value}`), ...page.actions.map((a, i) => `${i + 1}. ${a.label}${a.disabled ? '（不可用）' : ''}`)].join('\n')) + '\n');
      process.stdout.write(page.fields ? `${page.fields[page.fieldIndex || 0].label} > ` : '选择编号，Enter确认；Esc返回 > ');
    } else process.stdout.write(`\n${safeText(client.notice)}\n输入 > `);
  };
  let number = '';
  let numberPage: Surface['page'] = null;
  const dispatch = (event: Input) => {
    if (done) return;
    if (reader && numberPage !== surface.page) { number = ''; numberPage = surface.page; }
    if (reader && surface.page && !surface.page.fields) {
      if (event.key === 'text' && /^\d+$/.test(event.text || '')) { number += event.text; process.stdout.write(event.text!); return; }
      if (event.key === 'enter' && number) { surface.page.selected = Math.max(0, Number(number) - 1); number = ''; }
    }
    const previous = surface.page, previousSuggestions = surface.suggestions;
    // Editing remains synchronous while network operations have one owner.
    const actionKey = ['enter', 'f9', 'f1', 'f2', 'f3', 'f4', 'ctrl-p', 'ctrl-b', 'ctrl-t', 'ctrl-n', 'escape', 'ctrl-c'].includes(event.key);
    const work = surface.dispatch(event);
    void work.finally(() => {
      if (reader) {
        const secret = surface.page?.fields?.[surface.page.fieldIndex || 0]?.secret;
        if (event.key === 'text' || event.key === 'paste') process.stdout.write(secret ? '' : safeText(event.text));
        else if (event.key === 'backspace') process.stdout.write(secret ? '' : '\n当前输入 > ' + safeText(surface.page?.fields ? surface.formEditor.text : surface.input.text));
        if (previous !== surface.page || previousSuggestions || surface.suggestions || actionKey || event.key === 'tab') echo();
      }
    });
  };
  const onData = (chunk: Buffer) => {
    clearTimeout(timer); clearTimeout(pasteTimer);
    for (const event of decoder.push(chunk)) dispatch(event);
    timer = setTimeout(() => { for (const event of decoder.flush()) dispatch(event); }, 120);
    pasteTimer = setTimeout(() => { const events = decoder.finishPaste(); for (const event of events) dispatch(event); if (events.some(e => e.key === 'paste')) { client.notice = '粘贴仍未结束，已保存收到的内容；迟到的按键字节仍按文本处理。'; client.changed(); } }, 2000);
  };
  let suspended = false;
  const draw = () => render(<App client={client} surface={surface} dispatch={dispatch} />, { exitOnCtrlC: false, patchConsole: false, maxFps: 20, incrementalRendering: true, alternateScreen: true });
  const resume = () => {
    if (done || !suspended) return; suspended = false;
    if (editingAbort) return;
    if (!reader) app = draw();
    process.stdin.setRawMode(true); process.stdin.resume(); process.stdout.write('\x1b[?2004h');
    void client.tick().catch(() => { client.connection = '连接中断 · 自动重连'; client.changed(); });
  };
  const suspend = () => {
    if (done || suspended) return; suspended = true;
    if (editingAbort) { process.kill(process.pid, 'SIGSTOP'); return; }
    app?.unmount(); process.stdin.setRawMode(false); process.stdin.pause(); process.stdout.write('\x1b[?2004l\x1b[?25h');
    process.kill(process.pid, 'SIGSTOP');
  };
  const cleanup = () => {
    if (done) return; done = true;
    clearTimeout(timer); clearTimeout(pasteTimer);
    process.stdin.off('data', onData); process.stdin.off('end', cleanup);
    process.off('SIGTERM', cleanup); process.off('SIGINT', interrupt); process.off('SIGHUP', cleanup);
    if (process.platform !== 'win32') { process.off('SIGTSTP', suspend); process.off('SIGCONT', resume); }
    app?.unmount(); process.stdin.setRawMode(false); process.stdin.pause();
    editingAbort?.abort();
    process.stdout.write('\x1b[?2004l\x1b[?25h');
    try { client.stop(); } catch { process.exitCode = 1; }
    process.stdout.write('\n终端已退出，后台服务继续运行。\n'); resolveExit();
  };
  const interrupt = () => { if (editingAbort) editingAbort.abort(); else cleanup(); };
  surface.onEditor = async text => {
    const command = process.env.VISUAL || process.env.EDITOR || '';
    editorArgv(command); // Refuse bad configuration before relinquishing the terminal.
    clearTimeout(timer); clearTimeout(pasteTimer);
    editingAbort = new AbortController();
    process.stdin.off('data', onData); process.stdin.pause();
    app?.unmount(); process.stdin.setRawMode(false); process.stdout.write('\x1b[?2004l\x1b[?25h');
    try { return await editExternal(text, { command, signal: editingAbort.signal }); }
    finally {
      editingAbort = undefined;
      if (!done) {
        if (!reader) app = draw();
        process.stdin.setRawMode(true); process.stdin.on('data', onData); process.stdin.resume(); process.stdout.write('\x1b[?2004h');
      }
    }
  };
  surface.onExit = cleanup;
  if (!reader) app = draw();
  process.stdin.setRawMode(true); process.stdin.resume(); process.stdin.on('data', onData); process.stdin.on('end', cleanup);
  process.stdout.write('\x1b[?2004h');
  process.on('SIGTERM', cleanup); process.on('SIGINT', interrupt); process.on('SIGHUP', cleanup);
  if (process.platform !== 'win32') { process.on('SIGTSTP', suspend); process.on('SIGCONT', resume); }
  let lastMessages = new Map<string, string>();
  let readerSession = '';
  const unsubscribe = reader ? client.subscribe(() => {
    if (editingAbort) return;
    const key = `${client.instance.instanceId || ''}:${client.view.sessionId}`;
    if (key !== readerSession) { lastMessages.clear(); readerSession = key; }
    for (const m of client.messages) {
      const next = safeText(messageText(m)), previous = lastMessages.get(m.id);
      if (next !== previous) { process.stdout.write(`\n${previous && next.startsWith(previous) ? next.slice(previous.length) : next}\n`); lastMessages.set(m.id, next); }
    }
  }) : () => {};
  try {
    await client.initialize();
    if (requestedSession && client.instance.instanceId) await client.attach(requestedSession);
    if (client.instance.initialized === false) await surface.execute(() => surface.phones());
    echo();
    void client.runLoop();
    await exited;
  } finally { unsubscribe(); cleanup(); }
}
