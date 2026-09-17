const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '../src/components/workbench/creative-canvas');
function load(name, overrides = {}) {
  const filename = path.join(root, name + '.ts');
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', 'require', source)(mod, mod.exports, (id) => overrides[id] || require(id));
  return mod.exports;
}
const sceneApi = load('proxy-scene');
const types = load('types');
const serialization = load('serialization', { './types': types, '../CreativeCanvasMedia': { creativeCanvasMediaType: (r) => r.mediaType } });
const graph = load('graph-operations', { './types': types });

test('entity identity and full reference purpose survive save, reorder and hydration', () => {
  const scene = sceneApi.createProxyScene();
  scene.entities[0].name = 'Robot'; scene.entities[1].name = 'Ceramic orb';
  const resource = { id: 'source-image', sessionId: 'session-a', name: 'reference.png', mediaType: 'image', mimeType: 'image/png', origin: 'source' };
  const refs = scene.entities.map((entity, i) => ({ resource, entityId: entity.entityId, bindingKey: 'binding-' + i, semanticRole: i ? 'material' : 'front', purpose: ('Long purpose ' + i + ' ').repeat(3000) + 'TAIL', resourceDigest: String(i).repeat(64) }));
  const original = { ...types.EMPTY_SNAPSHOT, graphId: 'graph-a', nodes: [{ nodeId: 'scene', kind: 'action', actionDefinitionId: sceneApi.SCENE_ACTION, origin: 'placeholder', x: 0, y: 0, width: 350, height: 300, parameters: { preserved: true }, configurationRevision: 4 }], edges: [] };
  let n = 0;
  const saved = sceneApi.saveProxyScene(original, 'scene', scene, refs, () => 'id-' + ++n);
  assert.equal(original.nodes[0].configurationRevision, 4, 'save is an immutable graph edit');
  assert.equal(saved.nodes.filter(n => n.kind === 'resource').length, 1, 'sharing one physical source does not duplicate it');
  const hydrated = serialization.normalizeSnapshot(JSON.parse(JSON.stringify(saved)));
  assert.equal(hydrated.nodes[0].configurationRevision, 5);
  assert.equal(hydrated.nodes[0].parameters.preserved, true);
  assert.equal(hydrated.edges.length, 2);
  const reordered = { ...scene, entities: [...scene.entities].reverse() };
  const again = sceneApi.saveProxyScene(hydrated, 'scene', reordered, [...refs].reverse(), () => 'id-' + ++n);
  for (const ref of refs) {
    const edge = again.edges.find(edge => edge.bindingKey === ref.bindingKey);
    assert.equal(edge.entityId, ref.entityId);
    assert.equal(edge.purpose, ref.purpose);
    assert.equal(edge.resourceDigest, ref.resourceDigest);
  }
  assert.equal(graph.isCanvasActionConfigured(hydrated.nodes[0], { parameterEditor: 'proxy_scene' }), true);
});

test('different subjects and style use the same timeline and camera contract', () => {
  const scene = sceneApi.createProxyScene();
  const entity = { ...scene.entities[0], name: 'Paper crane', shape: 'box', material: 'Folded rice paper', motion: [
    { time: 0, position: [-2, 0, 1], rotation: [0, 0, 0] },
    { time: 4, position: [2, 2, -1], rotation: [0, 180, 0] },
  ] };
  const sample = sceneApi.sampleEntity(entity, 2);
  assert.deepEqual(sample.position, [0, 1, 0]);
  assert.deepEqual(sample.rotation, [0, 90, 0]);
  assert.deepEqual(sceneApi.sampleEntity(entity, 10).position, [2, 2, -1]);
  assert.equal(graph.isCanvasActionConfigured({ parameters: { scene } }, { parameterEditor: 'proxy_scene' }), false, 'unnamed defaults do not count as configured');
});

test('aborted reference retrieval never produces a binding digest', async () => {
  const request = new AbortController();
  request.abort();
  await assert.rejects(sceneApi.sceneReferenceDigest({ url: 'data:image/png;base64,AQID', availability: 'available' }, request.signal), { name: 'AbortError' });
  await assert.rejects(sceneApi.sceneReferenceDigest({ availability: 'unavailable' }, new AbortController().signal), /reference_unavailable/);
});

test('base transform edits move a saved trajectory, key edits preserve other keys', () => {
  const entity = sceneApi.newSceneEntity('entity-a', 0);
  entity.motion = [{ time: 0, position: [...entity.position], rotation: [0, 0, 0] }, { time: 4, position: [2, .5, 1], rotation: [0, 90, 0] }];
  const moved = sceneApi.updateSceneEntity(entity, { position: [-1.5, 2, 0] });
  assert.equal(sceneApi.sampleEntity(moved, 0).position[1], 2);
  assert.equal(sceneApi.sampleEntity(moved, 4).position[1], 2);
  assert.equal(entity.motion[0].position[1], .5);
});

test('library aliases collapse by source identity, never by filename or other session', () => {
  const original = { id: 's1', origin: 'source', sessionId: 'a', mediaType: 'image', name: 'same.png' };
  const alias = { ...original, id: 'asset1', origin: 'workspace_asset', projectionRecord: { originKind: 'source', originId: 's1' } };
  const different = { ...original, id: 's2' };
  const otherSession = { ...original, sessionId: 'b', id: 'foreign' };
  assert.deepEqual(sceneApi.sceneImageChoices([alias, otherSession, original, different], 'a').map(r => r.id), ['s1', 's2']);
});

test('binding hashes original content rather than a transformed preview', async () => {
  const digest = await sceneApi.sceneReferenceDigest({ url: 'data:image/png;base64,BAUG', projectionRecord: { contentUrl: 'data:image/png;base64,AQID' } }, new AbortController().signal);
  assert.equal(digest, require('node:crypto').createHash('sha256').update(Buffer.from([1, 2, 3])).digest('hex'));
});
