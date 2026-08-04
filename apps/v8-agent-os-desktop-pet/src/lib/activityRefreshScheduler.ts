export type ActivityRefreshScheduler = {
  schedule: () => void;
  stop: () => void;
};

export function createActivityRefreshScheduler(
  refresh: () => Promise<unknown> | unknown,
  options: { minimumIntervalMs?: number } = {},
): ActivityRefreshScheduler {
  const minimumIntervalMs = Math.max(0, options.minimumIntervalMs ?? 750);
  let stopped = false;
  let running = false;
  let pending = false;
  let lastStartedAt = Number.NEGATIVE_INFINITY;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    if (stopped || running || !pending || timer) return;
    const delay = Math.max(0, minimumIntervalMs - (Date.now() - lastStartedAt));
    if (delay > 0) {
      timer = setTimeout(() => {
        timer = null;
        flush();
      }, delay);
      return;
    }

    pending = false;
    running = true;
    lastStartedAt = Date.now();
    void Promise.resolve()
      .then(refresh)
      .catch(() => undefined)
      .finally(() => {
        running = false;
        if (pending) flush();
      });
  };

  return {
    schedule() {
      if (stopped) return;
      pending = true;
      flush();
    },
    stop() {
      stopped = true;
      pending = false;
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}
