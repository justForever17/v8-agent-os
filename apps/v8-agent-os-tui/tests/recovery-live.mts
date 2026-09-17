import assert from 'node:assert/strict';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { engineJson } from '../../v8-agent-os-cli/src/engine_client.mjs';
import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
if (!process.argv.includes('--live') || !process.env.V8_AGENT_OS_HOME) throw new Error('--live and isolated state required');
let submitted = 0, loseReply = true;
const client = new Client(new ViewStore(), async (route, options) => {
  const result = await engineJson(route, options);
  if (route === '/v1/chat/submit') { submitted++; if (loseReply) { loseReply = false; throw new Error('injected response loss AFTER real Engine acceptance'); } }
  return result;
});
await client.initialize(); await client.attach('');
if (!client.view.workspace) await client.trustWorkspace(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-recovery-')));
client.setDraft('只回复：丢失回执恢复成功。不要调用工具。');
await client.submit(); assert.ok(client.draft.unknown); assert.equal(submitted, 1);
await assert.rejects(client.submit(), /待确认/); assert.equal(submitted, 1);
await client.tick(); assert.equal(client.draft.unknown, undefined);
const end = Date.now() + 90000;
while (client.active && Date.now() < end) { await new Promise(r => setTimeout(r, 300)); await client.tick(); }
assert.equal(client.run.status, 'completed');
const last = client.messages.filter(m => m.role === 'assistant').at(-1)!;
const previousEpoch = client.identity.contextEpoch;
await client.api(`/v1/sessions/${encodeURIComponent(client.view.sessionId)}/messages/${encodeURIComponent(last.id)}`, { method: 'PATCH', body: {
  content: '用户修订：恢复结果已核验。', expectedMessageVersion: last.version, expectedTranscriptRevision: client.identity.transcriptRevision, tailPolicy: 'reject',
} });
client.setDraft('修订期间保留的草稿'); await client.tick();
assert.ok(client.identity.contextEpoch > previousEpoch);
assert.equal(client.messages.at(-1).content, '用户修订：恢复结果已核验。');
assert.equal(client.draft.text, '修订期间保留的草稿');
const identity = { ...client.identity }; await client.attach(client.view.sessionId); assert.deepEqual(client.identity, identity);
const result = { realEngine: true, realProvider: true, fault: 'reply lost after actual acceptance', writes: submitted, noReplay: true, reconciled: true, revisionReloadParity: true, draftPreserved: true, sessionId: client.view.sessionId };
client.stop(); console.log(JSON.stringify(result));
