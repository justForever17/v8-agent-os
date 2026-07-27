export type UiTheme = "light" | "dark" | "system";

export const PRODUCT_THEME_STORAGE_KEY = "v8-product-theme";
export const PRODUCT_THEME_COOKIE_KEY = "v8-product-theme";

const PRODUCT_THEME_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export function normalizeProductTheme(value: unknown): UiTheme {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "light" || normalized === "dark" || normalized === "system"
    ? normalized
    : "system";
}

export function buildProductThemeBootstrapScript(
  theme: UiTheme,
  storageKey = PRODUCT_THEME_STORAGE_KEY,
  authoritative = true,
) {
  const normalizedTheme = normalizeProductTheme(theme);
  return `(() => { try { const fallbackTheme = ${JSON.stringify(normalizedTheme)}; const storageKey = ${JSON.stringify(storageKey)}; const authoritative = ${JSON.stringify(authoritative)}; const storedTheme = localStorage.getItem(storageKey); const storedIsValid = storedTheme === "light" || storedTheme === "dark" || storedTheme === "system"; const theme = authoritative || !storedIsValid ? fallbackTheme : storedTheme; const root = document.documentElement; if (authoritative || !storedIsValid) localStorage.setItem(storageKey, theme); if (authoritative) document.cookie = ${JSON.stringify(`${PRODUCT_THEME_COOKIE_KEY}=`)} + encodeURIComponent(theme) + ${JSON.stringify(`; Path=/; Max-Age=${PRODUCT_THEME_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`)}; const resolved = theme === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : theme; root.classList.remove("light", "dark"); root.classList.add(resolved); root.style.colorScheme = resolved; } catch (_) {} })();`;
}
