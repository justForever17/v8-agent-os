import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function read(...segments) {
    return fs.readFileSync(path.join(adminRoot, ...segments), "utf8");
}

test("Windows folder picker hides its PowerShell host while keeping the STA dialog", () => {
    const source = read("src", "app", "api", "admin", "workspace", "folder-picker", "route.ts");

    assert.match(source, /New-Object System\.Windows\.Forms\.FolderBrowserDialog/);
    assert.match(source, /"powershell\.exe"/);
    assert.match(source, /"-STA"/);
    assert.match(source, /windowsHide: true/);
    assert.doesNotMatch(source, /windowsHide: false/);
});

test("Research Source Router fetches providers and config only after expansion", () => {
    const source = read("src", "admin", "components", "research", "ResearchSourceProviderPanel.tsx");

    assert.match(source, /const \[expanded, setExpanded\] = useState\(false\)/);
    assert.match(source, /const nextExpanded = !expanded;\s*setExpanded\(nextExpanded\);\s*if \(nextExpanded && !loaded && !loading\) \{\s*void load\(\);/);
    assert.match(source, /fetchAdminJson<SourceProvidersPayload>\(SOURCE_PROVIDERS_URL/);
    assert.match(source, /fetchConfigDomain<SystemBaseData>\("system-base"/);
    assert.doesNotMatch(source, /useEffect\(/);
});

test("chat runtime secondary tabs do not fan out subagent data on hover or focus", () => {
    const source = read("src", "app", "admin", "(dashboard)", "chat-runtime", "page.tsx");

    assert.doesNotMatch(source, /prefetchAdminRouteData|onPointerEnter|onFocus/);
    // The tab is now a deep link. Pinning the retired local setter would reject
    // the query/back fix while still failing to observe a real prefetch.
    assert.match(source, /href="\/admin\/chat-runtime\?tab=subagents"/);
    assert.match(source, /useSearchParams\(\)\.get\("tab"\)/);
    assert.match(source, /tab === "supervisor" \? <SupervisorPage\/> : <SubagentsPage\/>/);
});

test("runtime broker and contextual tool mode copy have bilingual labels", () => {
    const runtimeSource = read("src", "admin", "lib", "runtime-admin.ts");
    const en = JSON.parse(read("src", "admin", "i18n", "locales", "en.json"));
    const zh = JSON.parse(read("src", "admin", "i18n", "locales", "zh-CN.json"));

    assert.match(runtimeSource, /runtime_broker: "lib\.runtime\.admin\.runtimeBroker"/);
    assert.equal(en["lib.runtime.admin.runtimeBroker"], "Runtime orchestration");
    assert.equal(zh["lib.runtime.admin.runtimeBroker"], "运行模式调度");
    assert.match(en["app.admin.dashboard.subagents.page.k3b6c2a75"], /inherits the skills/);
    assert.match(zh["app.admin.dashboard.subagents.page.k3b6c2a75"], /继承 Supervisor/);
});
