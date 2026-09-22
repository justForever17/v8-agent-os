import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { relocatePythonConsoleScripts } from '../../desktop/prepare-posix-python-runtime.mjs';

test('relocation only rewrites scripts tied to this exact Python interpreter', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-console-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bin = path.join(root, 'bin'); fs.mkdirSync(bin);
  const python = path.join(bin, 'python3');
  const binary = Buffer.from([0x7f, 0x45, 0x4c, 0x46, 0, 0]);
  fs.writeFileSync(python, binary);
  const foreign = '#!/usr/bin/python3\nprint("foreign")\n';
  const shell = '#!/bin/sh\necho shell\n';
  fs.writeFileSync(path.join(bin, 'foreign'), foreign);
  fs.writeFileSync(path.join(bin, 'shell'), shell);
  fs.writeFileSync(path.join(bin, 'playwright'), `#!${python}\nprint("own")\n`);
  assert.deepEqual(relocatePythonConsoleScripts(python, root), ['playwright']);
  assert.deepEqual(fs.readFileSync(python), binary);
  assert.equal(fs.readFileSync(path.join(bin, 'foreign'), 'utf8'), foreign);
  assert.equal(fs.readFileSync(path.join(bin, 'shell'), 'utf8'), shell);
  assert(fs.readFileSync(path.join(bin, 'playwright'), 'utf8').endsWith('print("own")\n'));
  assert.deepEqual(relocatePythonConsoleScripts(python, root), []);
});

test('relocated script executes Python with unchanged arguments from a path containing spaces', { skip: process.platform !== 'linux' }, t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-relocation-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const runtime = path.join(root, 'original'), bin = path.join(runtime, 'bin');
  fs.mkdirSync(bin, { recursive: true });
  // The external interpreter is only a fixture oracle, never a product fallback.
  const realPython = execFileSync('sh', ['-c', 'command -v python3'], { encoding: 'utf8' }).trim();
  const python = path.join(bin, 'python3'); fs.symlinkSync(realPython, python);
  const script = path.join(bin, 'probe');
  fs.writeFileSync(script, `#!${python}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n`, { mode: 0o755 });
  assert.deepEqual(relocatePythonConsoleScripts(python, runtime), ['probe']);
  const moved = path.join(root, 'moved runtime with spaces'); fs.renameSync(runtime, moved);
  assert(!fs.existsSync(runtime));
  const args = ['hello world', '$literal', '中文', "single'quote"];
  assert.deepEqual(JSON.parse(execFileSync(path.join(moved, 'bin/probe'), args, { encoding: 'utf8' })), args);
});
