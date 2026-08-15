export type CanvasRequestOwner = {
  sessionId: string;
  token: string;
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
