const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  SOFTWARE_RENDERING_ARG,
  createGpuRecoveryController,
  softwareRenderingRelaunchArgs,
  softwareRenderingRequested,
} = require('../lib/gpu-recovery.cjs');

const bootstrapSource = fs.readFileSync(path.join(__dirname, '..', 'electron', 'bootstrap.cjs'), 'utf8');
const shellMainSource = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.cjs'), 'utf8');
const desktopPetMainSource = fs.readFileSync(
  path.join(__dirname, '..', '..', 'v8-agent-os-desktop-pet', 'electron', 'main.cjs'),
  'utf8',
);

test('GPU recovery delegates relaunch to governed Shell and desktop pet shutdown', () => {
  assert.match(bootstrapSource, /app\.emit\([\s\S]{0,80}'v8os-gpu-recovery-requested'/);
  assert.doesNotMatch(bootstrapSource, /app\.relaunch/);
  assert.match(shellMainSource, /quitApplication:[\s\S]{0,240}app\.relaunch\([\s\S]{0,120}app\.quit\(\)/);
  assert.match(desktopPetMainSource, /function finalizeShutdown[\s\S]{0,700}app\.relaunch\([\s\S]{0,120}app\.exit\(0\)/);
  assert.match(shellMainSource, /app\.emit\('v8os-governed-shutdown-started'\)/);
  assert.match(desktopPetMainSource, /app\.emit\('v8os-governed-shutdown-started'\)/);
});

test('software rendering can be requested without unsafe sandbox flags', () => {
  assert.equal(softwareRenderingRequested(['shell', SOFTWARE_RENDERING_ARG], {}), true);
  assert.equal(softwareRenderingRequested(['shell'], { V8OS_SOFTWARE_RENDERING: '1' }), true);
  assert.equal(softwareRenderingRequested(['shell', '--disable-gpu'], {}), true);
  assert.equal(softwareRenderingRequested(['shell'], {}), false);

  const args = softwareRenderingRelaunchArgs(['shell', '--profile', 'local', SOFTWARE_RENDERING_ARG]);
  assert.deepEqual(args, ['--profile', 'local', SOFTWARE_RENDERING_ARG]);
  assert.equal(args.includes('--disable-gpu-sandbox'), false);
  assert.equal(args.includes('--enable-unsafe-swiftshader'), false);
  assert.equal(args.includes('--no-sandbox'), false);
});

test('two abnormal GPU exits in the bounded window request one recovery', () => {
  let timestamp = 1_000;
  let recoveries = 0;
  const warnings = [];
  const controller = createGpuRecoveryController({
    now: () => timestamp,
    onRecover: () => { recoveries += 1; },
    logger: {
      warn: (...args) => warnings.push(args),
      error: () => undefined,
    },
  });

  assert.equal(controller.handle({ type: 'Utility', reason: 'crashed' }), false);
  assert.equal(controller.handle({ type: 'GPU', reason: 'clean-exit', exitCode: 0 }), false);
  assert.equal(controller.handle({ type: 'GPU', reason: 'crashed', exitCode: 1002 }), false);
  timestamp += 500;
  assert.equal(controller.handle({ type: 'GPU', reason: 'launch-failed', exitCode: 1002 }), true);
  assert.equal(controller.handle({ type: 'GPU', reason: 'crashed', exitCode: 1002 }), false);
  assert.equal(recoveries, 1);
  assert.equal(warnings.length, 4);
});

test('software rendering mode never enters a relaunch loop', () => {
  let recoveries = 0;
  const controller = createGpuRecoveryController({
    softwareRendering: true,
    onRecover: () => { recoveries += 1; },
    logger: { warn: () => undefined, error: () => undefined },
  });
  assert.equal(controller.handle({ type: 'GPU', reason: 'crashed' }), false);
  assert.equal(controller.handle({ type: 'GPU', reason: 'crashed' }), false);
  assert.equal(recoveries, 0);
});

test('governed shutdown disables recovery before GPU teardown events arrive', () => {
  let recoveries = 0;
  const controller = createGpuRecoveryController({
    onRecover: () => { recoveries += 1; },
    logger: { warn: () => undefined, error: () => undefined },
  });
  controller.disable();
  assert.equal(controller.handle({ type: 'GPU', reason: 'crashed' }), false);
  assert.equal(controller.handle({ type: 'GPU', reason: 'launch-failed' }), false);
  assert.equal(recoveries, 0);
});

test('GPU recovery remains fail-closed after a governed shutdown attempt', () => {
  const bootstrap = fs.readFileSync(path.join(__dirname, '..', 'electron', 'bootstrap.cjs'), 'utf8');
  const shell = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.cjs'), 'utf8');
  assert.match(bootstrap, /app\.on\('before-quit', \(\) => gpuRecovery\.disable\(\)\)/);
  assert.match(bootstrap, /app\.on\('v8os-governed-shutdown-started', \(\) => gpuRecovery\.disable\(\)\)/);
  assert.match(shell, /if \(!failure\) return;[\s\S]{0,120}gpuRecoveryRelaunchArgs = null;/);
  assert.match(shell, /quitting = false;/);
});
