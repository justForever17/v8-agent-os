import assert from "node:assert/strict";
import test from "node:test";

import {
  PRODUCT_THEME_STORAGE_KEY,
  buildProductThemeBootstrapScript,
  normalizeProductTheme,
} from "../dist/product-theme-bootstrap.js";

function runBootstrap(theme, prefersDark = false, { authoritative = true, storedTheme } = {}) {
  const classes = new Set(["stale-theme"]);
  const stored = new Map();
  const document = {
    documentElement: {
      classList: {
        add: (...values) => values.forEach((value) => classes.add(value)),
        remove: (...values) => values.forEach((value) => classes.delete(value)),
      },
      style: {},
    },
  };
  const localStorage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };
  if (storedTheme) stored.set(PRODUCT_THEME_STORAGE_KEY, storedTheme);
  const window = {
    matchMedia: () => ({ matches: prefersDark }),
  };
  const script = buildProductThemeBootstrapScript(theme, PRODUCT_THEME_STORAGE_KEY, authoritative);
  Function("document", "localStorage", "window", script)(document, localStorage, window);
  return { classes, document, stored };
}

test("normalizes unsupported theme values to system", () => {
  assert.equal(normalizeProductTheme("unsupported"), "system");
});

test("applies canonical dark theme before hydration", () => {
  const result = runBootstrap("dark");
  assert.equal(result.stored.get(PRODUCT_THEME_STORAGE_KEY), "dark");
  assert.equal(result.classes.has("dark"), true);
  assert.equal(result.classes.has("light"), false);
  assert.equal(result.document.documentElement.style.colorScheme, "dark");
});

test("resolves system theme from the pre-paint media query", () => {
  const result = runBootstrap("system", true);
  assert.equal(result.classes.has("dark"), true);
  assert.equal(result.document.documentElement.style.colorScheme, "dark");
});

test("a degraded server fallback preserves the last locally selected theme", () => {
  const result = runBootstrap("system", false, { authoritative: false, storedTheme: "dark" });
  assert.equal(result.stored.get(PRODUCT_THEME_STORAGE_KEY), "dark");
  assert.equal(result.classes.has("dark"), true);
  assert.equal(result.document.documentElement.style.colorScheme, "dark");
});
