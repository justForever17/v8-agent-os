import assert from 'node:assert/strict';
import test from 'node:test';

import { buildActivityFromRuntimeEntry } from '../src/lib/desktopActivity';

test('desktop activity projects exact structured events instead of matching transcript words', () => {
  assert.equal(buildActivityFromRuntimeEntry({ id: 'tool-1', topic: 'tool.started' })?.event, 'tool.started');
  assert.equal(buildActivityFromRuntimeEntry({ id: 'research-tool-1', topic: 'research.tool.started' })?.event, 'tool.started');
  assert.equal(buildActivityFromRuntimeEntry({ id: 'approval-1', topic: 'approval.requested' })?.event, 'approval.requested');
  assert.equal(buildActivityFromRuntimeEntry({ id: 'unknown-1', topic: 'custom.unexposed.event' })?.event, null);
});
