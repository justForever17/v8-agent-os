const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const petRoot = path.resolve(__dirname, '..');

test('desktop pet menu keeps actions visible and scrolls only the collapsed workspace session region', () => {
  const source = fs.readFileSync(path.join(petRoot, 'src', 'components', 'CyberPet.tsx'), 'utf8');

  assert.match(source, /expandedWorkspaceIds/);
  assert.match(source, /aria-expanded=\{expanded\}/);
  assert.match(source, /data-session-scroll-region="true"/);
  assert.match(source, /overflow-y-auto overscroll-contain/);
  assert.match(source, /\[scrollbar-width:none\]/);
  assert.match(source, /\[&::-webkit-scrollbar\]:hidden/);
  assert.match(source, /flex w-\[320px\][\s\S]*flex-col overflow-hidden/);
  assert.doesNotMatch(source, /max-h-\[calc\(100vh-24px\)\] overflow-y-auto/);
  assert.match(source, /interactionStatus/);
  assert.match(source, /aria-live="polite"/);
  assert.doesNotMatch(source, /\{v8Connection\?\.error\}/);
});
