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

test('cursor position accounts for pending approval rows and multiline input offset', () => {
  // Top-relative fallback with pendingRows: 1
  const posFallback = composerCursorPosition({
    columns: 80,
    historyHeight: 10,
    overlayHeight: 0,
    pendingRows: 1,
    inputRow: 1,
    inputColumn: 3,
    inputOffset: 0,
  });
  assert.deepEqual(posFallback, { x: 5, y: 17 }); // 2(header) + 10(history) + 1(pending) + 1(notice) + 1(label) + 1(divider) + 0(overlay) + 1(row) = 17

  // Bottom-anchored multiline editor with inputHeight: 3 and scroll offset
  const posMultiline = composerCursorPosition({
    columns: 80,
    rows: 24,
    inputHeight: 3,
    inputRow: 4,
    inputColumn: 10,
    inputOffset: 2, // currentRowOffset = 4 - 2 = 2
  });
  // targetY = 24 - 1(hint) - 3(inputHeight) + 2(offset) = 22
  assert.deepEqual(posMultiline, { x: 12, y: 22 });
});

test('cursor x and y never exceed physical terminal boundaries even under extreme inputs', () => {
  const clampedExtreme = composerCursorPosition({
    columns: 20,
    rows: 10,
    inputHeight: 1,
    inputRow: 50,
    inputColumn: 100,
    inputOffset: 0,
  });
  assert.ok(clampedExtreme.x <= 19);
  assert.ok(clampedExtreme.x >= 0);
  assert.ok(clampedExtreme.y <= 9);
  assert.ok(clampedExtreme.y >= 0);

  const clampedNegative = composerCursorPosition({
    columns: 80,
    rows: 24,
    inputHeight: 1,
    inputRow: 0,
    inputColumn: -10,
    inputOffset: 5,
  });
  assert.ok(clampedNegative.x >= 0);
  assert.ok(clampedNegative.y >= 0);
});

