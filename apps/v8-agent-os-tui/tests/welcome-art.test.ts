import test from 'node:test';
import assert from 'node:assert/strict';
import stringWidth from 'string-width';
import { welcomeArt, welcomeArtCandidates, welcomeArtProjection, welcomeArtWidth } from '../src/welcome-art.js';

test('welcome candidates are clean glyphs, token-safe, and in the intended terminal width', () => {
  for (const candidate of Object.values(welcomeArtCandidates)) {
    assert.ok(candidate.length >= 6);
    for (const line of candidate) {
      assert.equal(/[\x00-\x1f\x7f]/.test(line), false);
      const width = stringWidth(line);
      assert.ok(width >= 50 && width <= 75);
    }
  }
  assert.equal(welcomeArtWidth('prism'), 71);
  assert.equal(welcomeArtWidth('ribbon'), 64);
});

test('24/40 columns use one ASCII row and 80 columns use the full prism', () => {
  for (const width of [1, 8, 23, 24, 40, 50, 62, 70]) {
    const projection = welcomeArtProjection(width);
    assert.equal(projection.rows.length, 1);
    assert.ok(projection.lines.every(line => stringWidth(line) <= width));
    assert.equal(projection.rows[0].raw, projection.lines[0]);
  }
  const full = welcomeArtProjection(80);
  assert.equal(full.compact, false);
  assert.equal(full.rows.length, 8);
  assert.equal(full.lines.join(''), full.rows.map(row => row.segments.map(segment => segment.text).join('')).join(''));
  assert.deepEqual(welcomeArt(80, true), ['V8 AGENT OS']);
});

test('ribbon is a second selectable candidate and legacy names remain aliases', () => {
  const ribbon = welcomeArtProjection(80, false, 'ribbon');
  assert.equal(ribbon.variant, 'ribbon');
  assert.equal(ribbon.rows.length, 6);
  assert.equal(welcomeArt(80, false, 'mark').length, 8);
  assert.equal(welcomeArt(80, false, 'banner').length, 6);
});

test('logo rows never contain ANSI controls and expose semantic theme tokens', () => {
  const projection = welcomeArtProjection(80);
  assert.equal(projection.tokens.length, projection.lines.length);
  assert.ok(projection.rows.some(row => row.segments.some(segment => segment.token === 'selected')));
  assert.ok(projection.rows.some(row => row.segments.some(segment => segment.token === 'code')));
  assert.ok(projection.rows.some(row => row.segments.some(segment => segment.token === 'success')));
  assert.doesNotMatch(projection.lines.join('\n'), /\x1b|\u001b/);
  assert.doesNotMatch(projection.rows.flatMap(row => row.segments).map(segment => segment.text).join(''), /\x1b|\u001b/);
});
