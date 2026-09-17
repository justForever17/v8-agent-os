/** Reproducible CPU comparison. No Engine, provider, PTY or production writes. */
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { TranscriptLayout } from '../src/transcript-layout.js';
import { graphemes, wrap } from '../src/terminal.js';

// Freeze the old path so this comparison remains meaningful after main adopts
// the new layout. Same message projection, width and incremental input both sides.
const textOf = (message: { id: string; content: string }) => `主理人 · \n${message.content}\n`;
function oldRows(message: { id: string; content: string }, width: number) {
  let offset = 0;
  return wrap(textOf(message), width).map(text => { const row = { text, messageId: message.id, offset }; offset += graphemes(text).length; return row; });
}
const text = '多字节🙂内容 '.repeat(12_000), width = 80, height = 18;
const layout = new TranscriptLayout({ textOf });
const message = { id: 'long', content: text };
const oldCold = performance.now(); oldRows(message, width); const oldColdMs = performance.now() - oldCold;
const newCold = performance.now(); layout.window([message], { width, height }); const newColdMs = performance.now() - newCold;
const old: number[] = [], improved: number[] = [];
for (let index = 0; index < 30; index++) {
  message.content += '新';
  const before = performance.now(), expected = oldRows(message, width); old.push(performance.now() - before);
  const start = performance.now(), actual = layout.window([message], { width, height }); improved.push(performance.now() - start);
  assert.deepEqual(actual.rows.map(row => row.text), expected.slice(-height).map(row => row.text));
}
const stats = (samples: number[]) => { samples.sort((a, b) => a - b); return { p50Ms: samples[14], p95Ms: samples[28] }; };
const report = { fixture: { utf16Characters: text.length, graphemes: graphemes(text).length, width, visibleRows: height, samples: old.length },
  node: process.version, platform: process.platform, cold: { oldMs: oldColdMs, newMs: newColdMs },
  append: { old: stats(old), new: stats(improved) }, correctness: 'every visible row equals old full-text oracle in all 30 samples',
  scope: 'same-environment CPU layout only; not provider, terminal paint or cross-product comparison' };
console.log(JSON.stringify(report, null, 2));
