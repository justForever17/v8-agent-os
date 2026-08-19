"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  fetchAdminJson,
  getAdminJsonSnapshot,
  subscribeAdminJsonCache,
  type AdminCacheOptions,
} from "@/lib/admin-client-cache";

export function useAdminJsonResource<T>(url: string, options: Pick<AdminCacheOptions, "ttlMs" | "timeoutMs"> = {}) {
  const ttlMs = options.ttlMs;
  const timeoutMs = options.timeoutMs;
  const subscribe = useCallback(
    (listener: () => void) => subscribeAdminJsonCache(url, listener),
    [url],
  );
  const getSnapshot = useCallback(() => getAdminJsonSnapshot<T>(url), [url]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    void fetchAdminJson<T>(url, { ttlMs, timeoutMs }).catch(() => undefined);
  }, [timeoutMs, ttlMs, url]);

  useEffect(() => {
    const refreshIfStale = () => {
      const current = getAdminJsonSnapshot<T>(url);
      if (!current.isFetching && current.expiresAt <= Date.now()) {
        void fetchAdminJson<T>(url, { ttlMs, timeoutMs }).catch(() => undefined);
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
  }, [timeoutMs, ttlMs, url]);

  const refresh = useCallback(
    () => fetchAdminJson<T>(url, { force: true, ttlMs, timeoutMs }),
    [timeoutMs, ttlMs, url],
  );

  return {
    ...snapshot,
    isLoading: snapshot.data === undefined && snapshot.error === null,
    isRefreshing: snapshot.data !== undefined && snapshot.isFetching,
    refresh,
  };
}
