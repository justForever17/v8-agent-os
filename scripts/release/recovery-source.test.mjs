import assert from 'node:assert/strict';
import test from 'node:test';
import { validateRecoverySource } from './recovery-source.mjs';
const tag = 'v8-os-v2026.09.17.3';
const run = { path: '.github/workflows/release.yml', event: 'push', head_branch: tag, status: 'completed', head_sha: 'a'.repeat(40) };
const jobs = [{ name: 'Build', conclusion: 'success' }, { name: 'Verify required release products', conclusion: 'success' }, { name: 'Publish one V8OS release', conclusion: 'failure' }];
test('publisher-only failure can reuse verified artifacts', () => assert.equal(validateRecoverySource(run, jobs, tag), run.head_sha));
test('wrong tag, event or workflow cannot supply recovery artifacts', () => {
  for (const wrong of [{ head_branch: 'other' }, { event: 'pull_request' }, { path: '.github/workflows/fake.yml' }]) assert.throws(() => validateRecoverySource({ ...run, ...wrong }, jobs, tag));
});
test('failed build, missing gate or incomplete publisher blocks recovery', () => {
  assert.throws(() => validateRecoverySource(run, jobs.map((job, i) => i === 0 ? { ...job, conclusion: 'failure' } : job), tag));
  assert.throws(() => validateRecoverySource(run, jobs.filter(job => !job.name.startsWith('Verify')), tag));
  assert.throws(() => validateRecoverySource(run, jobs.map(job => ({ ...job, conclusion: 'cancelled' })), tag));
});
