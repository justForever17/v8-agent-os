import test from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown } from '../src/markdown.js';
import { resolveTheme } from '../src/theme.js';
import { ViewStore } from '../src/persistence.js';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

test('renders the supported Markdown subset without executing markup or ANSI', () => {
  const rendered = renderMarkdown('# 标题\n\n- 项目 👩🏽‍💻\n> 引用\n`inline` [链接](https://example.test)\n\x1b[31m红色', { width: 24, theme: 'mono' });
  assert.deepEqual(rendered.lines.slice(0, 4), ['▌ 标题', '', '• 项目 👩🏽‍💻', '│ 引用']);
  assert.match(rendered.screenReader, /标题/);
  assert.match(rendered.screenReader, /链接 \(https:\/\/example.test\)/);
  assert.match(rendered.raw, /\x1b\[31m/);
  assert.doesNotMatch(rendered.lines.join(''), /\x1b\[/);
});

test('keeps an unfinished fence stable while streaming and wraps long code in narrow terminals', () => {
  const open = renderMarkdown('```ts\nconst x = "中文";', { width: 8 });
  assert.equal(open.rows[0].kind, 'fence');
  assert.ok(open.rows.some(row => row.kind === 'code'));
  assert.ok(open.lines.every(line => line.length > 0));
  const closed = renderMarkdown('```\nbody\n```', { width: 24 });
  assert.deepEqual(closed.rows.filter(row => row.kind === 'fence').map(row => row.text), ['┌─ code', '└─ code']);
});

test('NO_COLOR forces mono even when a visual theme was requested', () => {
  assert.equal(resolveTheme('dark', { NO_COLOR: '1' }).name, 'mono');
  assert.equal(resolveTheme('high-contrast', {}).name, 'high-contrast');
});

test('theme is local view state and survives a restart without entering Engine config', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-theme-'));
  try {
    const first = new ViewStore(root, 'engine');
    const view = first.bind('engine'); view.theme = 'high-contrast'; first.write(view);
    assert.equal(JSON.parse(readFileSync(first.file, 'utf8')).theme, 'high-contrast');
    assert.equal(new ViewStore(root, 'engine').bind('engine').theme, 'high-contrast');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('covers common terminal Markdown blocks, nesting, tasks, tables and safe inline degradation', () => {
  const source = [
    'Setext title',
    '============',
    '',
    '**bold** *italic* ~~deleted~~ `code` [link](https://example.test/a_(b))',
    '\\*literal\\* \\[text\\](https://ignored.example) \\q',
    '',
    '- [x] done',
    '  - nested',
    '    1. deep',
    '',
    '| A | B |',
    '| :--- | ---: |',
    '| x | y |',
    '',
    '---',
    '',
    '<script>alert(1)</script> \x1b[31mred',
  ].join('\n');
  const rendered = renderMarkdown(source, { width: 80, theme: 'mono' });
  assert.equal(rendered.rows.some(row => row.kind === 'heading'), true);
  assert.equal(rendered.rows.some(row => row.kind === 'list' && row.text.includes('☑')), true);
  assert.equal(rendered.rows.some(row => row.kind === 'table'), true);
  assert.equal(rendered.rows.some(row => row.kind === 'thematicBreak'), true);
  assert.match(rendered.lines.join('\n'), /bold italic deleted code link/);
  assert.match(rendered.lines.join('\n'), /\\q/);
  assert.match(rendered.lines.join('\n'), /<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(rendered.lines.join('\n'), /\x1b\[/);
  assert.doesNotMatch(rendered.screenReader, /\*\*|~~|`/);
  assert.match(rendered.screenReader, /checked/);
  const escaped = renderMarkdown('`\\*code\\*` \\*literal\\*', { width: 80, theme: 'mono' });
  assert.equal(escaped.lines.join(''), '\\*code\\* *literal*');
  const escapedLink = renderMarkdown('\\[text\\]\\(url\\)', { width: 80, theme: 'mono' });
  assert.equal(escapedLink.lines.join(''), '[text](url)');
});

test('does not close a four-backtick fence with a shorter marker and does not repeat screen-reader rows on wrapping', () => {
  const rendered = renderMarkdown('````ts\n**literal**\n```\nend\n````', { width: 80, theme: 'mono' });
  assert.equal(rendered.rows.filter(row => row.kind === 'fence').map(row => row.text).join('|'), '┌─ code (ts)|└─ code');
  assert.equal(rendered.screenReader.split('literal').length - 1, 1);
  assert.match(rendered.screenReader, /code block end/);
});
