import assert from 'node:assert/strict';
import test from 'node:test';

import { createActivityRefreshScheduler } from '../src/lib/activityRefreshScheduler';

const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

test('activity refresh scheduler coalesces bursts and keeps one trailing refresh', async () => {
  let calls = 0;
  const scheduler = createActivityRefreshScheduler(() => {
    calls += 1;
  }, { minimumIntervalMs: 20 });

  scheduler.schedule();
  scheduler.schedule();
  scheduler.schedule();
  await wait(5);
  assert.equal(calls, 1);
  await wait(30);
  assert.equal(calls, 2);
  scheduler.stop();
});

test('activity refresh scheduler never overlaps refresh requests', async () => {
  let calls = 0;
  let active = 0;
  let maximumActive = 0;
  let releaseFirst: (() => void) | undefined;
  const firstRefresh = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const scheduler = createActivityRefreshScheduler(async () => {
    calls += 1;
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    if (calls === 1) await firstRefresh;
    active -= 1;
  }, { minimumIntervalMs: 0 });

  scheduler.schedule();
  await wait(0);
  scheduler.schedule();
  scheduler.schedule();
  assert.equal(calls, 1);
  releaseFirst?.();
  await wait(5);
  assert.equal(calls, 2);
  assert.equal(maximumActive, 1);
  scheduler.stop();
});
