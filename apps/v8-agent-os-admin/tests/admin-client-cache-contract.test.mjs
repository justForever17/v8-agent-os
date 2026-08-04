import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function read(...segments) {
    return fs.readFileSync(path.join(adminRoot, ...segments), "utf8");
}

test("admin JSON cache preserves stale data while a refresh is in flight", () => {
    const source = read("src", "lib", "admin-client-cache.ts");

    assert.match(source, /if \(!options\.force && existing\?\.promise\) \{\s*return existing\.promise/);
    assert.match(source, /const requestId = \+\+nextRequestId/);
    assert.match(source, /current\?\.requestId === requestId/);
    assert.match(source, /data: existing\?\.data/);
    assert.match(source, /data: current\.data/);
    assert.match(source, /publish\(key/);
});

test("admin sidebar navigation does not fan out data reads from hover or focus", () => {
    const cacheSource = read("src", "lib", "admin-client-cache.ts");
    const sidebarSource = read("src", "components", "layout", "Sidebar.tsx");

    assert.match(cacheSource, /"\/admin\/model-hub": \[\["\/api\/model-hub\/bootstrap", 30_000\]\]/);
    assert.match(cacheSource, /"\/admin\/plugins":/);
    assert.match(sidebarSource, /prefetch=\{false\}/);
    assert.doesNotMatch(sidebarSource, /prefetchAdminRouteData|router\.prefetch|scheduleRoutePrefetch/);
    assert.doesNotMatch(sidebarSource, /onPointerEnter|onPointerLeave|onPointerDown|onFocus|onBlur/);
});

test("network supervisor prefetch uses the same plural neighbor and token routes as the page", () => {
    const source = read("src", "lib", "admin-client-cache.ts");

    assert.match(source, /"\/api\/network-supervisor\/openai\/tokens"/);
    assert.match(source, /"\/api\/network-supervisor\/neighbors\/status"/);
    assert.match(source, /"\/api\/network-supervisor\/neighbors\/tasks\?limit=20"/);
    assert.doesNotMatch(source, /"\/api\/network-supervisor\/openai-compat\/tokens"/);
    assert.doesNotMatch(source, /"\/api\/network-supervisor\/neighbor\/status"/);
});

test("operations prefetch avoids storage scans until the user opens retention details", () => {
    const source = read("src", "lib", "admin-client-cache.ts");
    const operationsPrefetch = source.match(/"\/admin\/operations-center": \[([\s\S]*?)\n\s*\],/u)?.[1] || "";

    assert.match(operationsPrefetch, /"\/api\/operations-center\/summary"/);
    assert.doesNotMatch(operationsPrefetch, /storage-retention/);
});

test("chat runtime and subagents share the supervisor model binding without a duplicate default-model request", () => {
    const cacheSource = read("src", "lib", "admin-client-cache.ts");
    const subagentsSource = read("src", "app", "admin", "(dashboard)", "subagents", "page.tsx");

    const chatPrefetch = cacheSource.match(/"\/admin\/chat-runtime": \[([\s\S]*?)\n\s*\],/u)?.[1] || "";
    const subagentPrefetch = cacheSource.match(/"\/admin\/subagents": \[([\s\S]*?)\n\s*\],/u)?.[1] || "";
    assert.doesNotMatch(chatPrefetch, /default-agent-model/);
    assert.doesNotMatch(subagentPrefetch, /default-agent-model/);
    assert.doesNotMatch(subagentsSource, /fetchAdminJson<\{ modelId\?: string; modelRef\?: string \}>\("\/api\/settings\/default-agent-model"/);
    assert.match(subagentsSource, /bindings\?\.defaultReplyModel/);
});

test("operations center owns one runtime data hook and its summary route only reads health", () => {
    const pageSource = read("src", "app", "admin", "(dashboard)", "operations-center", "page.tsx");
    const approvalsSource = read("src", "components", "runtime", "PendingApprovalsPanel.tsx");
    const runsSource = read("src", "components", "runtime", "RecentRunsPanel.tsx");
    const summarySource = read("src", "app", "api", "operations-center", "summary", "route.ts");

    assert.equal((pageSource.match(/useRuntimeOpsData\(\)/g) || []).length, 1);
    assert.doesNotMatch(approvalsSource, /const defaultRuntime = useRuntimeOpsData\(\)/);
    assert.doesNotMatch(runsSource, /const defaultRuntime = useRuntimeOpsData\(\)/);
    assert.match(summarySource, /proxyEngineJson\("\/health"\)/);
    assert.doesNotMatch(summarySource, /proxyEngineJson\("\/approvals/);
    assert.doesNotMatch(summarySource, /proxyEngineJson\("\/runs/);
});

test("model hub paints cached bootstrap data instead of resetting to a loading screen", () => {
    const source = read("src", "app", "admin", "(dashboard)", "model-hub", "page.tsx");

    assert.match(source, /peekAdminJsonCache<ModelHubBootstrapPayload>/);
    assert.match(source, /useState\(\(\) => !cachedBootstrap\)/);
    assert.match(source, /if \(!peekAdminJsonCache\(MODEL_HUB_BOOTSTRAP_URL\)\) setIsLoading\(true\)/);
});

test("observable resources refresh on focus without discarding their cached snapshot", () => {
    const source = read("src", "lib", "use-admin-json-resource.ts");

    assert.match(source, /useSyncExternalStore/);
    assert.match(source, /window\.addEventListener\("focus"/);
    assert.match(source, /document\.addEventListener\("visibilitychange"/);
});

test("chat runtime paints its page shell before supervisor configuration finishes", () => {
    const source = read("src", "app", "admin", "(dashboard)", "supervisor", "page.tsx");
    const headingIndex = source.indexOf('t("app.admin.dashboard.supervisor.page.kf45c6152")');
    const loadingRegionIndex = source.indexOf('{isLoading ? <div className="grid');

    assert.ok(headingIndex >= 0 && loadingRegionIndex > headingIndex);
    assert.match(source, /aria-busy="true"/);
    assert.doesNotMatch(source, /if \(isLoading\) \{\s*return/);
});

test("topbar defers inbox work and loads feature packs only on demand", () => {
    const source = read("src", "components", "layout", "Topbar.tsx");
    const inboxRoute = read("src", "app", "api", "admin-inbox", "route.ts");
    const healthCache = read("src", "lib", "server", "engine-health-snapshot.ts");

    assert.match(source, /setTimeout\(\(\) => void loadInbox\(true\), 1200\)/);
    assert.match(source, /fetchAdminJson<InboxPayload>\(url/);
    assert.match(source, /ttlMs: 10_000/);
    assert.match(source, /loadInbox\(true, true\)/);
    assert.match(source, /loadInbox\(true, true, true\)/);
    assert.match(source, /loadInbox\(false, true, true\)/);
    assert.match(source, /loadInbox\(false\)/);
    assert.match(source, /inboxLoadingRef\.current/);
    assert.match(source, /pendingInboxForceRef\.current = true/);
    assert.match(source, /if \(!pendingInboxForceRef\.current\) break/);
    assert.match(source, /requestRefreshHealth = true/);
    assert.match(source, /force: requestForce/);
    assert.match(source, /inboxRefreshAttempt/);
    assert.match(source, /primeAdminJsonCache\("\/api\/admin-inbox"/);
    assert.doesNotMatch(source, /void loadInstallState\(false, true\);/);
    assert.match(source, /if \(opening\) \{\s*void loadInstallState/);
    assert.match(source, /loadInstallState\(true, true, healthRefreshing \|\| packInstalling\)/);
    assert.match(inboxRoute, /readEngineHealthSnapshot\(\{ force: refreshHealth, waitForFresh: refreshHealth \}\)/);
    assert.match(inboxRoute, /retryAfterMs: healthResult\.refreshing \? 1_500 : null/);
    assert.match(healthCache, /HEALTH_FRESH_TTL_MS = 5_000/);
    assert.match(healthCache, /if \(options\.force && !pending\)/);
    assert.doesNotMatch(healthCache, /options\.force \|\| \(!hasFreshData/);
    assert.match(healthCache, /if \(existingRequest\) return existingRequest/);
    assert.match(healthCache, /Promise\.resolve\(\)\.then/);
    assert.match(healthCache, /healthRequests\.get\(origin\) === request/);
    assert.match(healthCache, /healthCaches = new Map/);
    assert.match(healthCache, /waitForFresh/);
    assert.match(healthCache, /HEALTH_REQUEST_TIMEOUT_MS = 4_000/);
});
