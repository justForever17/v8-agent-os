import { performance } from 'node:perf_hooks';
import { viewportRows } from '../src/main.js';
import { edit, editor } from '../src/terminal.js';
const messages = Array.from({ length: 10000 }, (_, i) => ({ id: `m${i}`, role: i % 2 ? 'assistant' : 'user', content: `消息 ${i} · 中文 é 👨‍👩‍👧‍👦\n` + '这是可读的历史正文。'.repeat(8), state: 'completed' }));
const cold = performance.now(); viewportRows(messages, 80); const coldMs = performance.now() - cold;
const samples: number[] = [], input: number[] = [];
for (let i = 0; i < 30; i++) {
  messages[9999] = { ...messages[9999], content: messages[9999].content + '新' };
  const start = performance.now(); viewportRows(messages, i % 2 ? 80 : 104); samples.push(performance.now() - start);
  const began = performance.now(); edit(editor('中文é👨‍👩‍👧‍👦'.repeat(100)), 'insert', '新'); input.push(performance.now() - began);
}
const stats = (values: number[]) => { values.sort((a, b) => a - b); return { p50: values[14], p95: values[28] }; };
console.log(JSON.stringify({ fixtureMessages: 10000, samples: 30, coldLayoutMs: coldMs, incrementalLayoutMs: stats(samples), inputEditMs: stats(input), rssBytes: process.memoryUsage().rss, scope: 'local projection/editor CPU; not provider or PTY paint latency' }));
