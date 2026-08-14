const assert = require('node:assert/strict');
const test = require('node:test');
const {
  classifyWindowOpen,
  isTrustedAdminAuthIpcSource,
  isTrustedIpcSource,
  isTrustedProductUrl,
  trustedProductOrigins,
} = require('../lib/surface-security.cjs');

const origins = trustedProductOrigins([
  'http://127.0.0.1:9527',
  'http://127.0.0.1:9528',
]);

test('only the configured Web and Admin origins are trusted product surfaces', () => {
  assert.equal(isTrustedProductUrl('http://127.0.0.1:9527/chat?id=session-1', origins), true);
  assert.equal(isTrustedProductUrl('http://127.0.0.1:9528/admin', origins), true);
  assert.equal(isTrustedProductUrl('http://127.0.0.1:9527.evil.example/chat', origins), false);
  assert.equal(isTrustedProductUrl('file:///tmp/fake.html', origins), false);
});

test('new windows are always denied and only safe destinations are routed elsewhere', () => {
  assert.equal(classifyWindowOpen('http://127.0.0.1:9528/admin/models', origins), 'product');
  assert.equal(classifyWindowOpen('https://docs.github.com/actions', origins), 'external');
  assert.equal(classifyWindowOpen('https://user:pass@example.com/private', origins), 'deny');
  assert.equal(classifyWindowOpen('file:///etc/passwd', origins), 'deny');
  assert.equal(classifyWindowOpen('javascript:alert(1)', origins), 'deny');
});

test('IPC requires the main window top frame and grants startup pages only explicit minimal access', () => {
  const trusted = { senderMatches: true, isMainFrame: true, frameUrl: 'http://127.0.0.1:9527/chat', origins };
  assert.equal(isTrustedIpcSource(trusted), true);
  assert.equal(isTrustedIpcSource({ ...trusted, senderMatches: false }), false);
  assert.equal(isTrustedIpcSource({ ...trusted, isMainFrame: false }), false);
  assert.equal(isTrustedIpcSource({ ...trusted, frameUrl: 'https://example.com' }), false);

  const startup = { ...trusted, frameUrl: 'data:text/html;charset=utf-8,%3Chtml%3E' };
  assert.equal(isTrustedIpcSource(startup), false);
  assert.equal(isTrustedIpcSource({ ...startup, allowStartup: true }), true);
});

test('Admin sign-out lock IPC is accepted only from the canonical Admin dashboard', () => {
  const source = {
    senderMatches: true,
    isMainFrame: true,
    frameUrl: 'http://127.0.0.1:9528/admin/models',
    origins,
    adminBaseUrl: 'http://127.0.0.1:9528',
  };
  assert.equal(isTrustedAdminAuthIpcSource(source), true);
  assert.equal(isTrustedAdminAuthIpcSource({ ...source, frameUrl: 'http://127.0.0.1:9527/chat' }), false);
  assert.equal(isTrustedAdminAuthIpcSource({ ...source, frameUrl: 'http://127.0.0.1:9528/login' }), false);
  assert.equal(isTrustedAdminAuthIpcSource({ ...source, frameUrl: 'http://127.0.0.1:9528/admin/verify' }), false);
  assert.equal(isTrustedAdminAuthIpcSource({ ...source, frameUrl: 'http://localhost:9528/admin' }), false);
  assert.equal(isTrustedAdminAuthIpcSource({ ...source, isMainFrame: false }), false);
});
