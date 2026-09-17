import stringWidth from 'string-width';
import { StringDecoder } from 'node:string_decoder';

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
export const graphemes = (text: string) => Array.from(segmenter.segment(text), s => s.segment);
// Make control bytes visible, including incomplete/split escape sequences.
// No user-controlled character can reach the renderer as a terminal command.
export function safeText(value: unknown): string {
  return String(value ?? '').replace(/\r\n?/g, '\n').replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/g,
    c => c === '\x1b' ? '␛' : `⟦${c.codePointAt(0)!.toString(16)}⟧`).replace(/\t/g, '    ');
}
export function wrap(text: string, width: number): string[] {
  width = Math.max(1, width);
  const lines: string[] = []; let line = '', cells = 0;
  for (const g of graphemes(safeText(text))) {
    if (g === '\n') { lines.push(line); line = ''; cells = 0; continue; }
    const size = stringWidth(g);
    if (cells + size > width && line) { lines.push(line); line = ''; cells = 0; }
    line += size > width ? '�' : g; cells += Math.min(size, width);
  }
  lines.push(line); return lines;
}
export function clip(text: string, width: number): string {
  const clean = safeText(text).replace(/\n/g, ' ');
  if (stringWidth(clean) <= width) return clean;
  let result = '';
  for (const g of graphemes(clean)) { if (stringWidth(result + g) > width - 1) break; result += g; }
  return result + '…';
}
export type Editor = { text: string; cursor: number; pasted: boolean };
export const editor = (text = ''): Editor => ({ text, cursor: graphemes(text).length, pasted: false });
export function edit(state: Editor, action: string, value = ''): Editor {
  const parts = graphemes(state.text); let cursor = state.cursor;
  if (action === 'left') cursor--;
  else if (action === 'right') cursor++;
  else if (action === 'home') { while (cursor > 0 && parts[cursor - 1] !== '\n') cursor--; }
  else if (action === 'end') { while (cursor < parts.length && parts[cursor] !== '\n') cursor++; }
  else if (action === 'backspace' && cursor > 0) { parts.splice(--cursor, 1); }
  else if (action === 'delete') parts.splice(cursor, 1);
  else if (action === 'insert' || action === 'paste') {
    const before = parts.slice(0, cursor).join('');
    const inserted = value.replace(/\r\n?/g, '\n');
    const next = before + inserted + parts.slice(cursor).join('');
    // Re-segment after insertion: combining marks/ZWJ can join an existing cell.
    return { text: next, cursor: graphemes(before + inserted).length, pasted: state.pasted || action === 'paste' };
  }
  return { ...state, text: parts.join(''), cursor: Math.max(0, Math.min(parts.length, cursor)) };
}
export type Input = { key: string; text?: string };
const keys: Record<string, string> = {
  '\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left',
  '\x1b[H': 'home', '\x1b[F': 'end', '\x1b[1~': 'home', '\x1b[4~': 'end',
  '\x1b[3~': 'delete', '\x1b[5~': 'pageup', '\x1b[6~': 'pagedown', '\x1b[Z': 'backtab',
  '\x1bOP': 'f1', '\x1bOQ': 'f2', '\x1bOR': 'f3', '\x1bOS': 'f4',
  '\x1b[11~': 'f1', '\x1b[12~': 'f2', '\x1b[13~': 'f3', '\x1b[14~': 'f4',
  '\x1b[19~': 'f8', '\x1b[20~': 'f9', '\x1b\r': 'newline',
};
export class InputDecoder {
  private decoder = new StringDecoder('utf8');
  private pending = ''; private paste: string | null = null;
  push(chunk: Buffer | string): Input[] {
    this.pending += typeof chunk === 'string' ? chunk : this.decoder.write(chunk);
    const result: Input[] = [];
    while (this.pending) {
      if (this.paste !== null) {
        const end = this.pending.indexOf('\x1b[201~');
        if (end < 0) {
          // Retain a possible marker prefix across arbitrarily split writes.
          const keep = Math.min(5, this.pending.length);
          this.paste += this.pending.slice(0, -keep); this.pending = this.pending.slice(-keep); break;
        }
        result.push({ key: 'paste', text: this.paste + this.pending.slice(0, end) });
        this.paste = null; this.pending = this.pending.slice(end + 6); continue;
      }
      if (this.pending.startsWith('\x1b[200~')) { this.paste = ''; this.pending = this.pending.slice(6); continue; }
      if (this.pending[0] === '\x1b') {
        const match = Object.keys(keys).find(k => this.pending.startsWith(k));
        if (match) { result.push({ key: keys[match] }); this.pending = this.pending.slice(match.length); continue; }
        if ([...Object.keys(keys), '\x1b[200~', '\x1b[201~'].some(k => k.startsWith(this.pending))) break;
        const unknown = this.pending.match(/^\x1b(?:\[[0-?]*[ -/]*[@-~]|O.)/);
        if (unknown) { this.pending = this.pending.slice(unknown[0].length); continue; }
        result.push({ key: 'escape' }); this.pending = this.pending.slice(1); continue;
      }
      const normal = this.pending.match(/^[^\x00-\x1f\x7f]+/);
      if (normal) {
        // Unbracketed multi-line bursts are text too, never commands/submits.
        const burst = this.pending.match(/^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+/)!;
        const text = burst[0]; result.push({ key: /[\r\n]/.test(text) ? 'paste' : 'text', text });
        this.pending = this.pending.slice(text.length); continue;
      }
      const code = this.pending.charCodeAt(0); this.pending = this.pending.slice(1);
      result.push({ key: code === 13 || code === 10 ? 'enter' : code === 9 ? 'tab' : code === 127 || code === 8 ? 'backspace' : `ctrl-${String.fromCharCode(code + 96)}` });
    }
    return result;
  }
  flush(): Input[] {
    if (this.paste !== null) return []; // Never turn an unfinished paste into commands.
    if (!this.pending) return [];
    const pending = this.pending; this.pending = '';
    return pending === '\x1b' ? [{ key: 'escape' }] : [{ key: 'text', text: safeText(pending) }];
  }
  finishPaste(): Input[] {
    if (this.paste === null) return this.flush();
    // Idle is not a protocol boundary. Save partial text but stay in paste
    // mode: a slow later chunk containing F9 must not submit the partial draft.
    let keep = 0;
    for (let i = 1; i < 6; i++) if (this.pending.endsWith('\x1b[201~'.slice(0, i))) keep = i;
    const text = this.paste + this.pending.slice(0, keep ? -keep : undefined);
    this.pending = keep ? this.pending.slice(-keep) : ''; this.paste = '';
    return text ? [{ key: 'paste', text }] : [];
  }
}
export function dimensions(columns: number, rows: number, left: boolean, right: boolean) {
  const small = columns < 60 || rows < 18;
  const sidebar = !small && columns >= 96 && left && !(right && columns < 128) ? 27 : 0;
  const detail = !small && columns >= 96 && right ? 35 : 0;
  return { columns, rows, small, sidebar, detail, chat: Math.max(1, columns - sidebar - detail), history: Math.max(1, rows - (small ? 6 : 10)) };
}
