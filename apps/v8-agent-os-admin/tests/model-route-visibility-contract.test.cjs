const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function readText(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("Model Hub exposes the provider model as the title and keeps the complete request route as secondary evidence", () => {
  const providerCard = readText("src/components/models/ProviderCard.tsx");
  const modelCard = readText("src/components/models/ModelCardV2.tsx");
  const modelAssets = readText("src/lib/models/model-assets.ts");
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(providerCard, /provider\.baseUrl \|\| provider\.code/);
  assert.match(modelCard, /resolveVisibleModelRoute/);
  assert.match(modelCard, /endpointBinding/);
  assert.match(modelCard, /const providerModelLabel =/);
  assert.match(modelCard, /modelId: providerModelLabel/);
  assert.match(modelCard, /components\.models\.ModelCardV2\.modelRoute/);
  assert.match(modelCard, /protocolVerificationWarning/);
  assert.match(modelCard, /providerHostedToolsEnabled/);
  assert.match(modelCard, /onToggleProviderHostedTools/);
  assert.match(modelCard, /visibleRoute\.wireProtocol === "openai\.responses"/);
  assert.match(modelAssets, /knownEndpointPrefix/);
  assert.match(modelAssets, /modelIdentityCandidates/);
  assert.match(modelAssets, /isEndpointQualifiedModelId/);
  assert.match(modelAssets, /if \(endpointQualified\) return null/);
  assert.doesNotMatch(modelCard, /model\.provider\?\.icon \|\| providerMark/);
  assert.match(modelCard, /<Brain className="h-5 w-5 text-muted-foreground"/);
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
  assert.match(projection, /endpointBinding: modelMeta\.endpointBinding/);
  assert.match(projection, /baseUrl: String\(providerMeta\.base_url \|\| providerMeta\.baseUrl/);
  assert.equal(zh["app.admin.dashboard.model.hub.catalog.modelRoute"], "请求路径：{route}");
  assert.equal(en["app.admin.dashboard.model.hub.catalog.modelRoute"], "Request route: {route}");
});

test("Manual and quick model setup share the canonical binding write surface", () => {
  const manualCreate = readText("src/app/api/models/route.ts");
  const manualUpdate = readText("src/app/api/models/[id]/route.ts");
  const quickConnect = readText("src/app/api/models/connect/route.ts");
  const form = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(manualCreate, /\/models\/bindings/);
  assert.match(manualUpdate, /\/models\/bindings/);
  assert.match(quickConnect, /\/models\/connect/);
  assert.match(form, /name="endpointPath"/);
  assert.match(form, /name="providerModelId"/);
  assert.match(form, /name="operationKind"/);
  assert.match(form, /name="wireProtocol"/);
  assert.match(form, /name="channelId"/);
  assert.match(form, /providerChannels/);
  assert.match(form, /customProviderChannels/);
  assert.match(form, /defaultChannelId/);
  assert.match(form, /openai\.responses/);
  assert.match(form, /handleToggleProviderHostedTools/);
  assert.match(form, /tools: \["web_search"\]/);
  assert.match(form, /source: "manual"/);
  const capabilityBlock = form.slice(
    form.indexOf("const CUSTOM_PROVIDER_CAPABILITIES"),
    form.indexOf("type AudioRuntimeConfig"),
  );
  assert.doesNotMatch(capabilityBlock, /id: "workflow"/);
  assert.match(form, /const catalogEndpointPath = String\(mediaLimits\.endpointPath \|\| mediaLimits\.requestPath/);
  assert.match(form, /const visibleRouteModelId = endpointPath && providerModelId/);
  assert.match(form, /modelId: visibleRouteModelId/);
  assert.match(form, /providerModelId,/);
  assert.match(form, /operationKind: operationKinds\[0\]/);
});

test("Quick connect exposes an explicit entry selector only for multi-channel providers", () => {
  const form = readText("src/app/admin/(dashboard)/model-hub/page.tsx");
  const zh = JSON.parse(readText("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(readText("src/i18n/locales/en.json"));

  assert.match(form, /selectedCatalogChannels\.length > 1/);
  assert.match(form, /if \(isLoading\) return;\s*if \(apiCatalogProviders\.some/);
  assert.match(form, /\[apiCatalogProviders, isLoading, selectedCatalogProviderId\]/);
  assert.match(form, /selectedCatalogProviderId, setSelectedCatalogProviderId\] = useState\(""\)/);
  assert.match(form, /data-testid="quick-connect-provider-trigger"/);
  assert.match(form, /data-testid="quick-connect-entry-trigger"/);
  assert.match(form, /Select value=\{selectedCatalogRuntime\.channelId\}/);
  assert.match(form, /setCatalogRuntimeProtocol\(value\)/);
  assert.match(form, /defaultChannelId: isCustomProvider \? customProviderDefaultChannelId : selectedCatalogRuntime\.channelId/);
  assert.match(form, /channelId: isCustomProvider \? selectedCustomChannel\?\.id \|\| "" : selectedCatalogRuntime\.channelId/);
  assert.match(form, /wireProtocol: isCustomProvider \? selectedCustomChannel\?\.defaultWireProtocol \|\| "" : selectedCatalogRuntime\.wireProtocol/);
  assert.equal(zh["app.admin.dashboard.model.hub.channel.catalogEntryTitle"], "API 入口");
  assert.equal(en["app.admin.dashboard.model.hub.channel.catalogEntryTitle"], "API entry");
});

test("Reasoning effort is persisted per model without growing the fixed model card", () => {
  const modelCard = readText("src/components/models/ModelCardV2.tsx");
  const projection = readText("src/lib/models/model-admin.ts");
  const form = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(modelCard, /h-\[128px\]/);
  assert.match(modelCard, /reasoningEffortControl/);
  assert.match(modelCard, /type="range"/);
  assert.match(modelCard, /aria-valuetext=/);
  assert.match(modelCard, /onSetReasoningLevel/);
  assert.match(projection, /payload\.reasoningEffortControl = data\.reasoningEffortControl/);
  assert.match(form, /handleSetReasoningLevel/);
  assert.match(form, /selectedLevel: disabled \? "auto" : level/);
});
