export type AdminCacheOptions = {
  force?: boolean;
  ttlMs?: number;
  timeoutMs?: number;
};

export type AdminJsonSnapshot<T = unknown> = {
  data?: T;
  expiresAt: number;
  updatedAt: number;
  isFetching: boolean;
  error: string | null;
};

type AdminCacheEntry = AdminJsonSnapshot & {
  promise?: Promise<unknown>;
  requestId?: number;
  controller?: AbortController;
};

type AdminEngineOriginBrowser = {
  dispatchEvent: (event: Event) => boolean;
  location: { reload: () => void };
};

type AdminEngineOriginChangeOptions = {
  browser?: AdminEngineOriginBrowser;
  reload?: boolean;
};

type RoutePrefetchTarget = string | [string, number];

const DEFAULT_TTL_MS = 60_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const EMPTY_SNAPSHOT: AdminJsonSnapshot = Object.freeze({
  expiresAt: 0,
  updatedAt: 0,
  isFetching: false,
  error: null,
});
const cache = new Map<string, AdminCacheEntry>();
const listeners = new Map<string, Set<() => void>>();
let nextRequestId = 0;
let cacheGeneration = 0;
let observedEngineOrigin = "";
let engineOriginReloadRequested = false;

const DEFAULT_ENGINE_BASE_URL = "http://127.0.0.1:9530/v1";
export const ADMIN_ENGINE_ORIGIN_CHANGED_EVENT = "v8os:admin-engine-origin-changed";

export class AdminEngineOriginChangedError extends Error {
  readonly code = "admin_engine_origin_changed";

  constructor() {
    super("Admin Engine origin changed while the request was in flight.");
    this.name = "AdminEngineOriginChangedError";
  }
}

const ROUTE_DATA_PREFETCH: Record<string, RoutePrefetchTarget[]> = {
  "/admin": [["/api/admin/stats?days=7", 10_000]],
  "/admin/model-hub": [["/api/admin/model-hub/bootstrap", 30_000]],
  "/admin/users": ["/api/admin/client/devices"],
  "/admin/chat-runtime": [
    "/api/admin/supervisor",
    "/api/admin/models",
    "/api/admin/mcp/tools",
    "/api/admin/settings/vision-model",
  ],
  "/admin/subagents": [
    "/api/admin/agents",
    "/api/admin/models",
    "/api/admin/extensions/catalog",
    "/api/admin/config-registry/supervisor",
    "/api/admin/agents/tool-surface",
  ],
  "/admin/memory": [
    ["/api/admin/memory/dashboard", 15_000],
    "/api/admin/memory/preferences",
    "/api/admin/memory/knowledge",
    "/api/admin/memory/knowledge?scope=global&status=quarantined&limit=100",
    "/api/admin/memory/knowledge-resolution-candidates?limit=100",
    "/api/admin/memory/knowledge-health",
    "/api/admin/storage-retention/stats",
  ],
  "/admin/automation": ["/api/admin/hooks"],
  "/admin/desktop-automation": [
    "/api/admin/config-registry/computer-use",
    "/api/admin/models",
    "/api/admin/runtime-capabilities",
    "/api/admin/runtime-feature-packs",
  ],
  "/admin/rpa": [
    "/api/admin/config-registry/rpa",
    "/api/admin/models",
    "/api/admin/runtime-capabilities",
    "/api/admin/runtime-feature-packs",
    "/api/admin/rpa/availability",
    "/api/admin/rpa/drafts?includeArchived=false",
    "/api/admin/rpa/scripts",
    "/api/admin/rpa/templates?includeArchived=false",
    "/api/admin/approvals?status=pending",
    "/api/admin/runs?limit=20",
  ],
  "/admin/creative-media": [["/api/admin/creative-media/bootstrap", 15_000]],
  "/admin/extensions": [
    "/api/admin/extensions/health",
    "/api/admin/config-registry/extensions",
    "/api/admin/skills/safety/reviews?limit=100",
    ["/api/admin/extensions/catalog", 15_000],
  ],
  // The store resolves the user's selected source and tab before fetching.
  "/admin/extensions/store": [],
  "/admin/research-runtime": [
    "/api/admin/research-runtime?view=source-providers",
    "/api/admin/research-runtime?view=ledger&scope=global&limit=30",
    "/api/admin/runtime-capabilities",
  ],
  "/admin/network-supervisor-runtime": [
    "/api/admin/config-registry/network-supervisor-runtime",
    "/api/admin/network-supervisor/status",
    "/api/admin/network-supervisor/peers",
    "/api/admin/network-supervisor/openai/tokens",
    "/api/admin/network-supervisor/neighbors/status",
    "/api/admin/network-supervisor/neighbors/candidates",
    "/api/admin/network-supervisor/neighbors/links",
    "/api/admin/network-supervisor/neighbors/task-settings",
    "/api/admin/network-supervisor/neighbors/tasks?limit=20",
  ],
  "/admin/plugins": [
    "/api/admin/plugins/catalog",
    "/api/admin/plugins/install-jobs",
    "/api/admin/plugins/grants",
    "/api/admin/plugins/events?limit=120",
  ],
  "/admin/runtime-governance": [
    "/api/admin/runtime-capabilities",
    "/api/admin/runs?limit=40",
    "/api/admin/approvals?status=pending",
    "/api/admin/conversations",
  ],
  "/admin/operations-center": [
    ["/api/admin/operations-center/summary", 15_000],
  ],
  "/admin/projects-workspaces": [
    "/api/admin/config-registry/projects",
    "/api/admin/config-registry/workspace",
  ],
  "/admin/system-base": ["/api/admin/config-registry/system-base"],
  "/admin/safety-control": [
    "/api/admin/config-registry/safety",
    "/api/admin/models",
    ["/api/admin/safety/dashboard?limit=80", 10_000],
  ],
  "/admin/engineering-lane": [
    "/api/admin/config-registry/engineering-lane",
    "/api/admin/models",
    ["/api/admin/model-cache/stats?days=7&limit=80", 10_000],
    ["/api/admin/engineering-lane/proof-ledger?limit=30", 10_000],
    ["/api/admin/engineering-lane/workset-observations?limit=40", 10_000],
    ["/api/admin/memory/workflows?class=engineering&limit=8", 10_000],
  ],
  "/admin/desktop-pet": ["/api/admin/config-registry/desktop-pet"],
};

function cacheKey(url: string) {
  return String(url || "").trim();
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error || "request_failed");
}

function assertCurrentGeneration(generation: number) {
  if (generation !== cacheGeneration) {
    throw new AdminEngineOriginChangedError();
  }
}

function notifySubscribers(key: string) {
  for (const listener of listeners.get(key) || []) listener();
}

function publish(key: string, entry: AdminCacheEntry) {
  cache.set(key, entry);
  notifySubscribers(key);
}

function clearAdminJsonCacheForEngineOriginChange() {
  cacheGeneration += 1;
  const invalidatedKeys = new Set([...cache.keys(), ...listeners.keys()]);
  const controllers = [...cache.values()].flatMap((entry) => entry.controller ? [entry.controller] : []);
  cache.clear();
  for (const controller of controllers) controller.abort();
  for (const key of invalidatedKeys) notifySubscribers(key);
}

export function normalizeAdminEngineBaseUrl(value: unknown) {
  const candidate = String(value || "").trim() || DEFAULT_ENGINE_BASE_URL;
  try {
    const parsed = new URL(candidate);
    const pathname = parsed.pathname.replace(/\/+$/, "");
    return `${parsed.protocol.toLowerCase()}//${parsed.host.toLowerCase()}${pathname}`;
  } catch {
    return candidate.replace(/\/+$/, "");
  }
}

export function applyAdminEngineOriginChange(
  previousValue: unknown,
  nextValue: unknown,
  options: AdminEngineOriginChangeOptions = {},
) {
  const previousOrigin = normalizeAdminEngineBaseUrl(previousValue);
  const nextOrigin = normalizeAdminEngineBaseUrl(nextValue);
  if (previousOrigin === nextOrigin) {
    observedEngineOrigin = nextOrigin;
    return false;
  }
  if (observedEngineOrigin === nextOrigin) return false;

  observedEngineOrigin = nextOrigin;
  clearAdminJsonCacheForEngineOriginChange();

  const browser = options.browser
    ?? (typeof window === "undefined" ? undefined : window);
  if (!browser || (options.reload && engineOriginReloadRequested)) return true;

  browser.dispatchEvent(new Event(ADMIN_ENGINE_ORIGIN_CHANGED_EVENT));
  if (options.reload) {
    engineOriginReloadRequested = true;
    browser.location.reload();
  }
  return true;
}

export function getAdminJsonSnapshot<T>(url: string): AdminJsonSnapshot<T> {
  return (cache.get(cacheKey(url)) || EMPTY_SNAPSHOT) as AdminJsonSnapshot<T>;
}

export function subscribeAdminJsonCache(url: string, listener: () => void) {
  const key = cacheKey(url);
  const subscribers = listeners.get(key) || new Set<() => void>();
  subscribers.add(listener);
  listeners.set(key, subscribers);
  return () => {
    subscribers.delete(listener);
    if (subscribers.size === 0) listeners.delete(key);
  };
}

export function peekAdminJsonCache<T>(url: string): T | undefined {
  return cache.get(cacheKey(url))?.data as T | undefined;
}

export async function fetchAdminJson<T>(url: string, options: AdminCacheOptions = {}): Promise<T> {
  const key = cacheKey(url);
  const ttlMs = Math.max(0, options.ttlMs ?? DEFAULT_TTL_MS);
  const timeoutMs = Math.max(1, options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS);
  const now = Date.now();
  const existing = cache.get(key);

  if (!options.force && existing?.data !== undefined && existing.expiresAt > now) {
    return existing.data as T;
  }
  if (!options.force && existing?.promise) {
    return existing.promise as Promise<T>;
  }

  const requestId = ++nextRequestId;
  const requestGeneration = cacheGeneration;
  const controller = new AbortController();
  let requestTimedOut = false;
  const timeout = setTimeout(() => {
    requestTimedOut = true;
    controller.abort();
  }, timeoutMs);
  const request: Promise<T> = fetch(key, { cache: "no-store", signal: controller.signal })
    .then(async (response) => {
      assertCurrentGeneration(requestGeneration);
      const payload = await response.json().catch(() => ({}));
      assertCurrentGeneration(requestGeneration);
      if (!response.ok) {
        const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
        const message = [record.detail, record.error, record.message]
          .map((value) => typeof value === "string" ? value
            : value && typeof value === "object" && "message" in value ? value.message : undefined)
          .find((value): value is string => typeof value === "string" && Boolean(value.trim()));
        throw new Error(message?.trim() || `HTTP ${response.status}`);
      }
      const current = cache.get(key);
      if (current?.requestId === requestId) {
        publish(key, {
          data: payload,
          expiresAt: Date.now() + ttlMs,
          updatedAt: Date.now(),
          isFetching: false,
          error: null,
        });
      }
      return payload as T;
    })
    .catch((error) => {
      assertCurrentGeneration(requestGeneration);
      const requestError = requestTimedOut
        ? new Error("admin_request_timeout")
        : error;
      const current = cache.get(key);
      if (current?.requestId === requestId) {
        publish(key, {
          data: current.data,
          expiresAt: current.expiresAt,
          updatedAt: current.updatedAt,
          isFetching: false,
          error: errorMessage(requestError),
        });
      }
      throw requestError;
    })
    .finally(() => clearTimeout(timeout));

  publish(key, {
    data: existing?.data,
    expiresAt: existing?.expiresAt || 0,
    updatedAt: existing?.updatedAt || 0,
    isFetching: true,
    error: null,
    promise: request,
    requestId,
    controller,
  });
  return request;
}

export function primeAdminJsonCache(url: string, data: unknown, ttlMs = DEFAULT_TTL_MS) {
  const key = cacheKey(url);
  publish(key, {
    data,
    expiresAt: Date.now() + Math.max(0, ttlMs),
    updatedAt: Date.now(),
    isFetching: false,
    error: null,
  });
}

export function invalidateAdminJsonCache(urlPrefix?: string) {
  const prefix = String(urlPrefix || "").trim();
  for (const [key, entry] of cache.entries()) {
    if (!prefix || key.startsWith(prefix)) {
      publish(key, { ...entry, expiresAt: 0 });
    }
  }
}

export async function prefetchAdminRouteData(href: string) {
  const route = href.split("?")[0];
  const targets = ROUTE_DATA_PREFETCH[route] || [];
  await Promise.allSettled(targets.map((target) => {
    const [url, ttlMs] = Array.isArray(target) ? target : [target, DEFAULT_TTL_MS];
    return fetchAdminJson(url, { ttlMs });
  }));
}
