const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/config-distribution.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const loaded = { exports: {} };
vm.runInThisContext(`(function(module,exports){${compiled}\n})`)(loaded, loaded.exports);
const api = loaded.exports;
const peer = { peerId: 'target-B', linkId: 'link-B' };

test('target capability identity and protocol must match the trusted peer', async () => {
    const capabilities = { peerId: peer.peerId, protocolVersion: 1, pathPolicy: 'target_local_only', roles: [], models: [] };
    assert.deepEqual(await api.loadDistributionTarget(async () => Response.json(capabilities), peer), capabilities);
    for (const mutation of [{ peerId: 'other-target' }, { protocolVersion: 0 }, { pathPolicy: 'copy_source_paths' }]) {
        await assert.rejects(api.loadDistributionTarget(async () => Response.json({ ...capabilities, ...mutation }), peer));
    }
    await assert.rejects(api.loadDistribution(async () => Response.json({ servingInstanceId: 'other-primary' }), 'primary-A'));
});

test('model mapping requires each explicit target model, ready local credentials, and distinct valid roles', () => {
    const template = { id: 'model-roles', roles: [{ id: 'supervisor' }, { id: 'subagent' }] };
    const capabilities = { roles: [{ id: 'supervisor' }, { id: 'subagent' }], models: [
        { modelRef: 'target/ready', ready: true, missingRequirements: [] },
        { modelRef: 'target/missing-key', ready: true, missingRequirements: ['credential'] },
        { modelRef: 'target/unready', ready: false, missingRequirements: [] },
    ] };
    const valid = { roles: { supervisor: 'supervisor', subagent: 'subagent' }, models: { supervisor: 'target/ready', subagent: 'target/ready' } };
    assert.equal(api.distributionMappingReady(template, valid, capabilities), true);
    for (const broken of [
        { ...valid, models: { supervisor: 'target/ready' } },
        { ...valid, models: { ...valid.models, subagent: 'source/provider/model' } },
        { ...valid, models: { ...valid.models, subagent: 'target/missing-key' } },
        { ...valid, models: { ...valid.models, subagent: 'target/unready' } },
        { ...valid, roles: { supervisor: 'supervisor', subagent: 'supervisor' } },
        { ...valid, roles: { supervisor: 'supervisor', subagent: 'unknown' } },
    ]) assert.equal(api.distributionMappingReady(template, broken, capabilities), false);
    assert.equal(api.distributionMappingReady(template, valid), false);
    assert.equal(api.distributionMappingReady({ id: 'model-policy' }, { roles: {}, models: {} }), true);
});

test('confirm, retry and withdraw carry the displayed exact revision, digest and caller command; no transport write retry', async () => {
    const job = { jobId: 'job/1', revision: 4, planDigest: 'displayed-digest' };
    const requests = [];
    for (const action of ['confirm', 'retry', 'withdraw']) {
        await api.actOnDistribution(async (url, init) => { requests.push({ url, body: JSON.parse(init.body) }); return Response.json(job); }, job, action, 'same-command');
    }
    assert.deepEqual(requests, ['confirm', 'retry', 'withdraw'].map((action) => ({ url: `/api/client/config-distribution/job%2F1/${action}`,
        body: { commandId: 'same-command', revision: 4, planDigest: 'displayed-digest' } })));
    let writes = 0;
    await assert.rejects(api.actOnDistribution(async () => { writes++; throw new Error('lost response'); }, job, 'confirm', 'same-command'), /lost response/);
    assert.equal(writes, 1);
    await assert.rejects(api.loadDistributionJob(async () => Response.json({ jobId: 'other-job' }), job.jobId));
});

test('aborted reads cannot return even if the transport delivers a late response', async () => {
    const controller = new AbortController();
    await assert.rejects(api.loadDistribution(async () => { controller.abort(); return Response.json({ servingInstanceId: 'A' }); }, 'A', controller.signal), { name: 'AbortError' });
});
