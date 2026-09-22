const assert = require('node:assert/strict');
const test = require('node:test');
const { createHarness, named, nodes, byId, selectById, deferred, payload } = require('./model-hub-harness.cjs');

function fixture() {
  const value = payload('A');
  const channel = (id, protocol) => ({ id, label: id, apiStandard: protocol.startsWith('anthropic') ? 'anthropic' : 'openai', baseUrl: 'https://fixture.invalid/' + id, apiVersion: '', wireProtocols: [protocol], defaultWireProtocol: protocol });
  value.providers = [
    { ...value.providers[0], name: '中文 · 通道 🎧', channels: [channel('chat', 'openai.chat_completions'), channel('responses', 'openai.responses')], defaultChannelId: 'responses' },
    { ...value.providers[0], id: 'B', code: 'B', name: 'B', channels: [channel('messages', 'anthropic.messages')], defaultChannelId: 'messages' },
  ];
  value.models = [{
    id: 'shared/id', providerId: 'A', modelId: '中文/alpha', type: 'IMAGE', isEnabled: true,
    mediaLimits: { capabilityModes: ['image.text_to_image'], untouched: { future: 42 } },
    endpointBinding: { channelId: 'chat', wireProtocol: 'openai.chat_completions', endpointPath: 'images/generations', providerModelId: 'upstream', untouched: { future: true } },
  }];
  return value;
}
function editor(kind, props = {}, harness = createHarness(), value = fixture()) {
  const component = harness.load('@admin/components/model-hub/' + kind + 'EditorDialog')[kind + 'EditorDialog'];
  const events = { saved: 0, closed: 0 };
  const target = kind === 'Model' ? value.models[0] : value.providers[0];
  const host = harness.mount(() => component({
    target, providers: value.providers, controlMeta: null,
    onSaved() { events.saved++; }, onClose() { events.closed++; }, ...props,
  }));
  return { ...harness, host, events, value, render: host.render, unmount: host.unmount };
}
const form = tree => named(tree, 'form')[0];
const event = fields => ({ preventDefault() {}, currentTarget: fields });
const modelFields = { providerId: 'B', modelId: 'changed', type: 'IMAGE', contextWindow: '', maxTokens: '', adapter: 'openai_images', providerModelId: 'upstream', endpointPath: 'images/generations' };

test('page passes only model target/context and lifecycle callbacks; save closes and refreshes', async () => {
  const value = fixture(), ui = createHarness(value);
  const page = ui.mount(ui.load('@admin/../app/admin/(dashboard)/model-hub/page').default);
  named(page.render(), 'ModelCardV2')[0].props.onEdit();
  const dialog = named(page.render(), 'ModelEditorDialog')[0];
  assert.deepEqual(Object.keys(dialog.props).sort(), ['controlMeta', 'onClose', 'onSaved', 'providers', 'target']);
  assert.equal(dialog.props.target, value.models[0]);
  const saving = dialog.props.onSaved();
  assert.equal(named(page.render(), 'ModelEditorDialog').length, 0);
  assert.equal(ui.bootstrapRequests.at(-1).options.force, true);
  ui.bootstrapRequests.at(-1).resolve(value); await saving;
  page.unmount();
});

test('model editor switches provider, channel and protocol together and preserves Unicode', () => {
  const ui = editor('Model');
  let tree = ui.render();
  assert.equal(byId(tree, 'model-model-id').props.defaultValue, '中文/alpha');
  assert.equal(byId(tree, 'model-channel').props.value, 'chat');
  selectById(tree, 'model-provider').props.onValueChange('B');
  tree = ui.render();
  assert.equal(byId(tree, 'model-channel').props.value, 'messages');
  selectById(tree, 'model-type').props.onValueChange('TEXT');
  tree = ui.render();
  assert.equal(nodes(tree, node => node.props.name === 'wireProtocol')[0].props.value, 'anthropic.messages');
  selectById(tree, 'model-type').props.onValueChange('RERANK');
  tree = ui.render();
  selectById(tree, 'model-rerank-flavor').props.onValueChange('cohere');
  assert.equal(selectById(ui.render(), 'model-rerank-flavor').props.value, 'cohere');
  ui.unmount();
});

test('provider presets update the local draft without mutating bootstrap channels', () => {
  const ui = editor('Provider');
  let tree = ui.render();
  assert.equal(byId(tree, 'provider-name').props.defaultValue, '中文 · 通道 🎧');
  const channelId = nodes(tree, node => node.props['aria-label'] === 'app.admin.dashboard.model.hub.channel.id')[0];
  channelId.props.onChange({ target: { value: 'new-channel' } });
  assert.equal(ui.value.providers[0].channels[0].id, 'chat');
  selectById(tree, 'provider-type').props.onValueChange('PLATFORM');
  tree = ui.render();
  assert.equal(nodes(tree, node => node.props.name === 'credentialMode')[0].props.value, 'oauthFile');
  selectById(tree, 'provider-type').props.onValueChange('LOCAL');
  tree = ui.render();
  assert.equal(selectById(tree, 'provider-credential-mode').props.value, 'apiKey');
  assert.match(byId(tree, 'provider-base-url').props.value, /11434/);
  ui.unmount();
});

test('model save preserves unknown fields/target identity, blocks duplicate submit and close, and retries failure', async () => {
  const ui = editor('Model');
  const handler = form(ui.render()).props.onSubmit;
  const save = handler(event(modelFields));
  await handler(event(modelFields));
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, '/api/admin/models/shared%2Fid?providerId=A');
  assert.equal(ui.requests[0].options.method, 'PUT');
  const body = JSON.parse(ui.requests[0].options.body);
  assert.deepEqual(body.mediaLimits, ui.value.models[0].mediaLimits);
  assert.deepEqual(body.endpointBinding, ui.value.models[0].endpointBinding);
  assert.deepEqual(body.capabilityModes, ['image.text_to_image']);
  assert.equal(body.maxTokens, null);
  assert.equal(body.channelId, 'chat');
  let tree = ui.render();
  assert.equal(named(tree, 'fieldset')[0].props.disabled, true);
  named(tree, 'Dialog')[0].props.onOpenChange(false);
  assert.equal(ui.events.closed, 0);
  ui.requests[0].reject(new Error('network failed')); await save;
  tree = ui.render();
  assert.equal(named(tree, 'fieldset')[0].props.disabled, false);
  assert.match(nodes(tree, node => node.props.role === 'alert')[0].props.children, /network failed/);
  assert.equal(selectById(tree, 'model-type').props.value, 'IMAGE');
  const retry = form(tree).props.onSubmit(event(modelFields));
  ui.requests[1].resolve(Response.json({ ok: true })); await retry;
  assert.equal(ui.events.saved, 1);
  ui.unmount();
});

test('provider rejection keeps draft available; retry persists selected channels', async () => {
  const ui = editor('Provider');
  const fields = { name: '中文', code: 'A', type: 'API', credentialMode: 'apiKey', apiKey: '' };
  const save = form(ui.render()).props.onSubmit(event(fields));
  const body = JSON.parse(ui.requests[0].options.body);
  assert.equal(body.defaultChannelId, 'responses');
  assert.equal(body.channels.length, 2);
  assert.equal(body.baseUrl, 'https://fixture.invalid/responses');
  ui.requests[0].resolve(Response.json({ error: 'provider refused' }, { status: 400 })); await save;
  assert.equal(ui.events.saved, 0);
  assert.equal(named(ui.render(), 'fieldset')[0].props.disabled, false);
  assert.equal(ui.toasts.at(-1).description, 'provider refused');
  const retry = form(ui.render()).props.onSubmit(event(fields));
  ui.requests[1].resolve(Response.json({ ok: true })); await retry;
  assert.equal(ui.events.saved, 1);
  ui.unmount();
});

test('persisted empty capability modes override control-plane inference', () => {
  const { createModelDraft } = createHarness().load('@admin/lib/model-hub/editor-drafts');
  const value = fixture(), model = { ...value.models[0], type: 'VIDEO', mediaLimits: { capabilityModes: [] } };
  const control = { mediaLimits: { capabilityModes: ['video.text_to_video'], operationKinds: ['video.text_to_video'] } };
  assert.deepEqual(createModelDraft(model, control, value.providers).mediaCapabilityModes, []);
  delete model.mediaLimits.capabilityModes;
  assert.deepEqual(createModelDraft(model, control, value.providers).mediaCapabilityModes, ['video.text_to_video']);
});

for (const outcome of ['success', 'failure']) {
  test('late workflow import ' + outcome + ' after closing A cannot alter B or show a stale error', async () => {
    const harness = createHarness(), value = fixture();
    const modelA = { ...value.models[0], type: 'WORKFLOW' };
    const first = editor('Model', { target: modelA }, harness, value);
    const read = deferred();
    const importing = byId(first.render(), 'comfy-workflow-file').props.onChange({ target: { files: [{ text: () => read.promise }] } });
    first.unmount();
    const second = editor('Model', { target: { ...modelA, id: 'B', providerId: 'B' } }, harness, value);
    named(second.render(), 'Textarea')[0].props.onChange({ target: { value: '{"newer":"B 草稿"}' } });
    if (outcome === 'success') read.resolve('{"old":"A 上传"}'); else read.reject(new Error('old read failed'));
    await importing;
    assert.equal(named(second.render(), 'Textarea')[0].props.value, '{"newer":"B 草稿"}');
    assert.equal(first.host.lateWrites, 0);
    assert.equal(harness.toasts.length, 0);
    second.unmount();
  });
}

test('workflow import uses latest selected file and preserves manual edits made while reading', async () => {
  const value = fixture(), ui = editor('Model', { target: { ...value.models[0], type: 'WORKFLOW' } });
  const first = deferred(), second = deferred();
  const upload = read => byId(ui.render(), 'comfy-workflow-file').props.onChange({ target: { files: [{ text: () => read.promise }] } });
  const a = upload(first), b = upload(second);
  second.resolve('{"new":"second file"}'); await b;
  first.resolve('{"old":"first file"}'); await a;
  assert.deepEqual(JSON.parse(named(ui.render(), 'Textarea')[0].props.value), { new: 'second file' });
  const third = deferred(), c = upload(third);
  named(ui.render(), 'Textarea')[0].props.onChange({ target: { value: '{"manual":"latest"}' } });
  third.resolve('{"file":"late"}'); await c;
  assert.equal(named(ui.render(), 'Textarea')[0].props.value, '{"manual":"latest"}');
  ui.unmount();
});

for (const kind of ['Model', 'Provider']) {
  test(kind + ' save aborts transport on unmount and ignores a late response', async () => {
    const ui = editor(kind);
    const save = form(ui.render()).props.onSubmit(event(modelFields));
    ui.unmount();
    assert.equal(ui.requests[0].options.signal.aborted, true);
    ui.requests[0].resolve(Response.json({ ok: true })); await save;
    assert.equal(ui.events.saved, 0);
    assert.equal(ui.host.lateWrites, 0);
  });
}

test('custom catalog entry wins projection while internal capability duplicates remain hidden', () => {
  const { buildCatalogProvidersForPurpose } = createHarness().load('@admin/lib/model-hub/catalog');
  const capability = { id: 'image-internal', sourceProviderId: 'image-internal', name: 'Internal', catalogVisibility: 'internal_capability', type: 'image', mediaModality: 'image', providerKind: 'media_generation', models: [{ id: 'x' }] };
  const root = { id: 'root', name: 'Root', capabilityEntries: [capability] };
  const custom = { ...root, isCustom: true, declaredCapabilities: ['image'], models: [{ id: 'custom-model' }] };
  assert.deepEqual(buildCatalogProvidersForPurpose([custom, capability], 'image'), [custom]);
  const projected = buildCatalogProvidersForPurpose([root, capability], 'image');
  assert.equal(projected.length, 1);
  assert.equal(projected[0].models[0].mediaLimits.adapterProviderId, 'image-internal');
});
