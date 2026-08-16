const SOFTWARE_RENDERING_ARG = '--v8os-software-rendering';
const GPU_FAILURE_REASONS = new Set(['abnormal-exit', 'crashed', 'oom', 'launch-failed']);

function softwareRenderingRequested(argv = process.argv, environment = process.env) {
  return environment?.V8OS_SOFTWARE_RENDERING === '1'
    || argv.includes(SOFTWARE_RENDERING_ARG)
    || argv.includes('--disable-gpu');
}

function softwareRenderingRelaunchArgs(argv = process.argv) {
  return [
    ...argv.slice(1).filter((argument) => argument !== SOFTWARE_RENDERING_ARG),
    SOFTWARE_RENDERING_ARG,
  ];
}

function createGpuRecoveryController(options = {}) {
  const failureThreshold = Number(options.failureThreshold || 2);
  const failureWindowMs = Number(options.failureWindowMs || 10_000);
  const now = options.now || Date.now;
  const logger = options.logger || console;
  const onRecover = options.onRecover || (() => undefined);
  const softwareRendering = options.softwareRendering === true;
  let failureTimes = [];
  let recoveryRequested = false;
  let disabled = false;

  return {
    disable() {
      disabled = true;
    },
    handle(details = {}) {
      if (details.type !== 'GPU') return false;
      const evidence = {
        reason: String(details.reason || 'unknown'),
        exitCode: Number.isInteger(details.exitCode) ? details.exitCode : null,
        serviceName: String(details.serviceName || ''),
      };
      logger.warn?.('[V8OS Desktop] GPU process exited', evidence);
      if (
        disabled
        || softwareRendering
        || recoveryRequested
        || !GPU_FAILURE_REASONS.has(evidence.reason)
      ) return false;

      const timestamp = Number(now());
      failureTimes = failureTimes.filter((entry) => timestamp - entry <= failureWindowMs);
      failureTimes.push(timestamp);
      if (failureTimes.length < failureThreshold) return false;

      recoveryRequested = true;
      logger.error?.('[V8OS Desktop] Repeated GPU failure; relaunching once with hardware acceleration disabled.', {
        ...evidence,
        failures: failureTimes.length,
        windowMs: failureWindowMs,
      });
      try {
        onRecover();
        return true;
      } catch (error) {
        logger.error?.('[V8OS Desktop] GPU recovery relaunch failed.', {
          reason: error instanceof Error ? error.message : String(error),
        });
        return false;
      }
    },
  };
}

module.exports = {
  SOFTWARE_RENDERING_ARG,
  createGpuRecoveryController,
  softwareRenderingRelaunchArgs,
  softwareRenderingRequested,
};
