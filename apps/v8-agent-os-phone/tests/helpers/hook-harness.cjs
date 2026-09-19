const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};

// Only React scheduling is controlled. Dependency comparison, cleanup on change,
// callback identity and unmount are retained so stable renders cannot hide leaks.
function loadHook(relative, overrides = {}, mutate = source => source) {
    const slots = [], timers = new Map();
    let cursor = 0, now = 10_000, nextTimer = 0, pending = [];
    const changed = (before, after) => !before || !after || before.length !== after.length
        || before.some((value, index) => !Object.is(value, after[index]));
    const effect = (layout, callback, deps) => {
        const index = cursor++;
        if (!slots[index] || changed(slots[index].deps, deps)) pending.push({ index, callback, deps, layout });
    };
    const react = {
        useRef(initial) { return slots[cursor++] ||= { current: initial }; },
        useCallback(callback, deps) {
            const index = cursor++;
            if (!slots[index] || changed(slots[index].deps, deps)) slots[index] = { callback, deps };
            return slots[index].callback;
        },
        useLayoutEffect: (callback, deps) => effect(true, callback, deps),
        useEffect: (callback, deps) => effect(false, callback, deps),
    };
    const clock = {
        setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: now + delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        async advance(ms) {
            const end = now + ms;
            while (true) {
                const next = [...timers].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                now = next[1].at; timers.delete(next[0]); next[1].callback(); await tick();
            }
            now = end; await tick();
        },
        get pendingCount() { return timers.size; },
    };
    const filename = path.resolve(__dirname, '../../', relative);
    const exports = {};
    vm.runInNewContext(ts.transpileModule(mutate(fs.readFileSync(filename, 'utf8')), {
        fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, {
        exports, require: name => name === 'react' ? react : overrides[name] || require(name),
        AbortController, Date: class extends Date { static now() { return now; } },
        setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
        console: { warn() {} },
    });
    return {
        exports, clock,
        render(callback) {
            cursor = 0; pending = [];
            const result = callback(exports);
            for (const layout of [true, false]) {
                const effects = pending.filter(item => item.layout === layout);
                for (const item of effects) slots[item.index]?.cleanup?.();
                for (const item of effects) slots[item.index] = { deps: item.deps, cleanup: item.callback() };
            }
            return result;
        },
        unmount() { for (const slot of slots) slot?.cleanup?.(); },
    };
}

module.exports = { loadHook, tick, deferred };
