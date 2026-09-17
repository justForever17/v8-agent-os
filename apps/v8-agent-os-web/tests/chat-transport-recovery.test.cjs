/* eslint-disable @typescript-eslint/no-require-imports */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '../src');
function readSource(relativePath) {
    const baseline = process.env.V8_WEB_TRANSPORT_BASELINE_REF;
    if (baseline && ['hooks/use-langgraph-stream.ts', 'app/chat/ChatClient.tsx'].includes(relativePath)) {
        return require('node:child_process').execFileSync('git', ['show', `${baseline}:apps/v8-agent-os-web/src/${relativePath}`], { cwd: root, encoding: 'utf8' });
    }
    return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

// Execute the production hook and projection modules. Only React scheduling,
// the view store and HTTP boundary are controlled by the harness.
function harness(fetcher, options = {}) {
    const refs = []; let slot = 0;
    const state = { messages: [], isLoading: false };
    const store = { ...state, setMessages: value => { state.messages = typeof value === 'function' ? value(state.messages) : value; }, setIsLoading: value => { state.isLoading = value; } };
    const cache = new Map();
    const react = {
        useRef(value) { const index = slot++; return refs[index] ||= { current: value }; },
        useState(value) { const ref = this.useRef(value); return [ref.current, next => { ref.current = next; }]; },
        useCallback: value => value,
        useEffect() {},
    };
    react.useState = react.useState.bind(react);
    const context = { console: { ...console, error() {}, warn() {} }, process: { env: { NODE_ENV: 'test' } }, crypto: require('node:crypto').webcrypto,
        fetch: fetcher, Response, ReadableStream, TextEncoder, TextDecoder, AbortController, AbortSignal, DOMException,
        setTimeout, clearTimeout, URL, URLSearchParams, Date };
    function load(name) {
        if (name === 'react') return react;
        if (name === '@/store/chat-store') return { useChatStore: () => ({ ...store, ...state }) };
        if (!name.startsWith('@/')) return require(name);
        if (cache.has(name)) return cache.get(name);
        const source = readSource(name.slice(2) + '.ts');
        const exports = {}; cache.set(name, exports);
        vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
            { ...context, exports, require: load });
        return exports;
    }
    const useHook = load('@/hooks/use-langgraph-stream').useLangGraphStream;
    const events = { errors: [], finishes: [], connects: [], resyncs: [] };
    let config = { apiEndpoint: '/api/chat', conversationId: 'A',
        onError: error => events.errors.push(error), onFinish: value => events.finishes.push(value),
        onConnect: (id, transport) => events.connects.push({ id, transport }),
        onResync: async id => { events.resyncs.push(id); state.messages = [{ id: 'canonical', role: 'assistant', content: 'Recovered' }]; }, ...options };
    return { state, events, load, render(changes = {}) { slot = 0; config = { ...config, ...changes }; return useHook(config); } };
}
const streamResponse = (...events) => new Response(events.map(event => typeof event === 'string' ? event : JSON.stringify(event) + '\n').join(''),
    { headers: { 'x-v8-agent-os-conversation-id': 'A', 'Content-Type': 'application/x-ndjson' } });
const textDelta = { type: 'text_chunk', content: 'Partial', run_id: 'run-A', message_id: 'assistant-A' };

for (const [name, tail] of [
    ['EOF without terminal', ''],
    ['Admin transport envelope', { type: 'transport_error', code: 'engine_stream_disconnected', error: 'connection lost', sessionId: 'A', runId: 'run-A', unknownOutcome: true, retryable: false, recovery: 'resync' }],
    ['malformed final frame', '{"type":'],
]) {
    test(`${name}: recover same session, keep canonical result, no onFinish or POST replay`, async () => {
        let posts = 0;
        const h = harness(async () => { posts++; return streamResponse(textDelta, tail); });
        const hook = h.render();
        assert.equal(await hook.sendMessage('work', { conversationId: 'A', clientMessageId: 'client-A' }), false);
        assert.deepEqual(h.events.resyncs, ['A']);
        assert.equal(h.state.messages[0].content, 'Recovered', 'pending partial batch cannot overwrite recovered snapshot');
        assert.equal(h.events.finishes.length, 0);
        assert.equal(h.events.errors.length, 1);
        assert.equal(h.events.errors[0].unknownOutcome, true);
        assert.equal(h.state.isLoading, false);
        assert.equal(posts, 1);
    });
}

test('valid terminal completes once; next message can be submitted', async () => {
    let posts = 0;
    const h = harness(async () => { posts++; return streamResponse(textDelta, { type: 'done' }); });
    assert.equal(await h.render().sendMessage('one', { conversationId: 'A' }), true);
    assert.equal(await h.render().sendMessage('two', { conversationId: 'A' }), true);
    assert.equal(posts, 2);
    assert.equal(h.events.finishes.length, 2);
    assert.equal(h.events.resyncs.length, 0);
    assert.equal(h.state.isLoading, false);
});

test('runtime error is surfaced and reconciled without a success callback', async () => {
    const h = harness(async () => streamResponse(textDelta, { type: 'error', error: 'provider failed' }));
    assert.equal(await h.render().sendMessage('work', { conversationId: 'A' }), false);
    assert.equal(h.events.finishes.length, 0);
    assert.equal(h.events.errors[0].message, 'provider failed');
    assert.deepEqual(h.events.resyncs, ['A']);
});

test('401 stays visible even when canonical recovery succeeds; no automatic resubmission', async () => {
    let posts = 0;
    const h = harness(async () => { posts++; return Response.json({ error: 'Unauthorized', code: 'auth_pre_execution' }, { status: 401 }); }, { submitEndpoint: '/api/chat-submit' });
    assert.equal(await h.render().sendMessage('work', { conversationId: 'A', clientMessageId: 'stable-A' }), false);
    assert.equal(posts, 1);
    assert.match(h.events.errors[0].message, /Unauthorized/);
    assert.equal(h.state.isLoading, false);
});

test('late durable acknowledgement cannot change session B or clear its active request', async () => {
    let acceptA; let acceptB;
    const h = harness((_url, init) => new Promise(resolve => {
        if (JSON.parse(init.body).session_id === 'A') acceptA = resolve; else acceptB = resolve;
    }), { submitEndpoint: '/api/chat-submit' });
    const old = h.render().sendMessage('A task', { conversationId: 'A' });
    const nextHook = h.render({ conversationId: 'B' });
    const next = nextHook.sendMessage('B task', { conversationId: 'B' });
    acceptA(Response.json({ accepted: true, session_id: 'A', run_id: 'run-A' }));
    assert.equal(await old, false);
    assert.equal(h.events.connects.length, 0);
    assert.equal(h.state.isLoading, true);
    acceptB(Response.json({ accepted: true, session_id: 'B', run_id: 'run-B' }));
    assert.equal(await next, true);
    assert.equal(nextHook.getSubmittedRunId(), 'run-B');
    assert.deepEqual(h.events.connects, [{ id: 'B', transport: 'submit' }]);
});

test('aborted old request does not hydrate session A over session B', async () => {
    let rejectA;
    const h = harness(() => new Promise((_resolve, reject) => { rejectA = reject; }), { submitEndpoint: '/api/chat-submit' });
    const hook = h.render();
    const old = hook.sendMessage('A task', { conversationId: 'A' });
    hook.stop();
    h.render({ conversationId: 'B' });
    h.state.messages = [{ id: 'B', content: 'B history' }];
    rejectA(new DOMException('aborted', 'AbortError'));
    assert.equal(await old, false);
    assert.equal(h.state.messages[0].content, 'B history');
    assert.equal(h.events.resyncs.length, 0);
    assert.equal(h.events.errors.length, 0);
});

test('retry after unknown acceptance keeps request id and a single optimistic user message', async () => {
    const requests = [];
    const h = harness(async (_url, init) => {
        requests.push(JSON.parse(init.body));
        if (requests.length === 1) return Response.json({ error: 'unknown outcome', unknownOutcome: true }, { status: 504 });
        return Response.json({ accepted: true, session_id: 'A', run_id: 'run-A' });
    }, { submitEndpoint: '/api/chat-submit', onResync: async () => {} });
    const data = { conversationId: 'A', clientMessageId: 'stable-A', projectId: 'project-A', workspaceId: 'ws-A' };
    assert.equal(await h.render().sendMessage('same work', data), false);
    assert.equal(await h.render().sendMessage('same work', data), true);
    assert.deepEqual(requests.map(request => request.clientMessageId), ['stable-A', 'stable-A']);
    assert.equal(h.state.messages.filter(message => message.id === 'stable-A').length, 1);
    assert.equal(requests[1].messages.filter(message => message.id === 'stable-A').length, 1);
    assert.equal(requests[1].workspace_id, 'ws-A');
});

test('durable acceptance binds only its pending assistant and keeps the render identity', async () => {
    let accept;
    const h = harness(() => new Promise(resolve => { accept = resolve; }), { submitEndpoint: '/api/chat-submit' });
    const submission = h.render().sendMessage('Hello', { conversationId: 'A', clientMessageId: 'client-A' });
    const placeholder = h.state.messages.find(message => message.role === 'assistant');
    accept(Response.json({ accepted: true, session_id: 'A', run_id: 'run-A' }));
    assert.equal(await submission, true);
    const answer = h.state.messages.find(message => message.role === 'assistant');
    assert.equal(answer.runId, 'run-A');
    assert.equal(answer.renderKey, placeholder.renderKey);
    assert.equal(answer.metadata.clientMessageId, 'client-A');
});

test('server receipt timestamp cannot move the waiting assistant above its user', () => {
    const h = harness(async () => Response.json({}));
    const state = h.load('@/lib/chat-stream-state');
    const assistant = { ...state.buildAssistantMessage({}), runId: 'run-A', timestamp: 100 };
    const user = { id: 'client-A', role: 'user', content: 'Hello', runId: 'run-A', timestamp: 200, ordinal: 1 };
    const normalized = state.normalizeMessagesForState([assistant, user]);
    assert.equal(normalized[0].id, 'client-A');
    assert.equal(normalized[1].renderKey, assistant.renderKey);
    const nextUser = { ...user, id: 'client-B', ordinal: 3 };
    assert.deepEqual(Array.from(state.normalizeMessagesForState([nextUser, { ...assistant, ordinal: 2 }, user]), m => m.ordinal), [1, 2, 3]);
});

test('authoritative replacement keeps DOM identity but replaces all content after an epoch change', () => {
    const h = harness(async () => Response.json({}));
    const state = h.load('@/lib/chat-stream-state');
    const prior = { ...state.buildAssistantMessage({}), id: 'answer-A', runId: 'run-A', content: 'Old text', nodes: [{id: 'old-node'}] };
    const canonical = { id: 'answer-A', role: 'assistant', runId: 'run-A', content: 'User revision', nodes: [], version: 2 };
    const [next] = state.preserveMessageRenderKeys([prior], [canonical]);
    assert.equal(next.renderKey, prior.renderKey);
    assert.equal(next.content, 'User revision');
    assert.equal(next.nodes.length, 0);
    assert.equal(next.version, 2);
});

test('typed failed and interrupted runs expose a reason while unknown/active/cancelled stays distinct', () => {
    const h = harness(async () => Response.json({}));
    const { readRunFailureMessage } = h.load('@/lib/chat/run-activity');
    for (const detail of [{error_message: 'Budget denied'}, {errorMessage: 'Circuit open'}, {error: 'Provider 503'}, {error: {message: 'Disconnected'}}]) {
        assert.notEqual(readRunFailureMessage({status: 'failed', ...detail}, 'fallback'), 'fallback');
    }
    assert.equal(readRunFailureMessage({status: 'interrupted'}, 'Recover the run'), 'Recover the run');
    for (const status of ['running', 'cancelled', 'unknown', 'completed']) assert.equal(readRunFailureMessage({status, error: 'old'}, 'fallback'), '');
});

test('a compact failed-run snapshot reads its durable error by run id; older run errors stay isolated', () => {
    const h = harness(async () => Response.json({}));
    const { readRunFailureMessage } = h.load('@/lib/chat/run-activity');
    const ast = ts.createSourceFile('ChatClient.tsx', readSource('app/chat/ChatClient.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let expression;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'runFailureMessage') expression = node.initializer.getText(ast);
        ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(expression);
    const bindings = {readRunFailureMessage, localConversationLoading:false, activeConversationRunning:false,
        currentRun:{id:'run-B',status:'failed'}, runEntries:[{id:'run-A',status:'failed',error_message:'Old failure'}, {id:'run-B',status:'failed',error_message:'Provider 503'}], t:()=> 'fallback'};
    const read = () => vm.runInNewContext(expression, bindings);
    assert.equal(read(), 'Provider 503');
    bindings.currentRun = {id:'run-C',status:'failed'};
    assert.equal(read(), 'fallback');
    bindings.localConversationLoading = true;
    assert.equal(read(), '');
});

function actualQueueSubmit(bindings) {
    return actualClientCallback('submitQueuedMessage', bindings);
}

function actualClientCallback(name, bindings) {
    const source = readSource('app/chat/ChatClient.tsx');
    const ast = ts.createSourceFile('ChatClient.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let callback;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name) callback = node.initializer.arguments[0];
        if (ts.isPropertyAssignment(node) && node.name.getText(ast) === name) callback = node.initializer;
        ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(callback);
    const compiled = ts.transpileModule(`const submit = ${callback.getText(ast)}; exports.submit = submit;`,
        { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const exports = {};
    vm.runInNewContext(compiled, { exports, ...bindings });
    return exports.submit;
}

function historyFixture(latestEventSeq, detailSeq, epoch = 0) {
    const h = harness(async () => Response.json({}));
    const shared = require('@v8/session-realtime');
    const state = h.load('@/lib/chat-stream-state');
    const applied = [];
    const owner = {current: 'instance-A/principal-A'};
    const noop = () => {};
    const load = actualClientCallback('loadConversationHistory', {
        ...shared, ...state, AbortController, AbortSignal, console,
        historyLoadControllerRef: {current: null}, activeConversationIdRef: {current: 'A'},
        sessionOwnerRef: owner,
        latestRealtimeSeqRef: {current: latestEventSeq}, turnIndexRef: {current: []},
        setTurnIndex: noop, setTotalTurnCount: noop, setFocusedTurnId: noop,
        fetch: async () => Response.json({contextEpoch: epoch, transcriptRevision: 1, messages: [], projection: {latestSeq: detailSeq, runtimeStatus: 'running'}}),
        loadConversationTurnPage: async () => ({messages: [], pageInfo: {}}),
        setQueuedMessageError: noop, router: {replace: noop},
        transcriptIdentitiesRef: {current: new Map()}, setTranscriptIdentity: noop,
        realtimeMessageStateRef: {current: {pendingRuntimeEvents: []}},
        setLegacyChatUnsupported: noop, isLegacyChatUnsupportedPayload: () => false,
        applyQueuedMessagesSnapshot: noop, extractQueuedMessages: () => [], synchronizeQueue: noop,
        setSessionProjection: noop, mergeRuntimeTimelineSnapshot: () => ({}),
        askUserApprovalId: '', applyAskUserPendingApproval: noop,
        shouldPreserveCurrentHistoryOnEmpty: () => true,
        messagesRef: {current: [{id: 'assistant-A', role: 'assistant', content: 'Already visible', runId: 'run-A', nodes: []}]},
        mergeTurnIndexEntries: noop, turnBeforeCursorRef: {current: null}, isLoadingOlderTurnsRef: {current: false},
        setIsLoadingOlderTurns: noop, setHasOlderTurns: noop, messageCacheRef: {current: new Map()},
        applyProjectedSnapshot: (messages, seq) => { applied.push({messages, seq}); return messages; },
        applySessionProcessSurface: noop, window: {setTimeout: noop},
    });
    return {load, applied, owner};
}

test('parallel detail watermark cannot acknowledge events absent from the turn-window response', async () => {
    const f = historyFixture(0, 20);
    await f.load('A', {preserveCurrentOnEmpty: true});
    assert.equal(f.applied.length, 1);
    assert.equal(f.applied[0].seq, 0, 'only the message snapshot itself may advance the covered-event cursor');
    assert.equal(f.applied[0].messages[0].content, 'Already visible');
});

test('late history started before a text or failure event cannot replace the newer projection', async () => {
    const f = historyFixture(20, 3);
    await f.load('A', {preserveCurrentOnEmpty: true});
    assert.equal(f.applied.length, 0);
});

test('instance/principal changes invalidate history even when the session id is reused', async () => {
    const f = historyFixture(0, 3);
    const pending = f.load('A');
    f.owner.current = 'instance-B/principal-B';
    await pending;
    assert.equal(f.applied.length, 0);
});

test('new context epoch can replace messages even across an older sequence watermark', async () => {
    const f = historyFixture(20, 3, 2);
    await f.load('A', {replaceTranscript: true, preserveCurrentOnEmpty: true});
    assert.equal(f.applied.length, 1);
    assert.equal(f.applied[0].seq, 3);
    assert.equal(f.applied[0].messages.length, 0, 'new epoch is authoritative; old visible content is not retained');
});

test('canonical recovery advances the existing snapshot cursor; replay is ignored and new tail remains usable', async () => {
    const realtime = require('@v8/session-realtime');
    const h = harness(async () => streamResponse(textDelta));
    const stateModule = h.load('@/lib/chat-stream-state');
    const messagesRef = { current: [] };
    const covered = { current: 3 }; const latest = { current: 3 };
    const stateRef = { current: realtime.createInitialSessionRealtimeMessageState([], stateModule.WEB_STREAM_LIFECYCLE_OPTIONS) };
    const applySnapshot = actualClientCallback('applyProjectedSnapshot', {
        runtimeFlushFrameRef: { current: null }, runtimeFlushTimerRef: { current: null },
        ...stateModule, ...realtime, messagesRef, realtimeMessageStateRef: stateRef,
        // This case recovers a complete canonical message at sequence 8.
        mergeProjectedSnapshotMessages: (_current, incoming) => stateModule.normalizeProjectedMessages(incoming),
        snapshotCoveredRealtimeSeqRef: covered, latestRealtimeSeqRef: latest,
        seenRealtimeEventIdentitiesRef: { current: { pruneSnapshotCovered() {} } },
        setMessages: value => { h.state.messages = value; },
    });
    const reads = [];
    const resync = actualClientCallback('onResync', {
        activeConversationIdRef: { current: 'A' },
        loadConversationHistory: async (id, options) => {
            reads.push({ id, options });
            const response = Response.json({ messages: [{ id: 'canonical-A', role: 'assistant', content: 'canonical answer', runId: 'run-A' }], latestSeq: 8 });
            const snapshot = await response.json();
            applySnapshot(snapshot.messages, snapshot.latestSeq, options);
        }, loadRuns: async id => reads.push({ runs: id }),
    });
    const accepted = await h.render({ onResync: resync }).sendMessage('work', { conversationId: 'A' });
    assert.equal(accepted, false);
    assert.equal(covered.current, 8);
    assert.equal(latest.current, 8);
    assert.equal(h.state.messages[0].content, 'canonical answer');
    assert.deepEqual(reads.map(read => read.id || read.runs), ['A', 'A']);
    assert.equal(realtime.evaluateSessionRuntimeEvent({ type: 'text_chunk', seq: 8 }, { snapshotCoveredSeq: covered.current }).accept, false);
    assert.equal(realtime.evaluateSessionRuntimeEvent({ type: 'text_chunk', seq: 9 }, { snapshotCoveredSeq: covered.current }).accept, true);
    assert.equal(h.events.finishes.length, 0);
    assert.equal(h.state.isLoading, false);
});

test('late run-status body cannot replace another session or a newer same-session response', async () => {
    let finishBody; const active = { current: 'A' }; const entries = [];
    let delayed = true;
    const loadRuns = actualClientCallback('loadRuns', {
        activeConversationIdRef: active, runLoadGenerationRef: { current: 0 }, AbortSignal,
        fetch: async () => delayed ? { ok: true, json: () => new Promise(resolve => { finishBody = resolve; }) }
            : Response.json({ runs: [{ id: 'new-run', status: 'running' }] }),
        setRunEntries: runs => entries.push(runs), isRecognizedRunStatus: () => false, console,
    });
    const old = loadRuns('A'); await new Promise(setImmediate);
    active.current = 'B';
    finishBody({ runs: [{ id: 'old-run', status: 'failed' }] }); await old;
    assert.equal(entries.length, 0);
    active.current = 'A';
    const stale = loadRuns('A'); await new Promise(setImmediate);
    delayed = false; await loadRuns('A');
    finishBody({ runs: [{ id: 'old-run', status: 'failed' }] }); await stale;
    assert.deepEqual(entries, [[{ id: 'new-run', status: 'running' }]]);
});

test('running composer uses durable JSON acceptance, preserving scope and id across busy/idle race', async () => {
    const requests = []; const queue = []; const reloads = []; let busy = true;
    const submit = actualQueueSubmit({
        activeConversationIdRef: { current: 'A' }, session: { user: { id: 'user-A' } },
        buildScopePayload: id => ({ conversationId: id, projectId: 'project-A', workspaceId: 'ws-A' }),
        messagesRef: { current: [] },
        fetch: async (url, init) => {
            const request = JSON.parse(init.body); requests.push({ url, request });
            assert.equal(url, '/api/chat-submit', 'stream endpoint cannot acknowledge a queued JSON request');
            return Response.json(busy ? { accepted: true, queued: true, session_id: 'A', queuedMessage: { id: 'q-A', sessionId: 'A', clientMessageId: request.clientMessageId } }
                : { accepted: true, queued: false, session_id: 'A', runId: 'run-next' });
        },
        upsertQueuedMessage: item => queue.push(item), setQueuedMessagesCollapsed() {}, setQueuedMessageError() {},
        synchronizeQueue() {}, loadConversationHistory: async id => reloads.push(id), loadRuns: async () => {},
        readErrorPayloadMessage: payload => payload.error, t: key => key,
    });
    await submit('first', { clientMessageId: 'stable-A', conversationId: 'wrong', workspaceId: 'wrong' });
    busy = false;
    await submit('second', { clientMessageId: 'stable-B' });
    assert.equal(queue.length, 1);
    assert.equal(queue[0].clientMessageId, 'stable-A');
    assert.equal(requests[0].request.data.conversationId, 'A');
    assert.equal(requests[0].request.workspace_id, 'ws-A');
    assert.deepEqual(reloads, ['A']);
});

test('composer renders a transport error without a queue and offers GET recovery without a new submission', async () => {
    const source = readSource('app/chat/ChatClient.tsx');
    const ast = ts.createSourceFile('ChatClient.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let dock;
    function visit(node) {
        if (ts.isJsxElement(node) && node.openingElement.attributes.properties.some(attribute =>
            ts.isJsxAttribute(attribute) && attribute.name.getText(ast) === 'data-testid' && attribute.initializer?.text === 'chat-transient-dock')) {
            dock = node.parent;
            while (dock && ts.isParenthesizedExpression(dock)) dock = dock.parent;
        }
        ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(dock && ts.isConditionalExpression(dock));
    const recovery = []; const errors = [];
    const exports = {};
    const jsx = ts.transpileModule(`exports.tree = (${dock.getText(ast)});`, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(jsx, { exports, require, activeConversationId: 'A', activeConversationIdRef: { current: 'A' },
        hasAskUserSurface: false, visibleQueuedMessages: [], chatTransportError: 'Unauthorized', visibleChatError: 'Unauthorized', runFailureMessage: '', queuedMessageError: '', scopeLoading: false,
        loadSessionScope: async id => { recovery.push(['scope', id]); return true; },
        loadConversationHistory: async id => recovery.push(['history', id]), loadRuns: async id => recovery.push(['runs', id]),
        synchronizeQueue: async id => recovery.push(['queue', id]), setChatTransportError: value => errors.push(value),
    });
    const html = require('react-dom/server').renderToStaticMarkup(exports.tree);
    assert.match(html, /role="alert"/);
    assert.match(html, /Unauthorized/);
    let button;
    function walk(node) {
        if (!node || typeof node !== 'object') return;
        if (node.type === 'button') button = node;
        const children = node.props?.children;
        for (const child of Array.isArray(children) ? children.flat() : [children]) walk(child);
    }
    walk(exports.tree); assert.ok(button);
    await button.props.onClick();
    assert.deepEqual(recovery, [['scope', 'A'], ['history', 'A'], ['runs', 'A'], ['queue', 'A']]);
    assert.deepEqual(errors, ['']);
});
