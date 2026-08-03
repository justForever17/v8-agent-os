export type AdminCacheOptions = {
  force?: boolean;
  ttlMs?: number;
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
};

type RoutePrefetchTarget = string | [string, number];

const DEFAULT_TTL_MS = 60_000;
const EMPTY_SNAPSHOT: AdminJsonSnapshot = Object.freeze({
  expiresAt: 0,
  updatedAt: 0,
  isFetching: false,
  error: null,
});
const cache = new Map<string, AdminCacheEntry>();
const listeners = new Map<string, Set<() => void>>();
let nextRequestId = 0;

const ROUTE_DATA_PREFETCH: Record<string, RoutePrefetchTarget[]> = {
  "/admin": [["/api/stats?days=7", 10_000]],
  "/admin/model-hub": [["/api/model-hub/bootstrap", 30_000]],
  "/admin/users": ["/api/client/devices"],
  "/admin/chat-runtime": [
    "/api/supervisor",
    "/api/models",
    "/api/mcp/tools",
    "/api/settings/vision-model",
  ],
  "/admin/subagents": [
    "/api/agents",
    "/api/models",
    "/api/extensions/catalog",
    "/api/config-registry/supervisor",
    "/api/agents/tool-surface",
  ],
  "/admin/memory": [
    ["/api/memory/dashboard", 15_000],
    "/api/memory/preferences",
    "/api/memory/knowledge",
    "/api/memory/knowledge?scope=global&status=quarantined&limit=100",
    "/api/memory/knowledge-resolution-candidates?limit=100",
    "/api/memory/knowledge-health",
    "/api/memory/artifacts?limit=160",
    "/api/storage-retention/stats",
  ],
  "/admin/automation": ["/api/hooks"],
  "/admin/desktop-automation": [
    "/api/config-registry/computer-use",
    "/api/models",
    "/api/runtime-capabilities",
    "/api/runtime-feature-packs",
  ],
  "/admin/rpa": [
    "/api/config-registry/rpa",
    "/api/models",
    "/api/runtime-capabilities",
    "/api/runtime-feature-packs",
    "/api/rpa/availability",
    "/api/rpa/drafts?includeArchived=false",
    "/api/rpa/scripts",
    "/api/rpa/templates?includeArchived=false",
    "/api/approvals?status=pending",
    "/api/runs?limit=20",
  ],
  "/admin/creative-media": [["/api/creative-media/bootstrap", 15_000]],
  "/admin/extensions": [
    "/api/extensions/health",
    "/api/config-registry/extensions",
    "/api/models",
    "/api/skills/safety/reviews?limit=100",
    ["/api/extensions/catalog", 15_000],
  ],
  "/admin/extensions/store": [
    "/api/extensions/store/skills?limit=30",
    "/api/extensions/store/mcp?limit=30",
  ],
  "/admin/research-runtime": [
    "/api/research-runtime?view=source-providers",
    "/api/research-runtime?view=ledger&scope=global&limit=30",
    "/api/runtime-capabilities",
  ],
  "/admin/network-supervisor-runtime": [
    "/api/config-registry/network-supervisor-runtime",
    "/api/network-supervisor/status",
    "/api/network-supervisor/peers",
    "/api/network-supervisor/openai/tokens",
    "/api/network-supervisor/neighbors/status",
    "/api/network-supervisor/neighbors/candidates",
    "/api/network-supervisor/neighbors/links",
    "/api/network-supervisor/neighbors/task-settings",
    "/api/network-supervisor/neighbors/tasks?limit=20",
  ],
  "/admin/plugins": [
    "/api/plugins/catalog",
    "/api/plugins/install-jobs",
    "/api/plugins/grants",
    "/api/plugins/events?limit=120",
  ],
  "/admin/runtime-governance": [
    "/api/runtime-capabilities",
    "/api/runs?limit=40",
    "/api/approvals?status=pending",
    "/api/conversations",
  ],
  "/admin/operations-center": [
    ["/api/operations-center/summary", 15_000],
  ],
  "/admin/projects-workspaces": [
    "/api/config-registry/projects",
    "/api/config-registry/workspace",
  ],
  "/admin/system-base": ["/api/config-registry/system-base"],
  "/admin/safety-control": [
    "/api/config-registry/safety",
    "/api/models",
    ["/api/safety/dashboard?limit=80", 10_000],
  ],
  "/admin/engineering-lane": [
    "/api/config-registry/engineering-lane",
    "/api/models",
    ["/api/model-cache/stats?days=7&limit=80", 10_000],
    ["/api/engineering-lane/proof-ledger?limit=30", 10_000],
    ["/api/engineering-lane/workset-observations?limit=40", 10_000],
    ["/api/memory/workflows?class=engineering&limit=8", 10_000],
  ],
  "/admin/desktop-pet": ["/api/config-registry/desktop-pet"],
};

function cacheKey(url: string) {
  return String(url || "").trim();
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error || "request_failed");
}

function publish(key: string, entry: AdminCacheEntry) {
  cache.set(key, entry);
  for (const listener of listeners.get(key) || []) listener();
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
  const now = Date.now();
  const existing = cache.get(key);

  if (!options.force && existing?.data !== undefined && existing.expiresAt > now) {
    return existing.data as T;
  }
  if (!options.force && existing?.promise) {
    return existing.promise as Promise<T>;
  }

  const requestId = ++nextRequestId;
  const request: Promise<T> = fetch(key, { cache: "no-store" })
    .then(async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
        throw new Error(String(record.detail || record.error || `HTTP ${response.status}`));
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
      const current = cache.get(key);
      if (current?.requestId === requestId) {
        publish(key, {
          data: current.data,
          expiresAt: current.expiresAt,
          updatedAt: current.updatedAt,
          isFetching: false,
          error: errorMessage(error),
        });
      }
      throw error;
    });

  publish(key, {
    data: existing?.data,
    expiresAt: existing?.expiresAt || 0,
    updatedAt: existing?.updatedAt || 0,
    isFetching: true,
    error: null,
    promise: request,
    requestId,
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
