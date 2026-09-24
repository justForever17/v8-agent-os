import stringWidth from 'string-width';
import { graphemes, safeText } from './terminal.js';
import type { MarkdownRender, MarkdownRow } from './markdown.js';

/** Offset remains the pre-existing visible-grapheme offset. New anchors also
 * count hard line breaks, which disambiguates consecutive blank lines. */
export type TranscriptAnchor = { messageId: string; offset: number; lineBreaks?: number };
export type TranscriptRow = TranscriptAnchor & {
  text: string; lineBreaks: number;
  /** Optional Human Surface styling and provenance supplied by a projector. */
  token?: MarkdownRow['token']; raw?: string; screenReader?: string;
};
type LayoutRow = TranscriptRow & { sourceStart: number };
type Entry = { text: string; width: number; rows: LayoutRow[] };
type Position = { message: number; row: number };
export type TranscriptWindow = {
  rows: TranscriptRow[]; anchor?: TranscriptAnchor; following: boolean;
  hasEarlier: boolean; hasLater: boolean;
};
export type WindowOptions = {
  width: number; height: number; anchor?: TranscriptAnchor; following?: boolean; scrollDelta?: number;
};

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
const CHUNK_SIZE = 2048;

/** Segment bounded pieces, carrying the final cluster into the next piece.
 * This also joins split surrogate pairs, combining marks, ZWJ and RI sequences.
 * Segmenting a full streamed transcript creates avoidable large ICU temporaries. */
function* segments(text: string): Generator<{ text: string; index: number }> {
  let carry = '', consumed = 0;
  for (let start = 0; start < text.length; start += CHUNK_SIZE) {
    const chunk = carry + text.slice(start, start + CHUNK_SIZE);
    let last: { segment: string; index: number } | undefined;
    for (const part of segmenter.segment(chunk)) {
      if (last) yield { text: last.segment, index: consumed + last.index };
      last = part;
    }
    if (last) { carry = last.segment; consumed += last.index; }
  }
  if (carry) yield { text: carry, index: consumed };
}

/** Layout cache only. Canonical message ownership and human text projection
 * stay with the caller. No content truncation, state writes, timers or workers. */
export class TranscriptLayout<Message = { id: string; content: string }> {
  private cache = new Map<string, Entry>();
  private widths = new Map<string, number>();
  private cachedCharacters = 0;
  private readonly textOf: (message: Message) => string;
  private readonly project?: (message: Message, width: number) => MarkdownRender;
  private readonly idOf: (message: Message) => string;
  private readonly maxCachedMessages: number;
  private readonly maxCachedCharacters: number;

  constructor(options: {
    textOf: (message: Message) => string; idOf?: (message: Message) => string;
    /** Width-aware safe Human Surface projection. Raw text remains owned by textOf. */
    project?: (message: Message, width: number) => MarkdownRender;
    maxCachedMessages?: number; maxCachedCharacters?: number;
  }) {
    this.textOf = options.textOf;
    this.project = options.project;
    this.idOf = options.idOf || ((message: Message) => String((message as { id: string }).id));
    this.maxCachedMessages = Math.max(1, options.maxCachedMessages ?? 100);
    this.maxCachedCharacters = Math.max(1, options.maxCachedCharacters ?? 2_000_000);
  }

  clear() { this.cache.clear(); this.widths.clear(); this.cachedCharacters = 0; }

  private width(grapheme: string): number {
    if (grapheme.length === 1 && grapheme.charCodeAt(0) >= 32 && grapheme.charCodeAt(0) <= 126) return 1;
    const cached = this.widths.get(grapheme);
    if (cached !== undefined) return cached;
    const result = stringWidth(grapheme);
    if (this.widths.size >= 1024) this.widths.delete(this.widths.keys().next().value!);
    if (grapheme.length <= 128) this.widths.set(grapheme, result);
    return result;
  }

  private wrap(text: string, width: number, messageId: string, start = 0, offset = 0, lineBreaks = 0): LayoutRow[] {
    const rows: LayoutRow[] = [];
    let row: LayoutRow = { text: '', messageId, offset, lineBreaks, sourceStart: start }, cells = 0;
    for (const part of segments(text.slice(start))) {
      const value = part.text, index = start + part.index;
      if (value === '\n') {
        rows.push(row); lineBreaks++;
        row = { text: '', messageId, offset, lineBreaks, sourceStart: index + 1 }; cells = 0;
        continue;
      }
      const size = this.width(value);
      if (cells + size > width && row.text) {
        rows.push(row); row = { text: '', messageId, offset, lineBreaks, sourceStart: index }; cells = 0;
      }
      row.text += size > width ? '�' : value;
      cells += Math.min(size, width); offset++;
    }
    rows.push(row);
    return rows;
  }

  private wrapProjected(render: MarkdownRender, width: number, messageId: string): LayoutRow[] {
    const rows: LayoutRow[] = []; let offset = 0, lineBreaks = 0, sourceStart = 0;
    for (const projected of render.rows) {
      const chunks = this.wrap(projected.text, width, messageId, 0, offset, lineBreaks);
      for (const chunk of chunks) rows.push({ ...chunk, token: projected.token, raw: projected.raw, screenReader: projected.screenReader, sourceStart });
      offset += graphemes(projected.text).length; sourceStart += projected.text.length + 1; lineBreaks++;
    }
    return rows.length ? rows : [{ text: '', messageId, offset: 0, lineBreaks: 0, sourceStart: 0 }];
  }

  private entry(message: Message, width: number): Entry {
    const id = this.idOf(message), projection = this.project?.(message, width);
    const text = projection ? projection.rows.map(row => row.text).join('\n') : safeText(this.textOf(message));
    const old = this.cache.get(id);
    if (old?.width === width && old.text === text) {
      this.cache.delete(id); this.cache.set(id, old); return old;
    }
    let rows: LayoutRow[];
    if (projection) rows = this.wrapProjected(projection, width, id);
    else if (old?.width === width) {
      // Projection may append a trailing newline/tool summary after streaming
      // prose, so raw startsWith(old.text) alone misses real append updates.
      // Find the first changed code unit and keep only complete earlier rows.
      let prefix = 0;
      const maximum = Math.min(old.text.length, text.length);
      while (prefix < maximum && old.text.charCodeAt(prefix) === text.charCodeAt(prefix)) prefix++;
      let low = 0, high = old.rows.length;
      while (low < high) { const middle = (low + high) >>> 1; if (old.rows[middle].sourceStart <= prefix) low = middle + 1; else high = middle; }
      // Rewind one additional row: a combining character may change the prior
      // cluster's width and move it across a soft wrap. Never resegment a
      // substring that starts halfway through an existing cluster.
      const restart = Math.max(0, low - 2), previous = old.rows[restart];
      rows = old.rows.slice(0, restart).concat(this.wrap(text, width, id, previous.sourceStart, previous.offset, previous.lineBreaks));
    } else rows = this.wrap(text, width, id);
    const entry = { text, width, rows };
    if (old) { this.cachedCharacters -= old.text.length; this.cache.delete(id); }
    this.cache.set(id, entry); this.cachedCharacters += text.length;
    // Eviction forgets only layout. Keep one oversized current message intact;
    // there is no prefix/suffix truncation of the user's canonical content.
    while (this.cache.size > 1 && (this.cache.size > this.maxCachedMessages || this.cachedCharacters > this.maxCachedCharacters)) {
      const oldest = this.cache.keys().next().value!;
      this.cachedCharacters -= this.cache.get(oldest)!.text.length; this.cache.delete(oldest);
    }
    return entry;
  }

  window(messages: readonly Message[], options: WindowOptions): TranscriptWindow {
    const width = Math.max(1, Math.floor(options.width)), height = Math.max(1, Math.floor(options.height));
    if (!messages.length) return { rows: [], following: true, hasEarlier: false, hasLater: false };
    const entries = new Map<number, Entry>();
    const at = (index: number) => {
      let entry = entries.get(index);
      if (!entry) { entry = this.entry(messages[index], width); entries.set(index, entry); }
      return entry;
    };
    const move = (position: Position, amount: number): Position => {
      let { message, row } = position;
      while (amount < 0) {
        if (-amount <= row) { row += amount; break; }
        amount += row;
        if (message === 0) { row = 0; break; }
        message--; row = at(message).rows.length - 1; amount++;
      }
      while (amount > 0) {
        const available = at(message).rows.length - 1 - row;
        if (amount <= available) { row += amount; break; }
        amount -= available;
        if (message === messages.length - 1) { row += available; break; }
        message++; row = 0; amount--;
      }
      return { message, row };
    };
    const collect = (position: Position) => {
      const rows: LayoutRow[] = []; let { message, row } = position;
      while (rows.length < height && message < messages.length) {
        const available = at(message).rows;
        const take = Math.min(height - rows.length, available.length - row);
        rows.push(...available.slice(row, row + take)); row += take;
        if (row === available.length) { message++; row = 0; }
      }
      return { rows, hasLater: message < messages.length };
    };
    let position: Position;
    if (options.following !== false) {
      position = move({ message: messages.length - 1, row: at(messages.length - 1).rows.length - 1 }, 1 - height);
    } else {
      let message = options.anchor ? messages.findIndex(item => this.idOf(item) === options.anchor!.messageId) : 0;
      if (message < 0) message = 0;
      const rows = at(message).rows, anchor = options.anchor;
      let low = 0, high = rows.length;
      // Legacy anchors had no hard-line count: preserve their existing last
      // equal-offset behavior; every newly returned anchor is unambiguous.
      const before = (row: LayoutRow) => anchor?.lineBreaks === undefined
        ? row.offset <= (anchor?.offset ?? 0)
        : row.offset + row.lineBreaks <= anchor.offset + anchor.lineBreaks;
      while (low < high) { const middle = (low + high) >>> 1; if (before(rows[middle])) low = middle + 1; else high = middle; }
      position = { message, row: Math.max(0, low - 1) };
    }
    position = move(position, Math.trunc(options.scrollDelta || 0));
    let result = collect(position);
    if (result.rows.length < height) {
      position = move(position, result.rows.length - height); result = collect(position);
    }
    const rows = result.rows.map(({ sourceStart: _, ...row }) => row), first = rows[0];
    return {
      rows, anchor: first && { messageId: first.messageId, offset: first.offset, lineBreaks: first.lineBreaks },
      following: !result.hasLater, hasEarlier: position.message > 0 || position.row > 0, hasLater: result.hasLater,
    };
  }
}
