import stringWidth from 'string-width';
import { graphemes, safeText } from './terminal.js';
import { resolveTheme, type ThemeName, type ThemeTokens } from './theme.js';

export type MarkdownKind = 'text' | 'heading' | 'list' | 'quote' | 'code' | 'fence' | 'table' | 'thematicBreak';
export type MarkdownRow = {
  text: string;
  raw: string;
  kind: MarkdownKind;
  token: keyof ThemeTokens;
  /** A non-repeated, linear label for screen readers. Wrapped visual rows are empty. */
  screenReader: string;
};
export type MarkdownRender = { raw: string; rows: MarkdownRow[]; lines: string[]; screenReader: string };
export type MarkdownOptions = { width: number; theme?: ThemeName | ThemeTokens; screenReader?: boolean };

type Block = {
  kind: MarkdownKind;
  raw: string;
  text?: string;
  marker?: string;
  depth?: number;
  checked?: boolean;
  info?: string;
  fenceClosed?: boolean;
  rows?: string[][];
};

const fenceStart = /^ {0,3}(`{3,}|~{3,})[ \t]*(.*)$/;
const heading = /^ {0,3}(#{1,6})(?:[ \t]+(.*?)\s*#*[ \t]*|$)$/;
const listItem = /^(\s*)([-+*]|\d+[.)])[ \t]+(.*)$/;
const quoteLine = /^\s*>[ \t]?(.*)$/;
const tableDividerCell = /^:?-{3,}:?$/;

function isThematicBreak(value: string): boolean {
  const text = value.trim();
  return /^(?:\*\s*){3,}$/.test(text) || /^(?:-\s*){3,}$/.test(text) || /^(?:_\s*){3,}$/.test(text);
}

function splitTable(value: string): string[] {
  const text = value.trim();
  const withoutEdges = text.startsWith('|') ? text.slice(1) : text;
  const body = withoutEdges.endsWith('|') ? withoutEdges.slice(0, -1) : withoutEdges;
  const cells: string[] = []; let current = ''; let code = false; let escaped = false;
  for (const char of body) {
    if (escaped) { current += char; escaped = false; continue; }
    if (char === '\\') { current += char; escaped = true; continue; }
    if (char === '`') { code = !code; current += char; continue; }
    if (char === '|' && !code) { cells.push(current.trim()); current = ''; } else current += char;
  }
  cells.push(current.trim());
  return cells;
}

function isTableDivider(value: string): boolean {
  const cells = splitTable(value);
  return cells.length > 1 && cells.every(cell => tableDividerCell.test(cell.replace(/\s/g, '')));
}

function isSpecialStart(lines: string[], index: number): boolean {
  const line = lines[index];
  return !line.trim() || Boolean(fenceStart.test(line) || heading.test(line) || quoteLine.test(line) || listItem.test(line) || isThematicBreak(line) || (lines[index + 1] && isTableDivider(lines[index + 1])));
}

function parseBlocks(value: string): Block[] {
  const lines = value.split('\n'); const blocks: Block[] = []; let index = 0;
  while (index < lines.length) {
    const source = lines[index];
    if (!source.trim()) { blocks.push({ kind: 'text', raw: source, text: '' }); index++; continue; }
    const fence = source.match(fenceStart);
    if (fence) {
      const marker = fence[1][0], length = fence[1].length, info = fence[2].trim();
      blocks.push({ kind: 'fence', raw: source, marker, info, fenceClosed: false }); index++;
      while (index < lines.length) {
        const close = lines[index].match(new RegExp(`^ {0,3}${marker}{${length},}[ \\t]*$`));
        if (close) { blocks.push({ kind: 'fence', raw: lines[index], marker, info: '', fenceClosed: true }); index++; break; }
        blocks.push({ kind: 'code', raw: lines[index], text: lines[index] }); index++;
      }
      // An unfinished fence deliberately remains open in the projection. No synthetic close is emitted.
      continue;
    }
    const atx = source.match(heading);
    if (atx) { blocks.push({ kind: 'heading', raw: source, text: atx[2] || '' }); index++; continue; }
    if (isThematicBreak(source)) { blocks.push({ kind: 'thematicBreak', raw: source }); index++; continue; }
    if (quoteLine.test(source)) {
      const start = index; const content: string[] = [];
      while (index < lines.length && quoteLine.test(lines[index])) { content.push(lines[index].replace(/^\s*>[ \t]?/, '')); index++; }
      blocks.push({ kind: 'quote', raw: lines.slice(start, index).join('\n'), text: content.join('\n') }); continue;
    }
    if (listItem.test(source)) {
      while (index < lines.length) {
        const item = lines[index].match(listItem); if (!item) break;
        const task = item[3].match(/^\[([ xX])\][ \t]+(.*)$/);
        blocks.push({ kind: 'list', raw: lines[index], text: task ? task[2] : item[3], marker: item[2], depth: Math.floor(item[1].replace(/\t/g, '    ').length / 2), checked: task ? task[1].toLowerCase() === 'x' : undefined }); index++;
      }
      continue;
    }
    if (lines[index + 1] && isTableDivider(lines[index + 1])) {
      const start = index; const tableRows = [splitTable(lines[index])]; index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) { tableRows.push(splitTable(lines[index])); index++; }
      blocks.push({ kind: 'table', raw: lines.slice(start, index).join('\n'), rows: tableRows }); continue;
    }
    // Setext heading. The underline is consumed as part of the same block.
    if (lines[index + 1] && /^ {0,3}(=+|-+)[ \t]*$/.test(lines[index + 1])) {
      blocks.push({ kind: 'heading', raw: `${source}\n${lines[index + 1]}`, text: source }); index += 2; continue;
    }
    const paragraph: string[] = [source]; index++;
    while (index < lines.length && !isSpecialStart(lines, index)) { paragraph.push(lines[index]); index++; }
    blocks.push({ kind: 'text', raw: paragraph.join('\n'), text: paragraph.join('\n') });
  }
  return blocks;
}

/** Remove Markdown decoration while retaining literal/unknown syntax safely. */
function inline(value: string): string {
  const protectedValues: string[] = [];
  const protect = (text: string) => { const id = protectedValues.push(text) - 1; return `\uE000${id}\uE001`; };
  // Protect code spans first so escapes inside code stay literal. Escaped
  // punctuation outside code is then protected before emphasis/link passes.
  let text = value.replace(/(?<!\\)(`+)([^\n]*?)\1/g, (_match, _ticks, body) => protect(body));
  text = text.replace(/\\([\\`*_[\]{}#.!|~>+\-()])/g, (_match, character) => protect(character));
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  text = text.replace(/\[([^\]]+)\]\(([^\n)]*(?:\([^\n)]*\)[^\n)]*)?)\)/g, (_match, label, url) => protect(`${label} (${url})`));
  text = text.replace(/\[([^\]]+)\]\[([^\]]*)\]/g, (_match, label) => protect(label));
  text = text.replace(/~~([^\n]+?)~~/g, '$1');
  text = text.replace(/\*\*([^\n]+?)\*\*/g, '$1').replace(/__([^\n]+?)__/g, '$1');
  text = text.replace(/(^|[^\\\w])\*([^*\n]+)\*(?=$|[^\\\w])/g, '$1$2');
  text = text.replace(/(^|[^\\\w])_([^_\n]+)_(?=$|[^\\\w])/g, '$1$2');
  return text.replace(/\uE000(\d+)\uE001/g, (_match, id) => protectedValues[Number(id)] || '');
}

function displayList(block: Block): string {
  const indent = ' '.repeat(Math.min(12, (block.depth || 0) * 2));
  const marker = block.checked === undefined ? (block.marker && /^\d/.test(block.marker) ? `${block.marker} ` : '• ') : `${block.checked ? '☑' : '☐'} `;
  return `${indent}${marker}${inline(block.text || '')}`;
}

function screenReaderFor(block: Block): string {
  if (block.kind === 'heading') return `Heading: ${inline(block.text || '')}`;
  if (block.kind === 'list') return `List item: ${inline(block.text || '')}${block.checked === undefined ? '' : block.checked ? ' (checked)' : ' (unchecked)'}`;
  if (block.kind === 'quote') return `Quote: ${inline(block.text || '').replace(/\n/g, ' ')}`;
  if (block.kind === 'code') return block.text || '';
  if (block.kind === 'fence') return block.fenceClosed ? 'code block end' : block.info ? `code block ${block.info}` : 'code block';
  if (block.kind === 'thematicBreak') return 'Separator';
  if (block.kind === 'table') return (block.rows || []).map(row => `Table row: ${row.map(inline).join('; ')}`).join('\n');
  return inline(block.text || '');
}

function wrap(text: string, width: number): string[] {
  const result: string[] = []; let line = ''; let cells = 0;
  for (const part of graphemes(text)) {
    if (part === '\n') { result.push(line); line = ''; cells = 0; continue; }
    const size = stringWidth(part);
    if (line && cells + size > width) { result.push(line); line = ''; cells = 0; }
    line += size > width ? '�' : part; cells += Math.min(width, size);
  }
  result.push(line); return result;
}

function rowsForBlock(block: Block, width: number): MarkdownRow[] {
  const token: keyof ThemeTokens = block.kind === 'code' || block.kind === 'fence' ? 'code' : block.kind === 'heading' ? 'selected' : block.kind === 'quote' || block.kind === 'thematicBreak' ? 'muted' : 'text';
  let visual: string;
  if (block.kind === 'fence') visual = block.fenceClosed ? '└─ code' : block.info ? `┌─ code (${block.info})` : '┌─ code';
  else if (block.kind === 'code') visual = `│ ${block.text || ''}`;
  else if (block.kind === 'heading') visual = `▌ ${inline(block.text || '')}`;
  else if (block.kind === 'quote') visual = `│ ${inline(block.text || '')}`;
  else if (block.kind === 'list') visual = displayList(block);
  else if (block.kind === 'thematicBreak') visual = '─'.repeat(Math.max(1, Math.min(width, 8)));
  else if (block.kind === 'table') visual = (block.rows || []).map(row => `│ ${row.map(inline).join(' │ ')} │`).join('\n');
  else visual = inline(block.text || '');
  const reader = screenReaderFor(block); const result: MarkdownRow[] = [];
  for (const [index, line] of wrap(visual, width).entries()) result.push({ text: line, raw: block.raw, kind: block.kind, token, screenReader: index === 0 ? reader : '' });
  return result;
}

/** Safe, bounded Markdown projection. It never executes HTML, URLs, ANSI or code. */
export function renderMarkdown(input: string, options: MarkdownOptions): MarkdownRender {
  const raw = String(input ?? '');
  const normalized = raw.replace(/\r\n?/g, '\n');
  const width = Math.max(1, Math.floor(options.width));
  // Resolve the theme as part of the projection contract; rows expose semantic tokens,
  // never ANSI escape sequences. The Ink caller applies the actual token color.
  if (typeof options.theme === 'object') void options.theme; else resolveTheme(options.theme);
  const rows = parseBlocks(safeText(normalized)).flatMap(block => rowsForBlock(block, width));
  return { raw, rows, lines: rows.map(row => row.text), screenReader: rows.map(row => row.screenReader).filter(Boolean).join('\n') };
}

export function markdownThemeToken(row: MarkdownRow, theme: ThemeTokens): string { return theme[row.token] as string; }
