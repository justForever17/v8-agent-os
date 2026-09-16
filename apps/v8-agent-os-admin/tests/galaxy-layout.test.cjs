const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/components/memory/galaxy-layout.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const projection = { exports: {} };
new Function('module', 'exports', compiled)(projection, projection.exports);
const { allocateOrbits, clusterCenter, visualNodeId, transformNode, inverseNode, localNode, stepCameraSpring } = projection.exports;

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

test('camera critically damps at both render rates without overshoot or zoom collapse', () => {
    const target = { x: 190, y: -66, zoom: 3.2 };
    const results = [];
    for (const hz of [30, 60]) {
        let camera = { x: 0, y: 0, zoom: .5 }, velocity = { x: 0, y: 0, zoom: 0 }, settled = false;
        for (let frame = 0; frame < hz * 2 && !settled; frame++) {
            const previous = camera;
            const next = stepCameraSpring(camera, target, velocity, 1 / hz);
            camera = next.camera; velocity = next.velocity; settled = next.settled;
            assert.ok(camera.x >= previous.x && camera.x <= target.x);
            assert.ok(camera.zoom >= previous.zoom && camera.zoom <= target.zoom);
            assert.ok(Number.isFinite(camera.y) && camera.zoom > 0);
        }
        assert.ok(settled); results.push(camera);
    }
    assert.deepEqual(results, [target, target]);
});

test('interrupting node focus preserves velocity then settles back to overview', () => {
    let camera = { x: 0, y: 0, zoom: .5 }, velocity = { x: 0, y: 100, zoom: 0 };
    for (let i = 0; i < 8; i++) ({ camera, velocity } = stepCameraSpring(camera, { x: 200, y: 0, zoom: 3 }, velocity, 1 / 60));
    const before = { ...camera };
    const first = stepCameraSpring(camera, { x: 0, y: 0, zoom: .5 }, velocity, 1 / 60);
    assert.ok(Math.hypot(first.camera.x - before.x, first.camera.y - before.y) < 30);
    ({ camera, velocity } = first);
    let settled = false;
    for (let i = 0; i < 180 && !settled; i++) ({ camera, velocity, settled } = stepCameraSpring(camera, { x: 0, y: 0, zoom: .5 }, velocity, 1 / 60));
    assert.ok(settled); assert.deepEqual(camera, { x: 0, y: 0, zoom: .5 });
});
