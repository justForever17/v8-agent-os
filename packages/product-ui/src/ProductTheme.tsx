"use client";

import {
  createContext,
  type ComponentProps,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes";
import { normalizeProductTheme, type UiTheme } from "./product-theme-bootstrap.js";

export type { UiTheme } from "./product-theme-bootstrap.js";
export type ProductThemeSyncState = "idle" | "syncing" | "synced" | "degraded";

export type ProductThemeState = {
  theme: UiTheme;
  resolvedTheme: "light" | "dark";
  syncState: ProductThemeSyncState;
};

export type ProductThemeContextValue = ProductThemeState & {
  setTheme: (theme: UiTheme) => Promise<void>;
  refreshTheme: () => Promise<void>;
};

export type ProductThemeProviderProps = ComponentProps<typeof NextThemesProvider> & {
  canonicalTheme?: UiTheme;
  endpoint?: string;
  initialSyncState?: ProductThemeSyncState;
};

const ProductThemeContext = createContext<ProductThemeContextValue | null>(null);

function ProductThemeSync({
  children,
  endpoint,
  initialSyncState,
  initialTheme,
}: {
  children: ReactNode;
  endpoint: string;
  initialSyncState: ProductThemeSyncState;
  initialTheme: UiTheme;
}) {
  const { theme: currentTheme, resolvedTheme: currentResolvedTheme, setTheme: setNextTheme } = useTheme();
  const [syncState, setSyncState] = useState<ProductThemeSyncState>(initialSyncState);
  const requestSequence = useRef(0);
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refreshTheme = useCallback(() => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const requestId = ++requestSequence.current;
    setSyncState("syncing");
    const request = (async () => {
      try {
        const response = await fetch(endpoint, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        if (response.status === 401 || response.status === 403) {
          if (requestId === requestSequence.current) setSyncState("idle");
          return;
        }
        if (!response.ok) throw new Error(String(payload?.error || `HTTP ${response.status}`));
        if (requestId !== requestSequence.current) return;
        setNextTheme(normalizeProductTheme(payload?.theme));
        setSyncState("synced");
      } catch (error) {
        if (requestId !== requestSequence.current) return;
        console.warn("[ProductTheme] Canonical theme read failed", error);
        setSyncState("degraded");
      }
    })().finally(() => {
      if (refreshInFlight.current === request) refreshInFlight.current = null;
    });
    refreshInFlight.current = request;
    return request;
  }, [endpoint, setNextTheme]);

  const setTheme = useCallback(async (nextTheme: UiTheme) => {
    const normalized = normalizeProductTheme(nextTheme);
    const requestId = ++requestSequence.current;
    setNextTheme(normalized);
    setSyncState("syncing");
    try {
      const response = await fetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: normalized }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.error || `HTTP ${response.status}`));
      if (requestId !== requestSequence.current) return;
      setNextTheme(normalizeProductTheme(payload?.theme));
      setSyncState("synced");
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      console.warn("[ProductTheme] Canonical theme write failed", error);
      setSyncState("degraded");
    }
  }, [endpoint, setNextTheme]);

  useEffect(() => {
    setNextTheme(initialTheme);
    if (initialSyncState !== "synced") void refreshTheme();
    const handleFocus = () => void refreshTheme();
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void refreshTheme();
    };
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      requestSequence.current += 1;
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [initialSyncState, initialTheme, refreshTheme, setNextTheme]);

  const value = useMemo<ProductThemeContextValue>(() => ({
    theme: normalizeProductTheme(currentTheme),
    resolvedTheme: currentResolvedTheme === "dark" ? "dark" : "light",
    syncState,
    setTheme,
    refreshTheme,
  }), [currentResolvedTheme, currentTheme, refreshTheme, setTheme, syncState]);

  return <ProductThemeContext.Provider value={value}>{children}</ProductThemeContext.Provider>;
}

export function ProductThemeProvider({
  canonicalTheme,
  children,
  defaultTheme,
  endpoint = "/api/ui-preferences/theme",
  initialSyncState = "idle",
  ...props
}: ProductThemeProviderProps) {
  const initialTheme = normalizeProductTheme(canonicalTheme ?? defaultTheme);
  return (
    <NextThemesProvider {...props} defaultTheme={initialTheme}>
      <ProductThemeSync endpoint={endpoint} initialSyncState={initialSyncState} initialTheme={initialTheme}>
        {children}
      </ProductThemeSync>
    </NextThemesProvider>
  );
}

export function useProductTheme() {
  const context = useContext(ProductThemeContext);
  if (!context) throw new Error("useProductTheme must be used within ProductThemeProvider");
  return context;
}
