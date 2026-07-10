const assert = require('node:assert/strict');
const test = require('node:test');
const { buildStartupHtml } = require('../lib/startup-screen.cjs');

test('startup screen uses minimal centered product mark and shimmer brand', () => {
  const html = buildStartupHtml({ markUrl: 'file:///tmp/product-mark.png', detail: '正在等待服务就绪' });
  assert.match(html, /V8 Agent OS/);
  assert.match(html, /class="product-mark"/);
  assert.match(html, /class="brand-text"/);
  assert.match(html, /white-space: nowrap/);
  assert.match(html, /font-size: clamp\(28px, 5vw, 64px\)/);
  assert.match(html, /linear-gradient\(105deg[\s\S]*linear-gradient\(180deg/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(html, /正在准备 V8OS/);
  assert.doesNotMatch(html, /Engine 运行核心/);
  assert.doesNotMatch(html, /window\.v8osShell\.minimize/);
});
