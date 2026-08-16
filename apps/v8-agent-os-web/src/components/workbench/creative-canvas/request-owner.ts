export type CanvasRequestOwner = {
  sessionId: string;
  token: string;
};

export type CanvasSessionRequestCoordinator = {
  activateSession: (sessionId: string) => void;
  acquire: <T extends CanvasRequestOwner>(owner: T) => T | null;
  current: () => CanvasRequestOwner | null;
  isActive: (candidate: CanvasRequestOwner, mounted?: boolean) => boolean;
  release: (candidate: CanvasRequestOwner) => boolean;
};

type CanvasRuntimeProjection = {
  graphRunId?: unknown;
  status?: unknown;
  updatedAt?: unknown;
};

type CanvasRuntimeProjectionOptions = {
  allowExplicitActiveTransition?: boolean;
};

const ACTIVE_RUNTIME_STATUSES = new Set(["queued", "running"]);
const RETRYABLE_FAILED_RUNTIME_STATUSES = new Set(["failed", "recoverable_failed"]);
const FINAL_RUNTIME_STATUSES = new Set([
  "cancelled",
  "canceled",
  "completed",
  "interrupted",
  "succeeded",
]);

function normalizedRuntimeId(value: unknown) {
  return String(value || "").trim();
}

function normalizedRuntimeStatus(value: unknown) {
  return normalizedRuntimeId(value).toLowerCase();
}

function isNewerRuntimeProjection(current: CanvasRuntimeProjection, incoming: CanvasRuntimeProjection) {
  const currentTime = Date.parse(String(current.updatedAt || ""));
  const incomingTime = Date.parse(String(incoming.updatedAt || ""));
  return Number.isFinite(incomingTime) && Number.isFinite(currentTime) && incomingTime > currentTime;
}

export function sameCanvasRequestOwner(
  current: CanvasRequestOwner | null,
  candidate: CanvasRequestOwner,
) {
  return Boolean(
    current
    && current.sessionId === candidate.sessionId
    && current.token === candidate.token,
  );
}

export function isActiveCanvasRequestOwner(
  current: CanvasRequestOwner | null,
  candidate: CanvasRequestOwner,
  activeSessionId: string,
  mounted: boolean,
) {
  return mounted
    && activeSessionId === candidate.sessionId
    && sameCanvasRequestOwner(current, candidate);
}

export function isCurrentCanvasRuntimeEpoch(capturedEpoch: number, currentEpoch: number) {
  return capturedEpoch === currentEpoch;
}

export function createCanvasSessionRequestCoordinator(
  initialSessionId: string,
): CanvasSessionRequestCoordinator {
  let activeSessionId = initialSessionId;
  let owner: CanvasRequestOwner | null = null;

  return {
    activateSession(sessionId: string) {
      activeSessionId = sessionId;
      // A request from a previous visit to this Session must not become
      // current again when the user navigates back.
      owner = null;
    },
    acquire<T extends CanvasRequestOwner>(candidate: T) {
      if (candidate.sessionId !== activeSessionId || owner?.sessionId === candidate.sessionId) return null;
      owner = candidate;
      return candidate;
    },
    current() {
      return owner;
    },
    isActive(candidate: CanvasRequestOwner, mounted = true) {
      return isActiveCanvasRequestOwner(owner, candidate, activeSessionId, mounted);
    },
    release(candidate: CanvasRequestOwner) {
      if (!sameCanvasRequestOwner(owner, candidate)) return false;
      owner = null;
      return true;
    },
  };
}

export function reconcileCanvasRuntimeProjection<T extends CanvasRuntimeProjection>(
  current: T,
  incoming: T,
  options: CanvasRuntimeProjectionOptions = {},
) {
  const currentRunId = normalizedRuntimeId(current.graphRunId);
  const incomingRunId = normalizedRuntimeId(incoming.graphRunId);
  const currentStatus = normalizedRuntimeStatus(current.status);
  const incomingStatus = normalizedRuntimeStatus(incoming.status);
  const sameRun = Boolean(currentRunId) && currentRunId === incomingRunId;
  const newerProjection = isNewerRuntimeProjection(current, incoming);
  const incomingIsActive = ACTIVE_RUNTIME_STATUSES.has(incomingStatus);
  const incomingIsCancelling = incomingStatus === "cancelling";
  const currentIsRetryableFailure = RETRYABLE_FAILED_RUNTIME_STATUSES.has(currentStatus);
  const permitsFailedRetry = currentIsRetryableFailure
    && incomingIsActive
    && (Boolean(options.allowExplicitActiveTransition) || newerProjection);

  if (sameRun) {
    if (currentStatus === "cancelling" && incomingIsActive) return current;
    if (FINAL_RUNTIME_STATUSES.has(currentStatus) && (incomingIsActive || incomingIsCancelling)) return current;
    if (
      currentIsRetryableFailure
      && (incomingIsActive || incomingIsCancelling)
      && !permitsFailedRetry
    ) return current;
  }
  return incoming;
}
