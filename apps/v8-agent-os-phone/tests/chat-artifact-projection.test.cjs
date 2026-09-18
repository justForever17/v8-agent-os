const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function loadProjection() {
    const cache = new Map();
    function load(name) {
        if (name === '@/src/lib/locale') return { translateCurrent: () => 'Supervisor' };
        if (!name.startsWith('@/src/')) return require(name);
        if (cache.has(name)) return cache.get(name);
        const relative = name.slice(2) + '.ts';
        const baseline = process.env.V8_PHONE_ARTIFACT_BASELINE_REF;
        const source = baseline
            ? require('node:child_process').execFileSync('git', ['show', `${baseline}:apps/v8-agent-os-phone/${relative}`], { cwd: root, encoding: 'utf8' })
            : fs.readFileSync(path.join(root, relative), 'utf8');
        const module = { exports: {} };
        cache.set(name, module.exports);
        new Function('require', 'module', 'exports', ts.transpileModule(source, {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText)(load, module, module.exports);
        return module.exports;
    }
    return { ...load('@/src/lib/chat-realtime'), ...load('@/src/lib/chat-stream-state') };
}

function rawArtifactEvent(artifact) {
    return { type: 'custom_event', name: 'artifact_recorded', topic: 'artifact.recorded', visibility: 'visible', targets: ['message'],
        run_id: 'run-A', session_id: 'session-A', event_id: 'artifact-event', seq: 5, data: { artifact } };
}

function project(projection, event, messages = [], current) {
    const result = projection.applyRealtimeEventToMessages(event, messages, current, {});
    return { messages, current: result.currentAiMsg };
}

test('Phone keeps blocked-preview evidence and scoped resource references through event and message projection', () => {
    const projection = loadProjection();
    for (const snakeCase of [false, true]) {
        const field = (camel, snake, value) => ({ [snakeCase ? snake : camel]: value });
        const artifact = {
            id: 'artifact-A', title: 'Report', kind: 'document',
            ...field('previewBlockedReason', 'preview_blocked_reason', 'Preview is unavailable for this format'),
            ...field('workspaceId', 'workspace_id', 'workspace-A'),
            ...field('projectId', 'project_id', 'project-A'),
            ...field('workspaceRoot', 'workspace_root', '/synthetic/workspace'),
            ...field('workspaceRelativePath', 'workspace_relative_path', 'reports/report.bin'),
            ...field('storageClass', 'storage_class', 'workspace_artifact'),
            ...field('surfaceVisible', 'surface_visible', false),
            ...field('resourceRef', 'resource_ref', { kind: 'artifact_content', artifactId: 'artifact-A', sessionId: 'session-A' }),
        };
        const event = projection.normalizePhoneRealtimeEvent(rawArtifactEvent(artifact));
        assert.ok(event);
        assert.equal(event.artifact.previewBlockedReason, 'Preview is unavailable for this format');
        const { messages, current } = project(projection, event);
        assert.equal(messages.length, 1);
        assert.deepEqual(current.artifacts, [event.artifact], 'the lifecycle adapter must not discard normalized fields');
        assert.deepEqual(current.nodes.filter(node => node.kind === 'artifact').map(node => node.artifact), [event.artifact]);
        assert.equal(current.artifacts[0].workspaceId, 'workspace-A');
        assert.equal(current.artifacts[0].surfaceVisible, false);
        assert.equal(current.artifacts[0].resourceRef.sessionId, 'session-A');
        assert.equal(current.artifacts[0].resourceRef.adminPath, '/api/client/artifacts/artifact-A/content?sessionId=session-A');
        project(projection, event, messages, current);
        assert.equal(current.artifacts.length, 1, 'replayed event must not duplicate the artifact');
        assert.equal(current.nodes.filter(node => node.kind === 'artifact').length, 1);
    }
});

test('Phone raw stream and normalized event use the same artifact contract', () => {
    const projection = loadProjection();
    const raw = rawArtifactEvent({ id: 'artifact-A', title: 'Report', preview_blocked_reason: 'Unavailable', workspace_id: 'workspace-A' });
    const direct = project(projection, raw).current;
    const normalized = project(projection, projection.normalizePhoneRealtimeEvent(raw)).current;
    assert.equal(direct.artifacts[0].previewBlockedReason, 'Unavailable');
    assert.deepEqual(direct.artifacts, normalized.artifacts);
});

test('Phone artifact projection rejects empty records and does not synthesize resource authority from a path', () => {
    const projection = loadProjection();
    for (const artifact of [null, '', {}, { mimeType: 'application/pdf' }]) {
        const { current } = project(projection, projection.normalizePhoneRealtimeEvent(rawArtifactEvent(artifact)));
        assert.equal(current.artifacts.length, 0);
        assert.equal(current.nodes.filter(node => node.kind === 'artifact').length, 0);
    }
    const { current } = project(projection, projection.normalizePhoneRealtimeEvent(rawArtifactEvent({
        id: 'unscoped', source_path: '/synthetic/private/report.pdf',
        resource_ref: { kind: 'artifact_content', artifactId: 'unscoped' },
    })));
    assert.equal(current.artifacts[0].resourceRef, null, 'a missing session must stay unpreviewable');
    assert.equal(current.artifacts[0].sourcePath, '/synthetic/private/report.pdf');
});
