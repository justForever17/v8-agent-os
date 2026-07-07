const assert = require('node:assert/strict');
const test = require('node:test');
const { buildStartupHtml } = require('../lib/startup-screen.cjs');

test('startup screen uses product topbar language and window controls', () => {
  const html = buildStartupHtml({ markUrl: 'file:///tmp/product-mark.png', detail: '正在等待服务就绪' });
  assert.match(html, /V8 Agent OS/);
  assert.match(html, /正在准备 V8OS/);
  assert.match(html, /Engine 运行核心/);
  assert.match(html, /Admin 配置中心/);
  assert.match(html, /Web 聊天界面/);
  assert.match(html, /window\.v8osShell\.minimize/);
});
