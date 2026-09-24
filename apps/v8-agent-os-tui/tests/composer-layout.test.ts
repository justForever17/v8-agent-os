import test from 'node:test';
import assert from 'node:assert/strict';
import { composerCursorPosition } from '../src/composer-layout.js';

test('cursor y follows transcript, candidate overlay and visible input window', () => {
  assert.deepEqual(composerCursorPosition({ columns: 80, historyHeight: 10, overlayHeight: 0, inputRow: 0, inputColumn: 0, inputOffset: 0 }), { x: 2, y: 15 });
  assert.deepEqual(composerCursorPosition({ columns: 80, historyHeight: 10, overlayHeight: 3, inputRow: 0, inputColumn: 0, inputOffset: 0 }), { x: 2, y: 18 });
  assert.deepEqual(composerCursorPosition({ columns: 80, historyHeight: 10, overlayHeight: 3, inputRow: 2, inputColumn: 4, inputOffset: 1 }), { x: 6, y: 19 });
});

test('cursor remains inside the terminal after wide graphemes or a narrow resize', () => {
  const point = composerCursorPosition({ columns: 12, promptWidth: 10, historyHeight: 1, overlayHeight: 5, inputRow: 4, inputColumn: 80, inputOffset: 0 });
  assert.equal(point.x, 9);
  assert.ok(point.y >= 0);
});

test('bottom-anchored cursor locks to terminal prompt row regardless of dynamic upper content', () => {
  const pos1 = composerCursorPosition({ columns: 80, rows: 24, inputHeight: 1, historyHeight: 10, overlayHeight: 0, inputRow: 0, inputColumn: 5, inputOffset: 0 });
  assert.deepEqual(pos1, { x: 7, y: 22 });

  const pos2 = composerCursorPosition({ columns: 80, rows: 24, inputHeight: 1, historyHeight: 6, overlayHeight: 4, inputRow: 0, inputColumn: 5, inputOffset: 0 });
  assert.deepEqual(pos2, { x: 7, y: 22 });
});
