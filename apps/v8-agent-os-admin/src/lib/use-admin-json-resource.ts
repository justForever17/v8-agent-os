"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  fetchAdminJson,
  getAdminJsonSnapshot,
  subscribeAdminJsonCache,
  type AdminCacheOptions,
} from "@/lib/admin-client-cache";

export function useAdminJsonResource<T>(url: string, options: Pick<AdminCacheOptions, "ttlMs"> = {}) {
  const ttlMs = options.ttlMs;
  const subscribe = useCallback(
    (listener: () => void) => subscribeAdminJsonCache(url, listener),
    [url],
  );
  const getSnapshot = useCallback(() => getAdminJsonSnapshot<T>(url), [url]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    void fetchAdminJson<T>(url, { ttlMs }).catch(() => undefined);
  }, [ttlMs, url]);

  useEffect(() => {
    const refreshIfStale = () => {
      const current = getAdminJsonSnapshot<T>(url);
      if (!current.isFetching && current.expiresAt <= Date.now()) {
        void fetchAdminJson<T>(url, { ttlMs }).catch(() => undefined);
      }
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") refreshIfStale();
    };
    window.addEventListener("focus", refreshIfStale);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.removeEventListener("focus", refreshIfStale);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [ttlMs, url]);

  const refresh = useCallback(
    () => fetchAdminJson<T>(url, { force: true, ttlMs }),
    [ttlMs, url],
  );

  return {
    ...snapshot,
    isLoading: snapshot.data === undefined,
    isRefreshing: snapshot.data !== undefined && snapshot.isFetching,
    refresh,
  };
}
