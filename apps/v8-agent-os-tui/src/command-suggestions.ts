import { clip } from './terminal.js';

// Metadata belongs to the existing action catalog; this module only projects it.
export type CommandEntry = { command?: string; description?: string; label: string; disabled?: boolean };
export function commandMatches<T extends CommandEntry>(actions: T[], query: string): T[] {
  const term = query.trim().replace(/^\//, '').toLocaleLowerCase();
  return actions.filter(action => action.command && [action.command, action.label, action.description]
    .some(value => value?.toLocaleLowerCase().includes(term)))
    .sort((a, b) => Number(b.command === term) - Number(a.command === term));
}

/** Keep the selected action visible; the caller reserves the composer first. */
export function suggestionRows(actions: CommandEntry[], query: string, selected: number, width: number, maxRows: number) {
  const matches = commandMatches(actions, query), total = actions.filter(action => action.command).length;
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
