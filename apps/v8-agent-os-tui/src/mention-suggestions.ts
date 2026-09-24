import { clip, graphemes } from './terminal.js';
import type { MentionCandidate, MentionGroup } from './mentions.js';

export const mentionGroups: MentionGroup[] = ['all', 'files', 'sessions', 'mcp', 'skills', 'agents', 'plugins'];
export const mentionGroupLabels: Record<MentionGroup, string> = {
  all: '全部', files: '文件', sessions: '会话', mcp: 'MCP', skills: 'Skills', agents: 'Agents', plugins: 'Plugins',
};

export type MentionSuggestionState = {
  query: string;
  group: MentionGroup;
  selected: number;
  candidates: MentionCandidate[];
  loading: boolean;
  error?: string;
  triggerStart: number;
  triggerEnd: number;
  originalText: string;
  originalCursor: number;
  sessionId: string;
};

export function mentionMatches(state: MentionSuggestionState): MentionCandidate[] {
  const term = state.query.trim().toLocaleLowerCase();
  return state.candidates.filter(item => (state.group === 'all' || item.group === state.group)
    && (!term || `${item.value} ${item.label} ${item.description || ''}`.toLocaleLowerCase().includes(term)));
}

export function selectedMention(state: MentionSuggestionState): MentionCandidate | undefined {
  const matches = mentionMatches(state);
  return matches[Math.max(0, Math.min(matches.length - 1, state.selected))];
}

export function moveMentionSelection(state: MentionSuggestionState, delta: number): MentionSuggestionState {
  const count = mentionMatches(state).length;
  return { ...state, selected: count ? Math.max(0, Math.min(count - 1, state.selected + delta)) : 0 };
}

export function switchMentionGroup(state: MentionSuggestionState, delta: number): MentionSuggestionState {
  const index = mentionGroups.indexOf(state.group);
  const group = mentionGroups[(index + delta + mentionGroups.length) % mentionGroups.length];
  const next = { ...state, group, selected: 0 };
  return moveMentionSelection(next, 0);
}

/** The renderer contract is plain terminal rows; it never receives raw catalog data. */
export function mentionSuggestionRows(state: MentionSuggestionState, width: number, maxRows: number) {
  const matches = mentionMatches(state);
  const rows = Math.max(0, Math.floor(maxRows));
  if (!rows) return { lines: [] as string[], selectedRow: -1, count: matches.length };
  const tabs = mentionGroups.map(group => group === state.group ? `[${mentionGroupLabels[group]}]` : mentionGroupLabels[group]).join(' ');
  const lines = [`@ 候选 · ${tabs}${state.loading ? ' · 读取目录…' : ''}`];
  if (rows > 1 && state.error) lines.push(clip(`无法读取候选：${state.error}`, width));
  else if (rows > 1 && !matches.length) lines.push(clip(state.loading ? '正在读取当前工作区与 Engine 目录…' : '当前分组没有匹配项；继续输入可筛选。', width));
  const available = Math.max(0, rows - lines.length);
  const selected = Math.max(0, Math.min(matches.length - 1, state.selected));
  const start = Math.max(0, Math.min(selected, Math.max(0, matches.length - available)));
  for (const [offset, item] of matches.slice(start, start + available).entries()) {
    const marker = start + offset === selected ? '›' : ' ';
    lines.push(clip(`${marker} @${item.value} · ${item.label}${item.description ? `  ${item.description}` : ''}${item.authorized === false ? '（未授权）' : ''}`, width));
  }
  return { lines, selectedRow: matches.length ? 1 + selected - start : -1, count: matches.length };
}

/** Find the whitespace-delimited @ token immediately before the editor cursor. */
export function mentionTokenAt(text: string, cursor: number) {
  const parts = graphemes(text), before = parts.slice(0, Math.max(0, cursor));
  let start = before.length, quoted = false;
  while (start > 0) {
    const part = before[start - 1] || '';
    if (part === '"') quoted = !quoted;
    if (!quoted && /\s/.test(part)) break;
    start--;
  }
  const token = before.slice(start).join('');
  if (!token.startsWith('@') || token.length < 1 || (start > 0 && !/\s/.test(before[start - 1] || ''))) return null;
  return { start, end: before.length, query: token.slice(1), token };
}

export function mentionReplacement(text: string, start: number, end: number, value: string) {
  const parts = graphemes(text);
  const canonical = /[\s"]/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
  const replacement = `@${canonical}`;
  const next = [...parts.slice(0, start), ...graphemes(replacement), ...parts.slice(end)].join('');
  return { text: next, cursor: start + graphemes(replacement).length };
}
