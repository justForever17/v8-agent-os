const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/components/memory/galaxy-layout.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const projection = { exports: {} };
new Function('module', 'exports', compiled)(projection, projection.exports);
const { allocateOrbits, clusterCenter, visualNodeId, transformNode, inverseNode, localNode } = projection.exports;

test('all cluster circles remain separated across complete orbital periods', () => {
    for (const count of [0, 1, 8, 32, 100]) {
        const orbits = allocateOrbits(['global', ...Array.from({ length: count }, (_, i) => `workspace-${i}`)]);
        for (let seconds = 0; seconds <= 3600; seconds += 17) {
            for (let i = 0; i < orbits.length; i++) for (let j = i + 1; j < orbits.length; j++) {
                const a = clusterCenter(orbits[i], seconds), b = clusterCenter(orbits[j], seconds);
                assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= orbits[i].radius + orbits[j].radius + 10);
            }
        }
    }
});
test('new IDs keep existing slots and structured visual identities cannot collide', () => {
    const initial = allocateOrbits(['global', 'z', 'y']);
    const next = allocateOrbits(['global', 'a', 'z', 'y'], initial);
    for (const orbit of initial) assert.deepEqual(next.find(item => item.id === orbit.id), orbit);
    assert.notEqual(visualNodeId('a:b', 'c'), visualNodeId('a', 'b:c'));
    assert.notEqual(visualNodeId('A', 'same'), visualNodeId('B', 'same'));
});
test('drag inverse uses the same transform as drawing and remains inside its cluster', () => {
    const orbit = allocateOrbits(['global', 'workspace'])[1];
    for (const seconds of [0, 43, 299, 3600]) {
        const local = localNode(4, 30, orbit.radius);
        const back = inverseNode(transformNode(local, orbit, seconds), orbit, seconds);
        assert.ok(Math.hypot(back.x - local.x, back.y - local.y) < 1e-8);
        const bounded = inverseNode({ x: 100000, y: 100000 }, orbit, seconds);
        assert.ok(Math.hypot(bounded.x, bounded.y) <= orbit.radius - 12 + 1e-8);
    }
});
