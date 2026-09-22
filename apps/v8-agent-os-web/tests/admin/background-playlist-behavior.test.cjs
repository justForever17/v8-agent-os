const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Persistence, revision conflicts, receipts and real uploads moved with their
// owner to Engine's test_client_identity/test_engine_client_assets tests.
const source = fs.readFileSync(path.resolve(__dirname, '../../../../packages/product-ui/src/background-playlist.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const schema = {};
new Function('exports', compiled)(schema);

test('shared playlist preserves duplicate media references but rejects duplicate IDs and external media', () => {
    const media = '/user-assets/background/one.webp';
    const playlist = schema.normalizeBackgroundPlaylist({ items: [{ id: 'a', media, kind: 'image' }, { id: 'b', media, kind: 'image' }] });
    assert.equal(playlist.items.length, 2);
    assert.equal(schema.referencedBackgroundMedia({ webBackground: playlist }).size, 1);
    assert.throws(() => schema.normalizeBackgroundPlaylist({ items: [{ id: 'a', media, kind: 'image' }, { id: 'a', media, kind: 'image' }] }));
    assert.throws(() => schema.normalizeBackgroundPlaylist({ items: [{ id: 'a', media: 'https://untrusted.invalid/one.webp', kind: 'image' }] }));
    assert.throws(() => schema.normalizeBackgroundPlaylist({ imageDurationMs: 0 }));
});
