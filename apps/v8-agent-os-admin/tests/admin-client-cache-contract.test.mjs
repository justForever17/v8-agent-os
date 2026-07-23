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

    assert.match(source, /if \(existing\?\.promise\) \{\s*return existing\.promise/);
    assert.match(source, /data: existing\?\.data/);
    assert.match(source, /data: current\.data/);
    assert.match(source, /publish\(key/);
});

test("admin routes prefetch both Next code and their data contracts", () => {
    const cacheSource = read("src", "lib", "admin-client-cache.ts");
    const sidebarSource = read("src", "components", "layout", "Sidebar.tsx");

    assert.match(cacheSource, /"\/admin\/model-hub": \[\["\/api\/model-hub\/bootstrap", 30_000\]\]/);
    assert.match(cacheSource, /"\/admin\/plugins":/);
    assert.match(sidebarSource, /router\.prefetch\(item\.href\)/);
    assert.match(sidebarSource, /prefetchAdminRouteData\(item\.href\)/);
    assert.doesNotMatch(sidebarSource, /prefetch=\{false\}/);
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
