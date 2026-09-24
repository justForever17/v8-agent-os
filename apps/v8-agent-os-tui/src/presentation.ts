import { buildClientToolSurface, redactClientToolText } from '../../../packages/session-realtime/src/client-tool-surface.js';
import { renderMarkdown } from './markdown.js';
import type { ThemeName, ThemeTokens } from './theme.js';
export { renderMarkdown } from './markdown.js';

const statuses: Record<string, string> = {
  completed: '已完成', succeeded: '已完成', success: '已完成', failed: '失败', error: '失败',
  running: '运行中', active: '运行中', streaming: '正在回复', pending: '等待中', queued: '排队中',
  waiting: '等待中', waiting_approval: '等待确认', cancelled: '已取消', canceled: '已取消',
  interrupted: '已中断', partial: '部分完成', degraded: '结果不完整', unknown: '结果待确认', unknown_outcome: '结果待确认',
  timed_out: '已超时', terminated: '已终止', blocked: '操作受阻',
};
export function statusLabel(value: unknown): string {
  const key = String(value || '');
  return statuses[key] || (key ? '查看详情' : '');
}

/** Fold a call and its result by invocation identity, never merely tool name. */
export function executionSummaries(message: any): string[] {
  const calls = new Map<string, any>();
  for (const [index, node] of (message.nodes || []).entries()) {
    if (node.kind !== 'execution' || !['tool_call', 'tool_result'].includes(node.executionType) || node.displayInMessage === false) continue;
    const key = node.toolInvocationId || node.toolCallId || node.id || `unpaired-${index}`;
    const previous = calls.get(key);
    // An out-of-order old call must not resurrect an already known result.
    if (previous?.executionType === 'tool_result' && node.executionType === 'tool_call') continue;
    calls.set(key, node);
  }
  return Array.from(calls.values(), node => {
    const result = node.executionType === 'tool_result';
    const surface = buildClientToolSurface({ toolName: node.toolName || '工具', state: result ? 'result' : 'call',
      result: result ? node.result : undefined, resultStatus: node.resultStatus || node.status,
      resultReasonCode: node.resultReasonCode });
    const status = result ? surface.status : node.status || 'running';
    const label = statusLabel(status) || '结果待确认';
    const summary = result && surface.summary ? redactClientToolText(surface.summary) : '';
    const action = result && surface.actionable ? `\n  下一步：${redactClientToolText(surface.actionable)}` : '';
    // Runtime detail/control IDs remain in canonical nodes for diagnostics;
    // only references approved by the shared human projection enter chat.
    const refs = surface.refIds;
    return `▸ ${node.toolName || '工具'} · ${label}${summary ? ` — ${summary}` : ''}${action}${refs.length ? `\n  证据：${refs.join(' · ')}` : ''}`;
  });
}

export function messageText(message: any): string {
  const text = typeof message.content === 'string' ? message.content : (message.nodes || [])
    .filter((node: any) => node.kind === 'narrative' && node.displayInMessage !== false)
    .map((node: any) => node.content || '').join('\n');
  const label = statusLabel(message.state || message.status);
  return `${message.role === 'user' ? '你' : message.agentName || '主理人'}${label ? ` · ${label}` : ''}\n${text}`
    + executionSummaries(message).map(line => `\n${line}`).join('') + '\n';
}

/** Human Surface contract for TranscriptLayout/main.tsx integration. */
export function messageProjection(message: any, width: number, theme?: ThemeName | ThemeTokens) {
  return renderMarkdown(messageText(message), { width, theme });
}

/** Linear readers append prose without replaying the whole message on every
 * token. The final newline belongs to messageText's layout, not the prose. */
export function readerMessageUpdate(previous: string | undefined, next: string): string {
  if (previous === next) return '';
  const split = (value: string) => {
    const separator = value.indexOf('\n');
    const body = separator < 0 ? '' : value.slice(separator + 1);
    return { heading: separator < 0 ? value : value.slice(0, separator), body: body.endsWith('\n') ? body.slice(0, -1) : body };
  };
  const current = split(next);
  const full = `${current.heading}\n${current.body}`;
  if (previous === undefined) return `\n${full}`;
  const old = split(previous);
  if (current.body.startsWith(old.body)) {
    const append = current.body.slice(old.body.length);
    return append + (current.heading !== old.heading ? `\n${current.heading}\n` : '');
  }
  // Revisions, deletions and changed tool outcomes need an explicit correction;
  // a longest-common-prefix diff could silently erase a previously spoken fact.
  return `\n消息已更新\n${full}`;
}

/** Counts changed messages while reading history, not individual token deltas. */
export class PausedTranscriptUpdates {
  private previous = new Map<string, { content: unknown; nodes: unknown; state: unknown }>();
  private changed = new Set<string>();
  private initialized = false;
  update(messages: any[], following: boolean): number {
    const next = new Map<string, { content: unknown; nodes: unknown; state: unknown }>();
    for (const message of messages) {
      const id = String(message.id), value = { content: message.content, nodes: message.nodes, state: message.state || message.status };
      const old = this.previous.get(id);
      if (this.initialized && !following && (!old || old.content !== value.content || old.nodes !== value.nodes || old.state !== value.state)) this.changed.add(id);
      next.set(id, value);
    }
    this.previous = next; this.initialized = true;
    if (following) this.changed.clear();
    for (const id of this.changed) if (!next.has(id)) this.changed.delete(id);
    return this.changed.size;
  }
}
