import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore } from '../src/persistence.js';
import { ensureWorkspaceConsent } from '../bin/engine-bootstrap.mjs';
import { mkdtempSync } from 'node:fs';
import os from 'node:os';

function createFixtureClient() {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-consent-test-')));
  const client = new Client(store, async (route: string) => {
    if (route.endsWith('/instance')) return { instanceId: 'test-inst' };
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: 'test-owner' } };
    if (route.includes('/sessions')) return { sessions: [{ id: 'sess-1', title: 'Previous Session' }] };
    return {};
  });
  client.instance = { instanceId: 'test-inst' };
  client.view.sessionId = '';
  return client;
}

test('commands list exposes /resume with daily tier and attaches session handler', () => {
  const client = createFixtureClient();
  const surface = new Surface(client);
  const commands = surface.commands();
  const resumeCmd = commands.find(c => c.command === 'resume');
  assert.ok(resumeCmd, 'Expected /resume command to be registered');
  assert.equal(resumeCmd.tier, 'daily');
  assert.equal(resumeCmd.navigation, true);
  client.stop();
});

test('submitting /resume without args opens session picker and with arg attaches to target session', async () => {
  const client = createFixtureClient();
  const surface = new Surface(client);
  let attachedSession = '';
  client.attach = async (id: string) => {
    attachedSession = id;
    client.view.sessionId = id;
  };

  // Test 1: with target session ID
  client.setDraft('/resume target-123');
  await surface.submit();
  assert.equal(attachedSession, 'target-123');
  assert.equal(client.draft.text, '');

  // Test 2: without target session ID opens sessions page
  client.setDraft('/resume');
  await surface.submit();
  assert.ok(surface.page, 'Expected surface page to open for session selection');
  assert.equal(surface.page.title, '会话');

  client.stop();
});

test('Ctrl+C interrupts running task on first press, and exits cleanly when pressed twice within 2s', async () => {
  const client = createFixtureClient();
  const surface = new Surface(client);
  let exitCalled = false;
  surface.onExit = () => { exitCalled = true; };

  // Idle state: first press does not exit, sets notice
  await surface.handle({ key: 'ctrl-c' });
  assert.equal(exitCalled, false);
  assert.match(surface.client.notice, /2 秒内再次按 Ctrl\+C/);

  // Second press within 2s immediately triggers onExit
  await surface.handle({ key: 'ctrl-c' });
  assert.equal(exitCalled, true);

  // Active run state: reset previous interrupt timestamp
  surface.lastIdleInterrupt = 0;
  exitCalled = false;
  client.snapshot = { currentRun: { id: 'active-run-1', status: 'running' }, runtimeStatus: 'running' };
  let interrupted = false;
  client.interrupt = async () => { interrupted = true; };

  await surface.handle({ key: 'ctrl-c' });
  assert.equal(exitCalled, false);
  assert.equal(interrupted, true);
  assert.match(surface.client.notice, /2 秒内再次按 Ctrl\+C/);

  // Second press in active run state also cleanly triggers onExit
  await surface.handle({ key: 'ctrl-c' });
  assert.equal(exitCalled, true);

  client.stop();
});

test('ensureWorkspaceConsent non-TTY gracefully falls through without blocking', async () => {
  const result = await ensureWorkspaceConsent({
    cwd: process.cwd(),
    english: false,
  });
  assert.equal(result.ok, true);
  assert.equal(typeof result.workspace, 'string');
});
