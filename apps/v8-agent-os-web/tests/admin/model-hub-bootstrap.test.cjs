const assert = require('node:assert/strict');
const test = require('node:test');
const { createHarness, flush, payload } = require('./model-hub-harness.cjs');

function mount(cached) {
  const harness = createHarness(cached);
  const { useModelHubBootstrap } = harness.load('@admin/hooks/use-model-hub-bootstrap');
  return { ...harness, ...harness.mount(useModelHubBootstrap) };
}

test('cached data paints immediately; ordinary renders do not refetch', () => {
  const ui = mount(payload('cached'));
  const state = ui.render();
  assert.equal(state.snapshot.providers[0].id, 'cached');
  assert.equal(state.isLoading, false);
  assert.equal(state.audio.loaded, true);
  assert.equal(state.audio.value.tts.edge_tts.voice, 'cached');
  ui.render(); ui.render();
  assert.equal(ui.bootstrapRequests.length, 1);
  assert.deepEqual(ui.bootstrapRequests[0].options, { force: false, ttlMs: 30_000 });
});

test('cached bootstrap without audio does not expose defaults as loaded config', () => {
  const cached = payload('cached'); delete cached.audioConfig;
  assert.equal(mount(cached).render().audio.loaded, false);
});

for (const staleOutcome of ['success', 'failure']) {
  test('newest refresh wins over an older ' + staleOutcome, async () => {
    const ui = mount();
    const pending = ui.render().refresh(true);
    ui.bootstrapRequests[1].resolve(payload('new'));
    assert.equal(await pending, true);
    if (staleOutcome === 'success') ui.bootstrapRequests[0].resolve(payload('old'));
    else ui.bootstrapRequests[0].reject(new Error('stale failure'));
    await flush();
    const state = ui.render();
    assert.equal(state.snapshot.providers[0].id, 'new');
    assert.equal(state.snapshot.defaultModelRef, 'new::default');
    assert.equal(state.bootstrapError, null);
    assert.equal(state.isLoading, false);
  });
}

test('failed initial load is visible and a refresh recovers', async () => {
  const ui = mount(); ui.render();
  ui.bootstrapRequests[0].reject(new Error('unavailable')); await flush();
  let state = ui.render();
  assert.equal(state.bootstrapError, 'unavailable');
  assert.equal(state.audio.loaded, false);
  assert.equal(state.isLoading, false);
  const retry = state.refresh(true);
  ui.bootstrapRequests[1].resolve(payload('recovered'));
  assert.equal(await retry, true);
  state = ui.render();
  assert.equal(state.bootstrapError, null);
  assert.equal(state.snapshot.models[0].id, 'recovered');
});

for (const timing of ['before', 'during']) {
  test('audio edits made ' + timing + ' refresh survive its response', async () => {
    const ui = mount(payload('original'));
    let state = ui.render();
    const edit = () => state.audio.update(current => ({ ...current, tts: { ...current.tts, edge_tts: { voice: 'draft' } } }));
    if (timing === 'before') { edit(); state = ui.render(); }
    const pending = state.refresh(true);
    if (timing === 'during') edit();
    ui.bootstrapRequests[1].resolve(payload('server')); await pending;
    state = ui.render();
    assert.equal(state.audio.value.tts.edge_tts.voice, 'draft');
    assert.equal(state.snapshot.audioConfig.tts.edge_tts.voice, 'server');
  });
}

test('save clears only submitted audio draft and invalidates older reads', async () => {
  const ui = mount(payload('initial'));
  let state = ui.render();
  state.audio.update(current => ({ ...current, tts: { ...current.tts, edge_tts: { voice: 'submitted' } } }));
  state = ui.render();
  const submitted = state.audio.value;
  const { mergeAudioConfig } = ui.load('@admin/lib/model-hub/audio');
  const saved = mergeAudioConfig(payload('saved').audioConfig);
  state.audio.acceptSaved(submitted, saved);
  ui.bootstrapRequests[0].resolve(payload('old')); await flush();
  state = ui.render();
  assert.equal(state.audio.value.tts.edge_tts.voice, 'saved');
  state.audio.update(current => ({ ...current, tts: { ...current.tts, edge_tts: { voice: 'newer edit' } } }));
  state = ui.render();
  state.audio.acceptSaved(submitted, saved);
  assert.equal(ui.render().audio.value.tts.edge_tts.voice, 'newer edit');
});

test('refresh can preserve identity order while replacing model facts', async () => {
  const cached = payload('p');
  cached.models = ['b', 'a'].map(id => ({ id, providerId: 'p', modelId: id }));
  const ui = mount(cached);
  const pending = ui.render().refresh(true, true);
  ui.bootstrapRequests[1].resolve({ ...cached, models: ['a', 'c', 'b'].map(id => ({ id, providerId: 'p', modelId: id, maxTokens: 42 })) });
  await pending;
  assert.deepEqual(ui.render().snapshot.models.map(model => [model.id, model.maxTokens]), [['b', 42], ['a', 42], ['c', 42]]);
});

test('local changes use provider identity and survive an older read', async () => {
  const cached = payload('p');
  cached.models = [{ id: 'same', providerId: 'a' }, { id: 'same', providerId: 'b' }];
  const ui = mount(cached), state = ui.render();
  state.removeModel({ id: 'same', providerId: 'a' });
  state.rememberCatalogProvider({ id: 'custom', name: 'Custom' });
  state.setDefaultModel('b::same');
  ui.bootstrapRequests[0].resolve(cached); await flush();
  const next = ui.render().snapshot;
  assert.deepEqual(next.models, [{ id: 'same', providerId: 'b' }]);
  assert.equal(next.catalogProviders[0].id, 'custom');
  assert.equal(next.defaultModelRef, 'b::same');
});

test('real cache keeps saved audio across remount and rejects a pre-save in-flight read', async () => {
  const harness = createHarness(payload('cached'), { realCache: true });
  const useBootstrap = harness.load('@admin/hooks/use-model-hub-bootstrap').useModelHubBootstrap;
  const first = harness.mount(useBootstrap);
  let state = first.render();
  await flush();
  state = first.render();
  const oldRead = state.refresh(true);
  assert.equal(harness.requests.length, 1);
  const saved = { ...state.audio.value, tts: { ...state.audio.value.tts, edge_tts: { voice: 'saved voice' } } };
  state.audio.update(saved); state = first.render();
  state.audio.acceptSaved(saved, saved);
  first.unmount();
  const second = harness.mount(useBootstrap);
  state = second.render();
  assert.equal(state.audio.value.tts.edge_tts.voice, 'saved voice');
  assert.equal(harness.requests.length, 2, 'remount must revalidate, not reuse the pre-save promise');
  harness.requests[0].resolve(Response.json(payload('stale voice')));
  assert.equal(await oldRead, false);
  const cache = harness.load('@admin/lib/admin-client-cache');
  assert.equal(cache.peekAdminJsonCache('/api/admin/model-hub/bootstrap').audioConfig.tts.edge_tts.voice, 'saved voice');
  harness.requests[1].resolve(Response.json(payload('saved voice')));
  await flush();
  assert.equal(second.render().audio.value.tts.edge_tts.voice, 'saved voice');
  second.unmount();
});

for (const outcome of ['success', 'failure']) {
  test('unmount ignores late ' + outcome + ' without state writes', async () => {
    const harness = createHarness();
    const ui = harness.mount(harness.load('@admin/hooks/use-model-hub-bootstrap').useModelHubBootstrap);
    const state = ui.render(); ui.unmount();
    if (outcome === 'success') harness.bootstrapRequests[0].resolve(payload('late'));
    else harness.bootstrapRequests[0].reject(new Error('late'));
    await flush();
    assert.equal(ui.lateWrites, 0);
    assert.equal(await state.refresh(true), false);
    assert.equal(harness.bootstrapRequests.length, 1);
  });
}
