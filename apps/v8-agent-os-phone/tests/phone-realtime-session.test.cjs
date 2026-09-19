const assert = require('node:assert/strict');
const test = require('node:test');
const { loadHook, tick, deferred } = require('./helpers/hook-harness.cjs');

function harness(mutate) {
    const hook = loadHook('src/hooks/use-phone-realtime-session.ts', {}, mutate);
    const streams = [], snapshots = [], applied = [], synced = [], events = [];
    let token = 1, resetCount = 0;
    let options = {
        identityKey: 'device-A/principal-A', activeConversationId: 'session', enabled: true,
        authorizedRealtimeStream: async (path, onEvent, signal) => {
            const done = deferred(); streams.push({ path, onEvent, signal, done }); return done.promise;
        },
        isCurrentTransition: (_id, captured) => captured === undefined || captured === token,
        onEvent: (name, payload) => events.push([name, payload]),
        shouldKeepAlive: () => true,
        resetMessageState: () => { resetCount++; },
        snapshot: {
            getSyncCursor: async () => null,
            fetchSnapshot: async (id, signal) => {
                const done = deferred(); snapshots.push({ id, signal, done }); return done.promise;
            },
            syncTimeline: async () => ({ messages: [], deletions: [], syncCursor: 'new', sessionId: 'session' }),
            persistSync: async (_id, value) => { synced.push(value); },
            applySnapshot: value => applied.push(value),
        },
    };
    const render = (patch = {}) => {
        options = { ...options, ...patch };
        return hook.render(({ usePhoneRealtimeSession }) => usePhoneRealtimeSession(options));
    };
    return { hook, streams, snapshots, applied, synced, events, render,
        get options() { return options; }, get resets() { return resetCount; }, advanceToken() { token++; } };
}

test('unchanged renders keep the subscription; transition, stop and late events are fenced', async () => {
    const h = harness(); const session = h.render();
    const running = session.startRealtime('session', 1);
    h.render();
    assert.equal(h.streams[0].signal.aborted, false);
    assert.match(h.streams[0].path, /surface=phone&compact=1/);
    h.streams[0].onEvent('runtime', 'before');
    h.advanceToken();
    h.streams[0].onEvent('runtime', 'late');
    h.streams[0].done.resolve(); await running;
    await session.startRealtime('session', 1);
    assert.equal(h.streams.length, 1, 'a stale transition cannot reopen the stream');
    assert.deepEqual(h.events, [['runtime', 'before']]);
    assert.equal(h.hook.clock.pendingCount, 0);
    h.hook.unmount();
});

for (const boundary of ['stop', 'blur', 'unmount', 'device', 'principal', 'session']) {
    test(`${boundary} rejects a late snapshot and aborts the old request`, async () => {
        const h = harness(); const session = h.render();
        session.scheduleSnapshotRefresh('session', { force: true });
        await h.hook.clock.advance(900);
        assert.equal(h.snapshots.length, 1);
        if (boundary === 'stop') session.stopRealtime();
        if (boundary === 'blur') h.render({ enabled: false });
        if (boundary === 'unmount') h.hook.unmount();
        if (boundary === 'device') h.render({ identityKey: 'device-B/principal-A' });
        if (boundary === 'principal') h.render({ identityKey: 'device-A/principal-B' });
        if (boundary === 'session') h.render({ activeConversationId: 'session-B' });
        assert.equal(h.snapshots[0].signal.aborted, true);
        h.snapshots[0].done.resolve({ sessionId: 'session', latestSeq: 10 }); await tick();
        assert.deepEqual(h.applied, []);
        assert.equal(h.hook.clock.pendingCount, 0);
        h.hook.unmount();
    });
}

test('old finally cannot free the new snapshot slot; duplicate refreshes coalesce', async () => {
    const h = harness(); const first = h.render();
    first.scheduleSnapshotRefresh('session', { force: true }); await h.hook.clock.advance(900);
    const current = h.render({ identityKey: 'device-A/principal-B' });
    current.scheduleSnapshotRefresh('session', { force: true }); await h.hook.clock.advance(900);
    h.snapshots[0].done.resolve({ owner: 'old' }); await tick();
    for (let i = 0; i < 5; i++) current.scheduleSnapshotRefresh('session', { force: true });
    await h.hook.clock.advance(900);
    assert.equal(h.snapshots.length, 2, 'the current in-flight request retains its slot');
    h.snapshots[1].done.resolve({ owner: 'new' }); await tick();
    await h.hook.clock.advance(900);
    assert.equal(h.snapshots.length, 3, 'only one coalesced follow-up');
    assert.deepEqual(h.applied, [{ owner: 'new' }]);
    h.hook.unmount(); h.snapshots[2].done.resolve({ owner: 'too-late' }); await tick();
    assert.equal(h.applied.length, 1);
});

test('snapshot and timeline sync begin concurrently; cancel prevents persistence and projection', async () => {
    const h = harness(); const sync = deferred(); let syncStarted = false;
    const session = h.render({ snapshot: { ...h.options.snapshot,
        getSyncCursor: async () => 'cursor',
        syncTimeline: async (_id, _cursor, signal) => { syncStarted = true; assert.equal(signal.aborted, false); return sync.promise; },
    } });
    session.scheduleSnapshotRefresh('session', { force: true }); await h.hook.clock.advance(900);
    assert.equal(syncStarted, true);
    assert.equal(h.snapshots.length, 1, 'snapshot starts while sync remains pending');
    session.stopRealtime();
    sync.resolve({ messages: [], deletions: [], syncCursor: 'new', sessionId: 'session' });
    h.snapshots[0].done.resolve({ latestSeq: 1 }); await tick();
    assert.deepEqual(h.synced, []); assert.deepEqual(h.applied, []);
    h.hook.unmount();
});

test('disconnect retries are cancellable and cannot forward callbacks after stop', async () => {
    const h = harness(); const session = h.render();
    const running = session.startRealtime('session', 1);
    h.streams[0].done.reject(new Error('disconnected')); await tick();
    await h.hook.clock.advance(1400);
    assert.equal(h.streams.length, 2);
    h.streams[1].onEvent('runtime', 'resumed');
    session.stopRealtime();
    h.streams[1].onEvent('runtime', 'late');
    h.streams[1].done.resolve(); await running;
    h.snapshots[0].done.resolve({ late: true }); await tick();
    assert.deepEqual(h.events, [['runtime', 'resumed']]);
    assert.deepEqual(h.applied, []); assert.equal(h.hook.clock.pendingCount, 0);
    h.hook.unmount();
});

test('snapshot generation mutant is detected with the session id unchanged', async () => {
    const h = harness(source => source.replace('generation.current === epoch', 'true'));
    const session = h.render(); session.scheduleSnapshotRefresh('session', { force: true });
    await h.hook.clock.advance(900); session.stopRealtime();
    h.snapshots[0].done.resolve({ stale: true }); await tick();
    assert.equal(h.applied.length, 1, 'removing the generation check reproduces the independent late-apply defect');
    h.hook.unmount();
});
