import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore } from '../src/persistence.js';
import {
  MODEL_TABS,
  ROLE_TABS,
  ROLE_KEYS,
  filterModelsByTab,
  PRESET_PROVIDERS,
} from '../src/model-pages.js';

function createMockClient(routes: Record<string, any> = {}) {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-model-test-')));
  const client = new Client(store, async (route: string, opts: any) => {
    if (routes[route]) {
      return typeof routes[route] === 'function' ? routes[route](opts) : routes[route];
    }
    if (route.endsWith('/instance')) return { instanceId: 'test-inst' };
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: 'test-owner' } };
    if (route.includes('/sessions')) return { sessions: [{ id: 'sess-1', title: 'Session 1' }, { id: 'sess-2', title: 'Session 2' }] };
    return {};
  });
  client.instance = { instanceId: 'test-inst' };
  client.view.sessionId = 'sess-1';
  return client;
}

test('model and workspace commands are exposed as daily tier in commands()', () => {
  const client = createMockClient();
  const surface = new Surface(client);
  const commands = surface.commands();

  const modelCmd = commands.find(c => c.command === 'model');
  assert.ok(modelCmd, 'Expected /model command to be registered');
  assert.equal(modelCmd.tier, 'daily');
  assert.equal(modelCmd.navigation, true);

  const workspaceCmd = commands.find(c => c.command === 'workspace');
  assert.ok(workspaceCmd, 'Expected /workspace command to be registered');
  assert.equal(workspaceCmd.tier, 'daily');

  const mcpCmd = commands.find(c => c.command === 'mcp');
  assert.ok(mcpCmd, 'Expected /mcp command to be registered');
  assert.equal(mcpCmd.tier, 'daily');

  client.stop();
});

test('submitting /model in composer opens Model Hub with horizontal tabs', async () => {
  const sampleModels = [
    { modelRef: 'openai/gpt-4o', providerId: 'openai', providerName: 'OpenAI', modelId: 'gpt-4o', type: 'TEXT', capabilities: ['chat', 'tools', 'vision'] },
    { modelRef: 'deepseek/deepseek-chat', providerId: 'deepseek', providerName: 'DeepSeek', modelId: 'deepseek-chat', type: 'TEXT', capabilities: ['chat', 'tools'] },
    { modelRef: 'deepseek/deepseek-reasoner', providerId: 'deepseek', providerName: 'DeepSeek', modelId: 'deepseek-reasoner', type: 'REASONING', capabilities: ['reasoning'] },
  ];

  const client = createMockClient({
    '/v1/config-broker/roles': {
      roles: [
        { role: 'supervisor', label: '主编排', modelRef: 'deepseek/deepseek-chat', status: 'ready' },
      ],
    },
    '/v1/models/control-plane': {
      models: sampleModels,
    },
  });

  const surface = new Surface(client);
  client.setDraft('/model');
  await surface.submit();

  assert.ok(surface.page, 'Expected surface page to open');
  assert.equal(surface.page.title, '模型管理中心 / Model Hub');
  assert.deepEqual(surface.page.tabs, MODEL_TABS);
  assert.equal(surface.page.activeTab, 0);

  // Check that models are listed
  const labels = surface.page.actions.map(a => a.label);
  assert.ok(labels.some(l => l.includes('deepseek-chat') && l.includes('主编排')));
  assert.ok(labels.some(l => l.includes('gpt-4o')));

  client.stop();
});

test('filterModelsByTab correctly segregates model categories', () => {
  const models = [
    { modelId: 'm1', type: 'TEXT', capabilities: ['chat'] },
    { modelId: 'm2', type: 'VISION', capabilities: ['vision'] },
    { modelId: 'm3', type: 'REASONING', capabilities: ['reasoning'] },
    { modelId: 'm4', type: 'IMAGE', capabilities: [] },
    { modelId: 'm5', type: 'EMBEDDING', capabilities: [] },
  ];

  assert.equal(filterModelsByTab(models, 0).length, 5); // All
  assert.deepEqual(filterModelsByTab(models, 1).map(m => m.modelId), ['m1']); // Chat
  assert.deepEqual(filterModelsByTab(models, 2).map(m => m.modelId), ['m2']); // Vision
  assert.deepEqual(filterModelsByTab(models, 3).map(m => m.modelId), ['m3']); // Reasoning
  assert.deepEqual(filterModelsByTab(models, 4).map(m => m.modelId), ['m4']); // Media
  assert.deepEqual(filterModelsByTab(models, 5).map(m => m.modelId), ['m5']); // Embedding
});

test('left and right arrows cycle through tabs on tabbed pages', async () => {
  const client = createMockClient({
    '/v1/config-broker/roles': { roles: [] },
    '/v1/models/control-plane': { models: [] },
  });
  const surface = new Surface(client);
  await surface.modelsHub(0);

  assert.equal(surface.page?.activeTab, 0);

  // Press right arrow to switch to tab 1
  await surface.handle({ key: 'right' });
  assert.equal(surface.page?.activeTab, 1);

  // Press left arrow to switch back to tab 0
  await surface.handle({ key: 'left' });
  assert.equal(surface.page?.activeTab, 0);

  // Press left arrow again to wrap to last tab
  await surface.handle({ key: 'left' });
  assert.equal(surface.page?.activeTab, MODEL_TABS.length - 1);

  client.stop();
});

test('role assignment tabs view lists roles in tabs and allows one-click model binding', async () => {
  let preparedRole = '';
  let preparedModelRef = '';
  let committed = false;

  const client = createMockClient({
    '/v1/config-broker/roles': {
      roles: [
        { role: 'supervisor', label: '主编排', modelRef: 'openai/gpt-4o', status: 'ready' },
        { role: 'subagent', label: '子智能体', modelRef: '', status: 'unbound' },
      ],
    },
    '/v1/models/control-plane': {
      models: [
        { modelRef: 'openai/gpt-4o', providerId: 'openai', modelId: 'gpt-4o', type: 'TEXT' },
        { modelRef: 'deepseek/deepseek-chat', providerId: 'deepseek', modelId: 'deepseek-chat', type: 'TEXT' },
      ],
    },
    '/v1/config-broker/roles/prepare': (opts: any) => {
      const body = opts?.body || {};
      preparedRole = body.role;
      preparedModelRef = body.modelRef;
      return { transactionId: 'tx-123', planDigest: 'digest-abc' };
    },
    '/v1/config-broker/transactions/tx-123/commit': () => {
      committed = true;
      return { ok: true };
    },
  });

  const surface = new Surface(client);
  await surface.roleModelAssignmentTabs(0);

  assert.ok(surface.page, 'Expected roleModelAssignmentTabs to open');
  assert.deepEqual(surface.page.tabs, ROLE_TABS);
  assert.equal(surface.page.activeTab, 0);

  // Check model actions
  const deepseekAction = surface.page.actions.find(a => a.label.includes('deepseek-chat'));
  assert.ok(deepseekAction, 'Expected deepseek-chat option in model list');

  // Trigger binding of deepseek-chat to supervisor
  await deepseekAction.run();

  assert.equal(preparedRole, 'supervisor');
  assert.equal(preparedModelRef, 'deepseek/deepseek-chat');
  assert.equal(committed, true);
  assert.match(client.notice, /已成功将.*deepseek-chat.*绑定/);

  client.stop();
});

test('preset providers contain major ecosystem vendors with verified default configurations', () => {
  const ids = PRESET_PROVIDERS.map(p => p.id);
  assert.ok(ids.includes('openai'));
  assert.ok(ids.includes('deepseek'));
  assert.ok(ids.includes('dashscope'));
  assert.ok(ids.includes('anthropic'));
  assert.ok(ids.includes('ollama'));
  assert.ok(ids.includes('custom'));

  const ollama = PRESET_PROVIDERS.find(p => p.id === 'ollama');
  assert.equal(ollama?.baseUrl, 'http://127.0.0.1:11434/v1');

  const deepseek = PRESET_PROVIDERS.find(p => p.id === 'deepseek');
  assert.equal(deepseek?.baseUrl, 'https://api.deepseek.com/v1');
  assert.ok(deepseek?.presetModels.includes('deepseek-chat'));
});

test('sessions list focuses on first non-current session for rapid arrow-key resumption', async () => {
  const client = createMockClient();
  client.view.sessionId = 'sess-1';
  client.sessions = [
    { id: 'sess-1', title: 'Active Current Session', status: 'running' },
    { id: 'sess-2', title: 'Previous Session to Resume', status: 'completed' },
  ];

  const surface = new Surface(client);
  await surface.sessions();

  assert.ok(surface.page);
  assert.equal(surface.page.title, '会话');
  // First item is sess-1 (current), second is sess-2 (previous)
  assert.match(surface.page.actions[0].label, /● \[当前\] Session 1/);
  assert.match(surface.page.actions[1].label, /○ Session 2/);

  // selected index should be 1 (the first previous session!)
  assert.equal(surface.page.selected, 1);

  let attachedId = '';
  client.attach = async (id: string) => { attachedId = id; };

  // Press Enter on the selected item
  await surface.page.actions[surface.page.selected].run();
  assert.equal(attachedId, 'sess-2');

  client.stop();
});
