import test from 'node:test';
import assert from 'node:assert/strict';
import stringWidth from 'string-width';
import { editor, edit, graphemes, wrap, safeText, InputDecoder, dimensions } from '../src/terminal.js';

test('editing preserves CJK, combining marks, skin tones, flags and ZWJ graphemes', () => {
  const original = 'A中e\u0301👨‍👩‍👧‍👦👍🏽🇨🇳Z';
  let state = editor(original);
  const expected = ['A', '中', 'é', '👨‍👩‍👧‍👦', '👍🏽', '🇨🇳', 'Z'];
  assert.deepEqual(graphemes(original), expected);
  while (expected.length) { state = edit(state, 'backspace'); expected.pop(); assert.equal(state.text, expected.join('')); assert.equal(state.cursor, expected.length); }
  state = edit(editor('e'), 'insert', '\u0301'); assert.equal(state.cursor, 1); assert.equal(edit(state, 'backspace').text, '');
  state = edit(editor('中👍🏽Z'), 'left'); assert.equal(edit(state, 'delete').text, '中👍🏽');
});

test('every byte split of bracketed paste is one inert paste event, with UTF-8 intact', () => {
  const text = '中é👨‍👩‍👧‍👦\n/stop\n/exit\x03\x04\x1b[20~';
  const bytes = Buffer.from('\x1b[200~' + text + '\x1b[201~');
  for (let at = 1; at < bytes.length; at++) {
    const parser = new InputDecoder();
    const events = [...parser.push(bytes.subarray(0, at)), ...parser.push(bytes.subarray(at))];
    assert.deepEqual(events, [{ key: 'paste', text }], `split ${at}`);
  }
  const parser = new InputDecoder();
  const events = [...bytes].flatMap(byte => parser.push(Buffer.from([byte])));
  assert.deepEqual(events, [{ key: 'paste', text }]);
});

test('unfinished paste recovery and ordinary escape cannot execute pasted controls', () => {
  const parser = new InputDecoder();
  assert.deepEqual(parser.push('\x1b[200~/stop\n\x03'), []);
  assert.deepEqual(parser.flush(), []);
  assert.deepEqual(parser.finishPaste(), [{ key: 'paste', text: '/stop\n\x03' }]);
  assert.deepEqual(parser.push('\x1b[20~'), []);
  assert.deepEqual(parser.push('\x1b[201~'), [{ key: 'paste', text: '\x1b[20~' }]);
  assert.deepEqual(parser.push('你好\n/stop\n'), [{ key: 'paste', text: '你好\n/stop\n' }]);
  assert.deepEqual(parser.push('\x1b'), []); assert.deepEqual(parser.flush(), [{ key: 'escape' }]);
});

test('control payloads never reach terminal while normal text retains layout', () => {
  const hostile = '前\x1b]52;c;c2VjcmV0\x07\x1b]8;;https://bad\x1b\\链接\x1bPdata\x1b\\\x9b2J\u202e尾\n中\t文';
  const clean = safeText(hostile);
  assert.doesNotMatch(clean, /[\x00-\x08\x0b-\x1f\x7f-\x9f\u202e]/);
  assert.match(clean, /链接/); assert.match(clean, /中    文/);
  for (const width of [1, 10, 59, 64, 80, 104, 128]) {
    for (const line of wrap('中é👨‍👩‍👧‍👦👍🏽🇨🇳'.repeat(10), width)) assert.ok(stringWidth(line) <= width);
  }
  for (const cols of [128, 104, 80, 64, 59]) { const d = dimensions(cols, 24, true, true); assert.equal(d.chat + d.sidebar + d.detail, cols); }
  assert.equal(dimensions(128, 17, true, true).small, true);
});

test('128 KiB paste is not truncated or interpreted', () => {
  const text = '中/stop\n'.repeat(18000), parser = new InputDecoder();
  const events = parser.push('\x1b[200~' + text + '\x1b[201~');
  assert.equal(events.length, 1); assert.equal(events[0].text, text);
});
