import test from 'node:test';
import assert from 'node:assert/strict';
import { TranscriptLayout, type TranscriptAnchor } from '../src/transcript-layout.js';
import { graphemes, safeText, wrap } from '../src/terminal.js';
import { renderMarkdown } from '../src/markdown.js';

type Message = { id: string; content: string };
const make = (options = {}) => new TranscriptLayout<Message>({ textOf: message => message.content, ...options });
const lines = (layout: TranscriptLayout<Message>, messages: Message[], width: number) =>
  layout.window(messages, { width, height: 100_000 }).rows.map(row => row.text);
const oracle = (messages: Message[], width: number) => messages.flatMap(message => wrap(message.content, width));

test('bounded segmentation matches canonical grapheme/cell wrapping without dropping content', () => {
  const messages = [{ id: 'a', content: '标题\n\nA中e\u0301👨‍👩‍👧‍👦👍🏽🇨🇳Z\n\t\x1b[31m\r\n尾\n' }];
  for (const width of [1, 2, 3, 7, 15, 80]) assert.deepEqual(lines(make(), messages, width), oracle(messages, width));
});

test('chunk boundaries retain surrogate, combining, ZWJ, RI and prepend context', () => {
  for (const sequence of ['e\u0301', '👨‍👩‍👧‍👦', '👍🏽', '🇨🇳🇺🇸🇯', '\u0600中', '\r\n']) {
    for (const offset of [2046, 2047, 2048]) {
      const messages = [{ id: 'boundary', content: 'x'.repeat(offset) + sequence + 'tail' }];
      assert.deepEqual(lines(make(), messages, 79), oracle(messages, 79), `${offset}/${sequence}`);
    }
  }
});

test('every UTF-16 append boundary reflows the final grapheme and hard line exactly', () => {
  const layout = make(), message = { id: 'stream', content: 'ABC' };
  lines(layout, [message], 4);
  const stream = 'e\u0301👨‍👩‍👧‍👦🇨🇳🇺🇸👍🏽\r\n\nAB\u0301\u0301中文';
  for (let index = 0; index < stream.length; index++) {
    message.content += stream[index];
    assert.deepEqual(lines(layout, [message], 4), oracle([message], 4), `append code unit ${index}`);
  }
});

test('streaming insertion before a projected footer and final tool replacement keep exact text', () => {
  const layout = new TranscriptLayout<Message>({ textOf: message => `主理人 · \n${message.content}\n▸ fixture tool\n` });
  const message = { id: 'projected', content: '中文正文'.repeat(2000) };
  for (const suffix of ['', '新', 'e', '\u0301', '👨', '\u200d', '👩']) {
    message.content += suffix;
    assert.deepEqual(lines(layout, [message], 80), wrap(`主理人 · \n${message.content}\n▸ fixture tool\n`, 80));
  }
});

test('replace, shrink, middle edits and width changes invalidate the necessary layout', () => {
  const layout = make(), message = { id: 'edit', content: '第一行\nABC👩‍💻XYZ\n结尾' };
  for (const [content, width] of [
    [message.content, 7], ['中间整体替换\n\n新尾巴', 7], ['短', 7], ['中间整体替换\n\n新尾巴', 3],
    ['中间整体替换\n\n新尾巴继续', 1], ['中间整体替换\n\n新尾巴继续', 12], ['', 12],
  ] as const) {
    message.content = content;
    assert.deepEqual(lines(layout, [message], width), oracle([message], width));
  }
});

test('window returns a fixed row budget and crosses message boundaries in both directions', () => {
  const messages = Array.from({ length: 60 }, (_, index) => ({ id: String(index), content: `row ${index}\nsecond ${index}` }));
  const all = oracle(messages, 30), layout = make();
  let result = layout.window(messages, { width: 30, height: 9 });
  assert.deepEqual(result.rows.map(row => row.text), all.slice(-9));
  assert.equal(result.following, true);
  for (const delta of [-6, -45, 11, 10_000, -10_000]) {
    const anchor = result.anchor!;
    const start = messages.findIndex(message => message.id === anchor.messageId) * 2 + (anchor.lineBreaks || 0);
    result = layout.window(messages, { width: 30, height: 9, following: false, anchor, scrollDelta: delta });
    const expected = Math.max(0, Math.min(all.length - 9, start + delta));
    assert.deepEqual(result.rows.map(row => row.text), all.slice(expected, expected + 9));
    assert.equal(result.hasEarlier, expected > 0);
    assert.equal(result.hasLater, expected < all.length - 9);
    assert.equal(result.following, !result.hasLater);
  }
});

test('new anchors distinguish empty lines and preserve source position through resize and streaming', () => {
  const layout = make(), messages = [{ id: 'a', content: '标题\n\n\n中文ABCDEFGHIJ\n后续一\n后续二\n后续三' }];
  let result = layout.window(messages, { width: 8, height: 2, following: false, anchor: { messageId: 'a', offset: 2, lineBreaks: 2 } });
  assert.equal(result.rows[0].text, '');
  assert.equal(result.anchor!.lineBreaks, 2);
  const blank = result.anchor;
  messages[0].content += '\n追加正文';
  result = layout.window(messages, { width: 80, height: 2, following: false, anchor: blank });
  assert.equal(result.rows[0].text, '');
  assert.deepEqual(result.anchor, blank);
  assert.equal(result.following, false);
  const wrapped = layout.window(messages, { width: 4, height: 2, following: false, anchor: { messageId: 'a', offset: 8, lineBreaks: 3 } });
  const resized = layout.window(messages, { width: 6, height: 2, following: false, anchor: wrapped.anchor });
  const sourcePosition = wrapped.anchor!.offset + wrapped.anchor!.lineBreaks!;
  assert.ok(resized.rows[0].offset + resized.rows[0].lineBreaks <= sourcePosition);
  assert.ok(resized.rows[1].offset + resized.rows[1].lineBreaks > sourcePosition);
  // Old saved anchors remain usable, with the same old last-equal-offset rule.
  const old = layout.window(messages, { width: 8, height: 2, following: false, anchor: { messageId: 'a', offset: 2 } });
  assert.equal(old.rows[0].text, '中文ABCD');
});

test('layout never projects offscreen history, and eviction cannot truncate canonical messages', () => {
  const seen = new Set<string>();
  const layout = new TranscriptLayout<Message>({ textOf: message => { seen.add(message.id); return message.content; }, maxCachedMessages: 2, maxCachedCharacters: 20 });
  const messages = Array.from({ length: 10_000 }, (_, index) => ({ id: String(index), content: `message ${index}` }));
  const current = layout.window(messages, { width: 80, height: 12 });
  assert.equal(current.rows.length, 12); assert.ok(seen.size <= 12);
  const first = layout.window(messages, { width: 80, height: 12, following: false, anchor: { messageId: '0', offset: 0 } });
  assert.equal(first.rows[0].text, 'message 0');
  assert.deepEqual(layout.window(messages, { width: 80, height: 12 }).rows, current.rows);
  const long = [{ id: 'oversize', content: '完整正文🙂'.repeat(1000) }];
  assert.deepEqual(lines(layout, long, 13), oracle(long, 13));
});

test('following versus reading history remains distinct when messages arrive or an anchored message disappears', () => {
  const layout = make(), messages = Array.from({ length: 20 }, (_, index) => ({ id: String(index), content: `line ${index}` }));
  const anchor: TranscriptAnchor = { messageId: '3', offset: 0, lineBreaks: 0 };
  const old = layout.window(messages, { width: 80, height: 5, following: false, anchor });
  messages.push({ id: 'new', content: 'new live content' });
  assert.deepEqual(layout.window(messages, { width: 80, height: 5, following: false, anchor: old.anchor }).rows, old.rows);
  assert.equal(layout.window(messages, { width: 80, height: 5 }).rows.at(-1)!.text, 'new live content');
  messages.splice(3, 1);
  const deleted = layout.window(messages, { width: 80, height: 5, following: false, anchor });
  assert.equal(deleted.rows[0].text, 'line 0');
  assert.ok(!deleted.rows.some(row => row.text === 'line 3'));
});

test('width-aware Human Surface projection keeps rich row tokens and raw provenance', () => {
  const layout = new TranscriptLayout<Message>({
    textOf: message => message.content,
    project: (message, width) => renderMarkdown(message.content, { width, theme: 'mono' }),
  });
  const result = layout.window([{ id: 'rich', content: '# 标题\n```\n中文代码\n```' }], { width: 8, height: 20 });
  assert.equal(result.rows[0].token, 'selected');
  assert.equal(result.rows.some(row => row.token === 'code'), true);
  assert.equal(result.rows[0].raw, '# 标题');
  assert.match(result.rows.find(row => row.token === 'code')!.screenReader || '', /中文代码|code/);
});
