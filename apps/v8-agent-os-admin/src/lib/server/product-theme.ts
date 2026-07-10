import { normalizeProductTheme, type UiTheme } from "@v8/product-ui/theme-bootstrap";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export type InitialProductTheme = {
  theme: UiTheme;
  syncState: "synced" | "degraded";
};

export async function resolveInitialProductTheme(): Promise<InitialProductTheme> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 500);
  try {
    const response = await fetch(`${resolveEngineBaseUrl()}/config-registry/ui`, {
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    const data = payload && typeof payload === "object" && payload.data && typeof payload.data === "object"
      ? payload.data as Record<string, unknown>
      : payload as Record<string, unknown>;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return { theme: normalizeProductTheme(data.theme), syncState: "synced" };
  } catch {
    return { theme: "system", syncState: "degraded" };
  } finally {
    clearTimeout(timeout);
  }
}
