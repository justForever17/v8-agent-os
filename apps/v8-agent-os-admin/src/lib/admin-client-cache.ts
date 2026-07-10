type AdminCacheOptions = {
  force?: boolean;
  ttlMs?: number;
};

type AdminCacheEntry = {
  data?: unknown;
  expiresAt: number;
  promise?: Promise<unknown>;
};

const DEFAULT_TTL_MS = 15_000;
const cache = new Map<string, AdminCacheEntry>();

const ROUTE_DATA_PREFETCH: Record<string, Array<string | [string, number]>> = {
  "/admin": [["/api/stats?days=7", 10_000]],
  "/admin/model-hub": [["/api/model-hub/bootstrap", 30_000]],
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
  ],
  "/admin/creative-media": [["/api/creative-media/bootstrap", 15_000]],
  "/admin/extensions": [
    "/api/extensions/health",
    "/api/config-registry/extensions",
    "/api/models",
    "/api/skills/safety/reviews?limit=100",
    ["/api/extensions/catalog", 15_000],
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
};

function cacheKey(url: string) {
  return String(url || "").trim();
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

  const request = fetch(key, { cache: "no-store" })
    .then(async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
        throw new Error(String(record.detail || record.error || `HTTP ${response.status}`));
      }
      cache.set(key, { data: payload, expiresAt: Date.now() + ttlMs });
      return payload as T;
    })
    .finally(() => {
      const current = cache.get(key);
      if (current?.promise === request) {
        if (current.data === undefined) cache.delete(key);
        else cache.set(key, { data: current.data, expiresAt: current.expiresAt });
      }
    });

  cache.set(key, {
    data: options.force ? undefined : existing?.data,
    expiresAt: options.force ? 0 : existing?.expiresAt || 0,
    promise: request,
  });
  return request;
}

export function primeAdminJsonCache(url: string, data: unknown, ttlMs = DEFAULT_TTL_MS) {
  cache.set(cacheKey(url), {
    data,
    expiresAt: Date.now() + Math.max(0, ttlMs),
  });
}

export function invalidateAdminJsonCache(urlPrefix?: string) {
  const prefix = String(urlPrefix || "").trim();
  if (!prefix) {
    cache.clear();
    return;
  }
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}

export async function prefetchAdminRouteData(href: string) {
  const targets = ROUTE_DATA_PREFETCH[href] || [];
  await Promise.allSettled(targets.map((target) => {
    const [url, ttlMs] = Array.isArray(target) ? target : [target, DEFAULT_TTL_MS];
    return fetchAdminJson(url, { ttlMs });
  }));
}
