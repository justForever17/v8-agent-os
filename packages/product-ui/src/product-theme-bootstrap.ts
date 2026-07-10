export type UiTheme = "light" | "dark" | "system";

export const PRODUCT_THEME_STORAGE_KEY = "v8-product-theme";

export function normalizeProductTheme(value: unknown): UiTheme {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "light" || normalized === "dark" || normalized === "system"
    ? normalized
    : "system";
}

export function buildProductThemeBootstrapScript(
  theme: UiTheme,
  storageKey = PRODUCT_THEME_STORAGE_KEY,
) {
  const normalizedTheme = normalizeProductTheme(theme);
  return `(() => { try { const theme = ${JSON.stringify(normalizedTheme)}; const storageKey = ${JSON.stringify(storageKey)}; const root = document.documentElement; localStorage.setItem(storageKey, theme); const resolved = theme === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : theme; root.classList.remove("light", "dark"); root.classList.add(resolved); root.style.colorScheme = resolved; } catch (_) {} })();`;
}
