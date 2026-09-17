import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
if (!process.argv.includes('--live') || !process.env.V8_AGENT_OS_HOME) throw new Error('Use --live with an explicitly isolated V8_AGENT_OS_HOME');
const client = new Client(new ViewStore());
const workspace = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-live-workspace-'));
await client.initialize(); assert.equal(client.connection, '已连接');
const initialOwner = await client.api('/v1/client-identity/owner');
if (!initialOwner.initialized) await client.api('/v1/client-identity/bootstrap', { method: 'POST', body: { login: 'tui-fixture', name: 'TUI Fixture' } });
await client.initialize(); assert.equal(client.connection, '已连接');
await client.trustWorkspace(workspace); client.setDraft(process.argv.includes('--stream')
  ? '请用中文写一段约800字的普通自然景物描写，分8段，直接逐步输出正文，不要调用工具。此任务用于终端流式显示验收。'
  : '只回复：终端中文往返已完成。不要调用工具。');
await client.submit(); const sessionId = client.view.sessionId;
assert.ok(sessionId); assert.equal(client.draft.text, '');
const seen = new Set<string>(); const start = Date.now();
while (Date.now() - start < 120000) {
  await client.tick();
  for (const m of client.messages) if (m.role === 'assistant' && m.content) seen.add(m.content);
  if (!client.active && client.messages.some(m => m.role === 'assistant')) break;
  await new Promise(r => setTimeout(r, 200));
}
const status = client.run.status || client.snapshot.runtimeStatus;
const current = client.messages.map(m => ({ id: m.id, content: m.content, state: m.state }));
await client.attach(sessionId); assert.deepEqual(client.messages.map(m => ({ id: m.id, content: m.content, state: m.state })), current);
const attachment = path.join(workspace, '中文附件.txt'); writeFileSync(attachment, '中文附件往返');
await client.attachFile(attachment); assert.equal(client.draft.attachments.length, 1);
assert.ok(client.draft.attachments[0].sourceId);
const source = client.draft.attachments[0];
const { readFileSync } = await import('node:fs');
assert.equal(readFileSync(source.workspacePath, 'utf8'), '中文附件往返');
const owner = await client.api('/v1/client-identity/owner');
if (!owner.initialized) await client.api('/v1/client-identity/bootstrap', { method: 'POST', body: { login: 'tui-fixture', name: 'TUI Fixture' } });
const ticket = await client.api('/v1/client-identity/pairing-ticket', { method: 'POST', body: { baseUrl: 'https://fixture.example.invalid', ttlMs: 60000 } });
assert.ok(ticket.pairingCode); assert.ok(ticket.pairingId);
await client.api(`/v1/client-identity/pairing-ticket/${encodeURIComponent(ticket.pairingId)}`, { method: 'DELETE' });
const result = { realEngine: true, sessionId, status, canonicalReloadParity: true, observedAssistantVersions: seen.size, attachmentRegistered: true, phoneTicketCreatedAndRevoked: true, elapsedMs: Date.now() - start, realProviderSucceeded: ['completed', 'succeeded'].includes(status) };
client.stop();
console.log(JSON.stringify(result));
assert.ok(result.realProviderSucceeded, 'Engine conversation did not complete successfully; do not report as live chat passed');
if (process.argv.includes('--stream')) assert.ok(seen.size > 1, 'Live streaming must show more than the final snapshot');
