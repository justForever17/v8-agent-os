/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function readText(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("Model Hub exposes provider base URL and catalog-declared media route without hiding the provider model ID", () => {
  const providerCard = readText("src/components/models/ProviderCard.tsx");
  const modelCard = readText("src/components/models/ModelCardV2.tsx");
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(providerCard, /provider\.baseUrl \|\| provider\.code/);
  assert.match(modelCard, /resolveVisibleModelRoute/);
  assert.match(modelCard, /Request suffix:/);
  assert.match(modelCard, /components\.models\.ModelCardV2\.providerModelId/);
  assert.match(modelCard, /components\.models\.ModelCardV2\.routeFromCatalog/);
  assert.match(hub, /requestPath: relativePath/);
  assert.match(hub, /routeSource: "provider_catalog"/);
  assert.match(hub, /const visibleModelPath = hasExplicitRoute/);
  assert.doesNotMatch(hub, /const visibleModelPath = hasExplicitRoute \? `\/\$\{modelId/);
  assert.match(hub, /handleConnectCatalogModel\(probedCatalogProviderId \|\| selectedCatalogProviderId, selectedCatalogModelId/);
});

test("Admin model projection preserves media route evidence for the visible model card", () => {
  const projection = readText("src/lib/models/model-admin.ts");
  const zh = JSON.parse(readText("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(readText("src/i18n/locales/en.json"));

  assert.match(projection, /mediaLimits: modelMeta\.mediaLimits/);
  assert.match(projection, /baseUrl: String\(providerMeta\.base_url \|\| providerMeta\.baseUrl/);
  assert.equal(zh["app.admin.dashboard.model.hub.catalog.routeFromCatalog"], "目录声明路径");
  assert.equal(en["app.admin.dashboard.model.hub.catalog.routeFromCatalog"], "Catalog-declared route");
});
