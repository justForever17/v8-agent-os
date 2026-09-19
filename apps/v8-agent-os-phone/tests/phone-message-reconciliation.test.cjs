const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function loadDomain(baseline) {
    const cache = new Map();
    function load(name) {
        if (name === '@/src/lib/locale') return { translateCurrent: key => key };
        if (!name.startsWith('@/src/')) return require(name);
        if (cache.has(name)) return cache.get(name);
        const filename = path.join(root, name.slice(2) + '.ts');
        let source = fs.readFileSync(filename, 'utf8');
        if (baseline && name.endsWith('/phone-message-reconciliation')) {
            const screen = execFileSync('git', ['show', `${baseline}:apps/v8-agent-os-phone/src/screens/ChatScreen.tsx`], { cwd: root, encoding: 'utf8' });
            const begin = screen.indexOf('function buildAssistantPlaceholder');
            const end = screen.indexOf('function isLegacyChatUnsupportedPayload');
            assert.ok(begin >= 0 && end > begin);
            const header = source.slice(0, source.indexOf('function recordOf'));
            source = header + screen.slice(begin, end)
                + '\nfunction asRecord(value) { return value && typeof value === "object" ? value : {}; }'
                + '\nexport { mergeAuthoritativeSnapshotMessages, extractQueuedMessages, buildAssistantTaskProgressPatch, applyTodoToolEvent };';
        }
        const module = { exports: {} }; cache.set(name, module.exports);
        new Function('require', 'module', 'exports', ts.transpileModule(source, {
            fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText)(load, module, module.exports);
        return module.exports;
    }
    return load('@/src/lib/phone-message-reconciliation');
}

const current = loadDomain();
const message = (id, content, extra = {}) => ({ id, role: 'assistant', content, timestamp: 1, nodes: [], ...extra });
const plain = value => JSON.parse(JSON.stringify(value));

test('canonical snapshots replace revised text while preserving an active empty assistant shell', () => {
    const old = message('answer', 'old', { runId: 'run-A', uiStreamPhase: 'streaming', uiEphemeral: true });
    const revised = message('answer', 'new', { runId: 'run-A', version: 2, metadata: { transcriptVersion: 2 },
        nodes: [{ id: 'node', kind: 'narrative', role: 'assistant', content: 'new', timestamp: 1 }] });
    const [visible] = current.mergeAuthoritativeSnapshotMessages([old], [revised], true);
    assert.equal(visible.content, 'new'); assert.equal(visible.version, 2);
    const empty = { ...revised, content: '', nodes: [] };
    const [waiting] = current.mergeAuthoritativeSnapshotMessages([old], [empty], true);
    assert.equal(waiting.content, 'old'); assert.equal(waiting.uiStreamPhase, 'streaming');
});

test('snapshot reconciliation retains explicit composer inputs and honors populated authoritative fields', () => {
    const metadata = { clientMessageId: 'client', commandPreset: { name: 'review' },
        skillReferences: [{ name: 'skill' }], pluginReferences: [{ pluginId: 'plugin' }],
        contextMentions: [{ name: 'mention' }], contextSessionRefs: [{ sessionId: 'reference' }],
        explicitSubagentFamilies: [{ familyId: 'family' }], attachments: [{ id: 'source' }],
        composerPresentation: { text: 'visible draft' }, specMode: true, taskPlanningMode: true,
        taskPlanningSource: 'composer', taskPlanningRequestedByComposer: true };
    const local = message('user-local', 'hello', { role: 'user', metadata, images: ['image'], artifacts: [{ id: 'artifact' }] });
    const canonical = { ...local, id: 'user-canonical', images: [], artifacts: [], metadata: { clientMessageId: 'client', transcriptVersion: 1, skillReferences: [{ name: 'server-skill' }] } };
    const [visible] = current.mergeAuthoritativeSnapshotMessages([local], [canonical], true);
    assert.deepEqual(visible.metadata.skillReferences, [{ name: 'server-skill' }]);
    assert.deepEqual(visible.metadata.attachments, metadata.attachments);
    assert.deepEqual(visible.metadata.pluginReferences, metadata.pluginReferences);
    assert.deepEqual(visible.metadata.contextSessionRefs, metadata.contextSessionRefs);
    assert.equal(visible.metadata.specMode, true); assert.deepEqual(visible.artifacts, [{ id: 'artifact' }]);
});

test('empty queue snapshot clears the queue; omitted queue leaves reconciliation to its caller', () => {
    assert.deepEqual(current.extractQueuedMessages({ queuedMessages: [] }), []);
    assert.equal(current.extractQueuedMessages({}), null);
    assert.deepEqual(current.extractQueuedMessages({ snapshot: { queuedMessages: [{ id: '' }, { id: 'pending', state: 'pending' }] } }), [{ id: 'pending', state: 'pending' }]);
});

test('todo tool updates keep a single active item and do not mutate the previous projection', () => {
    const before = [{ id: 'a', content: 'first', status: 'in_progress' }, { id: 'b', content: 'second', status: 'pending' }];
    const after = current.applyTodoToolEvent(before, { tool: { toolName: 'update_todo', args: { index: 1, status: 'in_progress' } } });
    assert.deepEqual(after.map(item => item.status), ['pending', 'in_progress']);
    assert.equal(before[0].status, 'in_progress');
    assert.equal(current.buildAssistantTaskProgressPatch(after).currentStep, 'second');
});

const baselineRef = process.env.V8_PHONE_RECONCILIATION_BASELINE_REF;
test('reconciliation simplification matches the committed baseline across sparse and canonical snapshots', { skip: !baselineRef }, () => {
    const baseline = loadDomain(baselineRef);
    let cases = 0;
    for (const role of ['user', 'assistant']) for (const active of [false, true]) {
        for (const canonical of [false, true]) for (const sparse of [false, true]) for (const preserve of [false, true]) {
            const local = message('local', 'local text', { role, runId: 'run', uiEphemeral: active,
                uiStreamPhase: active ? 'tooling' : undefined, images: ['img'], artifacts: [{ id: 'artifact' }],
                metadata: { clientMessageId: 'client', assistantTaskProgress: { phase: 'tooling', completedCount: 0, totalCount: 2 },
                    attachments: [{ id: 'source' }], contextSessionRefs: [{ sessionId: 'context' }], specMode: true } });
            const snapshot = message('server', sparse ? '' : 'server text', { role, runId: 'run',
                metadata: { clientMessageId: 'client', ...(canonical ? { transcriptVersion: 3 } : {}) } });
            const expected = baseline.mergeAuthoritativeSnapshotMessages([structuredClone(local)], [structuredClone(snapshot)], preserve);
            const actual = current.mergeAuthoritativeSnapshotMessages([structuredClone(local)], [structuredClone(snapshot)], preserve);
            assert.deepEqual(plain(actual), plain(expected), JSON.stringify({ role, active, canonical, sparse, preserve }));
            cases++;
        }
    }
    assert.equal(cases, 32);
});
