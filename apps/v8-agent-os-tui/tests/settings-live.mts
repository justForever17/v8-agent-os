import assert from 'node:assert/strict';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
if (!process.argv.includes('--live') || !process.env.V8_AGENT_OS_HOME) throw new Error('--live and isolated state required');
const client = new Client(); await client.initialize(); assert.equal(client.connection, '已连接');
const surface = new Surface(client);
const before = await client.api('/v1/models/control-plane');
const oldBudgets = before.config.governance.budgets;
await surface.budgets(); assert.equal(surface.page?.title, '累计预算');
await surface.page!.onSave!({ globalDailyTokenLimit: '1000000', globalDailyCostLimit: '0', runMaxTokens: '54321', runMaxCost: '0' });
assert.equal(surface.page?.title, '配置变更预览');
assert.equal(surface.page?.selected, 0);
await surface.page!.actions[1].run();
assert.equal((await client.api('/v1/models/control-plane')).config.governance.budgets.runMaxTokens, 54321);
await surface.page!.actions[1].run(); await surface.page!.actions[1].run();
assert.deepEqual((await client.api('/v1/models/control-plane')).config.governance.budgets, oldBudgets);
const contextBefore = await client.api('/v1/config-registry/context');
await surface.contextSettings(); await surface.page!.onSave!({ window: '24000', ratio: '0.9', turns: '3' });
await surface.page!.actions[1].run();
assert.equal((await client.api('/v1/config-registry/context')).data.policy.compression.default_context_window_tokens, 24000);
await client.api('/v1/config-registry/context', { method: 'POST', body: contextBefore.data });
await surface.outputParameters();
const model = before.models.find((m: any) => ['TEXT', 'MULTIMODAL', 'VISION', 'CHAT'].includes(String(m.type || 'TEXT').toUpperCase()));
if (model) {
  await surface.page!.actions[1].run(); await surface.page!.onSave!({ mode: 'fixed', maxTokens: '2048' }); await surface.page!.actions[1].run();
  const saved = (await client.api('/v1/models/control-plane')).models.find((m: any) => m.modelRef === model.modelRef);
  assert.equal(saved.maxTokens, 2048); assert.equal(saved.outputTokenMode, 'fixed');
  await client.api('/v1/models/bindings', { method: 'PUT', body: { providerId: model.providerId, modelId: model.modelId,
    model: { outputTokenMode: model.outputTokenMode || (model.maxTokens ? 'fixed' : 'auto'), ...(model.maxTokens ? { maxTokens: model.maxTokens } : {}) } } });
}
const gateway = await client.api('/v1/config-broker/client-gateway');
const plan = await client.api('/v1/config-broker/client-gateway/prepare', { method: 'POST', body: { settings: { publicBaseUrl: 'https://tui-fixture.example.invalid' } } });
await client.api(`/v1/config-broker/transactions/${plan.transactionId}/commit`, { method: 'POST', body: { planDigest: plan.planDigest } });
await surface.phones(); const pairAction = surface.page!.actions.find(a => a.label === '添加手机')!; assert.equal(pairAction.disabled, false);
await pairAction.run(); await surface.page!.onSave!({ deviceName: 'TUI fixture' }); assert.equal(surface.page?.title, '手机配对');
assert.ok(surface.page!.lines.some(line => line.includes('tui-fixture.example.invalid')));
await surface.close(true);
await client.api(`/v1/config-broker/transactions/${plan.transactionId}/rollback`, { method: 'POST' });
assert.equal((await client.api('/v1/config-broker/client-gateway')).settings.publicBaseUrl, gateway.settings.publicBaseUrl);
const pages: string[] = [];
for (const [name, open] of Object.entries({ models: () => surface.models(), inbox: () => surface.inbox(), peers: () => surface.peers(), memory: () => surface.registry('memory'), schedule: () => surface.registry('cron'), gateway: () => surface.brokerSettings('client-gateway'), network: () => surface.brokerSettings('network') })) {
  await open(); assert.ok(surface.page); pages.push(name);
}
client.stop(); console.log(JSON.stringify({ realEngine: true, budgetPrepareCommitReadbackRollback: true, contextRoundtrip: true, modelOutputRoundtrip: Boolean(model), selectedPhoneAddressAndTicketClose: true, pages }));
