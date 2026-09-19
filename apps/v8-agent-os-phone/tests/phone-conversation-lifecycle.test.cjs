const assert = require('node:assert/strict');
const test = require('node:test');
const { loadHook, tick, deferred } = require('./helpers/hook-harness.cjs');

function harness(mutate) {
    const drafts = new Map([['draft-A', { input: 'keep me', files: ['attachment-A'], queue: ['queued-A'] }]]);
    const flushed = [], loads = [], starts = [], stops = [], resets = [];
    const hook = loadHook('src/hooks/use-phone-conversation-lifecycle.ts', {
        '@/src/lib/phone-drafts': { phoneDrafts: { flush: async key => { flushed.push(key); } } },
    }, mutate);
    let options = {
        identityKey: 'device-A/principal-A', status: 'authenticated', activeConversationId: 'A',
        draftKey: 'draft-A', isFocused: true, appVisible: true,
        realtime: {
            startRealtime: async (id, token) => { starts.push({ id, token }); },
            stopRealtime: settings => { stops.push(settings); },
            isRealtimeActive: () => false,
        },
        hydrate: async (id, settings) => { loads.push({ id, settings }); return true; },
        resetView: preserve => { resets.push(preserve); },
    };
    const render = (patch = {}) => {
        options = { ...options, ...patch };
        return hook.render(({ usePhoneConversationLifecycle }) => usePhoneConversationLifecycle(options));
    };
    return { hook, render, drafts, flushed, loads, starts, stops, resets };
}

test('initial failed hydration still opens the existing realtime recovery path', async () => {
    const h = harness(); h.render({ hydrate: async () => false }); await tick();
    assert.deepEqual(h.starts.map(item => item.id), ['A']);
    h.hook.unmount();
});

for (const boundary of ['session', 'device', 'principal', 'blur', 'unmount']) {
    test(`${boundary} invalidates late hydration without clearing a persisted draft`, async () => {
        const h = harness(); const old = deferred();
        const first = h.render({ hydrate: async () => old.promise });
        const lease = first.beginHydration('A', { force: true });
        assert.equal(lease.isCurrent(), true);
        if (boundary === 'session') h.render({ activeConversationId: 'B', draftKey: 'draft-B', hydrate: async () => true });
        if (boundary === 'device') h.render({ identityKey: 'device-B/principal-A', draftKey: 'draft-B', hydrate: async () => true });
        if (boundary === 'principal') h.render({ identityKey: 'device-A/principal-B', draftKey: 'draft-B', hydrate: async () => true });
        if (boundary === 'blur') h.render({ isFocused: false });
        if (boundary === 'unmount') h.hook.unmount();
        const currentStarts = h.starts.length;
        old.resolve(true); await tick();
        assert.equal(lease.isCurrent(), false);
        assert.equal(h.starts.length, currentStarts + (['session', 'device', 'principal'].includes(boundary) ? 1 : 0));
        assert.ok(h.flushed.includes('draft-A'));
        assert.deepEqual(h.drafts.get('draft-A'), { input: 'keep me', files: ['attachment-A'], queue: ['queued-A'] });
        h.hook.unmount();
    });
}

test('stable renders do not reload; background resume hydrates and subscribes once', async () => {
    const h = harness(); h.render(); await tick(); h.render(); await tick();
    assert.equal(h.loads.length, 1);
    const clearsBefore = h.resets.length;
    h.render({ appVisible: false });
    assert.equal(h.resets.length, clearsBefore, 'background retains the current view');
    h.render({ appVisible: true }); await tick();
    assert.equal(h.loads.length, 2); assert.equal(h.starts.length, 2);
    h.hook.unmount();
});

test('hydration leases isolate two requests for the same session and their finally cleanup', async () => {
    const h = harness(); const lifecycle = h.render(); await tick();
    const old = lifecycle.beginHydration('A', { force: true });
    const latest = lifecycle.beginHydration('A', { force: true });
    old.finish(); old.markHydrated();
    assert.equal(old.isCurrent(), false); assert.equal(latest.isCurrent(), true);
    assert.equal(lifecycle.beginHydration('A'), null, 'old finally cannot free the new lease');
    latest.markHydrated(); latest.finish();
    assert.equal(lifecycle.beginHydration('A'), null, 'successful hydration prevents a duplicate non-forced load');
    lifecycle.invalidate();
    assert.ok(lifecycle.beginHydration('A'));
    h.hook.unmount();
});

test('a newly seeded conversation subscribes without erasing its optimistic view', async () => {
    const h = harness(); const lifecycle = h.render(); await tick();
    lifecycle.seedConversation('B');
    h.render({ activeConversationId: 'B', draftKey: 'draft-B' }); await tick();
    assert.deepEqual(h.loads.map(item => item.id), ['A']);
    assert.deepEqual(h.starts.map(item => item.id), ['A', 'B']);
    assert.equal(h.resets.at(-1), true);
    h.hook.unmount();
});

test('owner changes cannot reuse an optimistic seed belonging to the previous owner', async () => {
    const h = harness(); const lifecycle = h.render(); await tick(); lifecycle.seedConversation('B');
    h.render({ activeConversationId: 'B', identityKey: 'device-B/principal-A', draftKey: 'draft-B' }); await tick();
    assert.deepEqual(h.loads.map(item => item.id), ['A', 'B']);
    assert.equal(h.resets.at(-1), false);
    h.hook.unmount();
});

test('the missing-negation mutant loses the initial recovery subscription', async () => {
    const h = harness(source => source.replace('loaded || !realtime.isRealtimeActive', 'loaded || realtime.isRealtimeActive'));
    h.render({ hydrate: async () => false }); await tick();
    assert.equal(h.starts.length, 0, 'the same false-load input detects the independently reported regression');
    h.hook.unmount();
});
