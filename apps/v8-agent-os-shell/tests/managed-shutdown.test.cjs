const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const mainSource = fs.readFileSync(path.resolve(__dirname, '..', 'electron', 'main.cjs'), 'utf8');

function loadFunction(name) {
  const start = mainSource.indexOf(`async function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist in electron/main.cjs`);
  const bodyStart = mainSource.indexOf('}) {', start) + 3;
  assert.ok(bodyStart > 2, `${name} must have a function body`);
  let depth = 0;
  let end = -1;
  for (let index = bodyStart; index < mainSource.length; index += 1) {
    if (mainSource[index] === '{') depth += 1;
    if (mainSource[index] === '}') depth -= 1;
    if (depth === 0) {
      end = index + 1;
      break;
    }
  }
  assert.notEqual(end, -1, `${name} must have a complete body`);
  const source = mainSource.slice(start, end);
  return Function(`"use strict"; ${source}; return ${name};`)();
}

const runManagedV8OSShutdown = loadFunction('runManagedV8OSShutdown');
const coreIds = ['engine', 'admin', 'web'];

function statuses(serviceIds, runningId = null) {
  return serviceIds.map((id) => ({
    id,
    pidAlive: id === runningId,
    portOpen: false,
    state: id === runningId ? 'managed_running' : 'stopped',
  }));
}

function createDependencies(overrides = {}) {
  const calls = [];
  const dependencies = {
    coreIds,
    desktopPetId: 'desktop-pet',
    shouldStopDesktopPet: true,
    stopDesktopPetGracefully: async () => { calls.push('pet:graceful'); },
    shellStop: async (serviceIds) => {
      calls.push(`stop:${serviceIds.join(',')}`);
      return serviceIds.map((id) => ({ id, status: 'stopped' }));
    },
    shellStatus: async (serviceIds) => statuses(serviceIds),
    waitForServicesStopped: async (shellStatus, _attempts, serviceIds) => shellStatus(serviceIds),
    removeShellProcessRecord: async () => { calls.push('record:remove'); },
    shellProcessRecordIdentity: { pid: 1234 },
    stopControl: async () => { calls.push('control:stop'); },
    quitApplication: () => { calls.push('app:quit'); },
    onDesktopPetShutdownError: () => { calls.push('pet:error'); },
    onCoreRetry: () => { calls.push('core:retry'); },
    ...overrides,
  };
  return { calls, dependencies };
}

test('managed V8OS shutdown commits only after core services and desktop pet are stopped', async () => {
  const { calls, dependencies } = createDependencies();
  const result = await runManagedV8OSShutdown(dependencies);

  assert.deepEqual(result, { ok: true, reason: 'stopped' });
  assert.deepEqual(calls, [
    'pet:graceful',
    'stop:engine,admin,web',
    'record:remove',
    'control:stop',
    'app:quit',
  ]);
});

test('managed V8OS shutdown may commit after the second verified core stop succeeds', async () => {
  let coreProbe = 0;
  const { calls, dependencies } = createDependencies({
    shellStatus: async (serviceIds) => {
      if (serviceIds.length === coreIds.length && serviceIds.every((id) => coreIds.includes(id))) {
        coreProbe += 1;
        return statuses(serviceIds, coreProbe === 1 ? 'engine' : null);
      }
      return statuses(serviceIds);
    },
  });

  const result = await runManagedV8OSShutdown(dependencies);

  assert.equal(result.ok, true);
  assert.equal(calls.filter((item) => item === 'stop:engine,admin,web').length, 2);
  assert.equal(calls.filter((item) => item === 'core:retry').length, 1);
  assert.deepEqual(calls.slice(-3), ['record:remove', 'control:stop', 'app:quit']);
});

test('managed V8OS shutdown keeps Shell ownership when a core service remains alive', async () => {
  const { calls, dependencies } = createDependencies({
    shellStatus: async (serviceIds) => statuses(serviceIds, serviceIds.includes('engine') ? 'engine' : null),
  });

  const result = await runManagedV8OSShutdown(dependencies);

  assert.equal(result.ok, false);
  assert.equal(result.reason, 'core_services_still_running');
  assert.deepEqual(result.remaining.map((item) => item.id), ['engine']);
  assert.equal(calls.filter((item) => item === 'stop:engine,admin,web').length, 2);
  assert.equal(calls.includes('record:remove'), false);
  assert.equal(calls.includes('control:stop'), false);
  assert.equal(calls.includes('app:quit'), false);
});

test('managed V8OS shutdown keeps Shell ownership when the desktop pet remains alive', async () => {
  const { calls, dependencies } = createDependencies({
    shellStatus: async (serviceIds) => statuses(
      serviceIds,
      serviceIds.includes('desktop-pet') ? 'desktop-pet' : null,
    ),
  });

  const result = await runManagedV8OSShutdown(dependencies);

  assert.equal(result.ok, false);
  assert.equal(result.reason, 'desktop_pet_still_running');
  assert.equal(calls.filter((item) => item === 'stop:desktop-pet').length, 1);
  assert.equal(calls.includes('record:remove'), false);
  assert.equal(calls.includes('control:stop'), false);
  assert.equal(calls.includes('app:quit'), false);
});

test('managed V8OS shutdown keeps Shell ownership when status verification throws', async () => {
  const { calls, dependencies } = createDependencies({
    shellStatus: async () => { throw new Error('status unavailable'); },
  });

  await assert.rejects(runManagedV8OSShutdown(dependencies), /status unavailable/);
  assert.equal(calls.includes('record:remove'), false);
  assert.equal(calls.includes('control:stop'), false);
  assert.equal(calls.includes('app:quit'), false);
});
