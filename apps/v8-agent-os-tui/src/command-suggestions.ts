import { clip } from './terminal.js';

// Metadata belongs to the existing action catalog; this module only projects it.
export type CommandTier = 'daily' | 'context' | 'advanced' | 'view';
export type CommandEntry = { command?: string; description?: string; label: string; disabled?: boolean; tier?: CommandTier; context?: string };

function fuzzyScore(value: string | undefined, term: string): number {
  const text = value?.toLocaleLowerCase() || '';
  if (!term) return 0;
  if (!text) return -1;
  if (text === term) return 1000;
  if (text.startsWith(term)) return 800 - Math.min(text.length, 200);
  const index = text.indexOf(term);
  if (index >= 0) return 500 - Math.min(index, 200);
  let cursor = 0;
  let gaps = 0;
  for (const character of term) {
    const found = text.indexOf(character, cursor);
    if (found < 0) return -1;
    gaps += found - cursor;
    cursor = found + 1;
  }
  return 250 - Math.min(gaps, 200);
}

export function commandMatches<T extends CommandEntry>(actions: T[], query: string): T[] {
  const term = query.trim().replace(/^\//, '').toLocaleLowerCase();
  return actions
    .map((action, index) => ({ action, index, score: Math.max(
      fuzzyScore(action.command, term),
      fuzzyScore(action.label, term),
      fuzzyScore(action.description, term),
    ) }))
    .filter(item => item.action.command && item.score >= 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(item => item.action);
}

/** The empty slash menu is intentionally small; filtering never removes entries from the full catalog. */
export function visibleCommandMatches<T extends CommandEntry>(actions: T[], query: string, context: { active?: boolean; hasAttachments?: boolean; configured?: boolean } = {}): T[] {
  const term = query.trim();
  if (term) return commandMatches(actions, term);
  return commandMatches(actions.filter(action => {
    if (!action.command || action.tier === 'advanced' || action.tier === 'view') return false;
    if (action.tier === 'context') {
      if (action.command === 'stop' || action.command === 'task' || action.command === 'retry') return Boolean(context.active);
      if (action.command === 'attach') return Boolean(context.hasAttachments);
      if (action.command === 'connect' || action.command === 'setup') return context.configured === false;
    }
    return true;
  }), '');
}

/** Keep the selected action visible; the caller reserves the composer first. */
export function suggestionRows(actions: CommandEntry[], query: string, selected: number, width: number, maxRows: number, context?: { active?: boolean; hasAttachments?: boolean; configured?: boolean }) {
  const matches = visibleCommandMatches(actions, query, context), total = actions.filter(action => action.command).length;
  selected = Math.max(0, Math.min(matches.length - 1, selected));
  maxRows = Math.max(0, Math.floor(maxRows));
  if (!maxRows) return { lines: [], selectedRow: -1, count: matches.length };
  if (!matches.length) return { lines: [`没有匹配的操作 · Esc 返回草稿`], selectedRow: -1, count: 0 };
  const selectedAction = matches[selected];
  if (maxRows === 1) return { lines: [clip(`› /${selectedAction.command} · ${selectedAction.label}`, width)], selectedRow: 0, count: matches.length };
  const detail = width < 72 && maxRows > 2 ? 1 : 0;
  const count = Math.min(6, maxRows - 1 - detail), start = Math.max(0, selected - count + 1);
  const lines = [`搜索：${query || '（输入关键词）'} · 匹配 ${matches.length} / ${total}`,
    ...matches.slice(start, start + count).map((action, i) => clip(`${start + i === selected ? '›' : ' '} /${action.command} · ${action.label}${action.disabled ? '（不可用）' : ''}${detail ? '' : `  ${action.description || ''}`}`, width))];
  if (detail) lines.push(clip(selectedAction.description || selectedAction.label, width));
  return { lines, selectedRow: selected - start + 1, count: matches.length };
}
