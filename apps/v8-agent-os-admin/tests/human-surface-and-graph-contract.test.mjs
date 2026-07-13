import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const adminRoot = path.resolve(import.meta.dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(adminRoot, relativePath), "utf8");

test("primary runtime cards keep exact identifiers behind technical details", () => {
    const recentRuns = read("src/components/runtime/RecentRunsPanel.tsx");
    const approvals = read("src/components/runtime/PendingApprovalsPanel.tsx");
    const safety = read("src/app/admin/(dashboard)/safety-control/page.tsx");
    const engineering = read("src/app/admin/(dashboard)/engineering-lane/page.tsx");
    const artifacts = read("src/components/memory/ArtifactExplorerPanel.tsx");
    const network = read("src/components/network-supervisor/NetworkSupervisorRuntimeWorkbench.tsx");

    assert.match(recentRuns, /TechnicalReferenceDetails/);
    assert.doesNotMatch(recentRuns, />Run ID:/);
    assert.match(approvals, /TechnicalReferenceDetails/);
    assert.doesNotMatch(approvals, />Run \{/);
    assert.match(safety, /TechnicalReferenceDetails/);
    assert.doesNotMatch(safety, />Run \{/);
    assert.match(engineering, /TechnicalReferenceDetails/);
    assert.doesNotMatch(engineering, /entry\.runId \|\| entry\.createdAt/);
    assert.doesNotMatch(engineering, /selectedProof\.sessionId \|\| "-"/);
    assert.match(artifacts, /TechnicalReferenceDetails/);
    assert.doesNotMatch(artifacts, /rounded-full border px-2 py-1 font-mono/);
    assert.match(network, /TechnicalReferenceDetails/);
    assert.doesNotMatch(network, /break-all text-slate-500.*rawRef/);
});

test("knowledge graph uses clustered spacing, drag, subtle motion, and reduced-motion support", () => {
    const graph = read("src/components/memory/GraphViewer.tsx");

    assert.match(graph, /graphClusterKey/);
    assert.match(graph, /forceCollide/);
    assert.match(graph, /focusPrimaryGraph/);
    assert.match(graph, /centerAt/);
    assert.match(graph, /enableNodeDrag/);
    assert.match(graph, /prefers-reduced-motion: reduce/);
    assert.match(graph, /requestAnimationFrame/);
    assert.match(graph, /menuMode === "summary"/);
    assert.doesNotMatch(graph, /enableNodeDrag=\{false\}/);
    assert.doesNotMatch(graph, /zoomToFit/);
});

test("subagent orchestration cards retain editable research and recursive budgets", () => {
    const subagents = read("src/app/admin/(dashboard)/subagents/page.tsx");

    assert.match(subagents, /globalConfigDialog/);
    assert.match(subagents, /setGlobalConfigDialog\("research"\)/);
    assert.match(subagents, /setGlobalConfigDialog\("recursive"\)/);
    assert.match(subagents, /setResearchDefaultShards/);
    assert.match(subagents, /setResearchMaxShards/);
    assert.match(subagents, /setResearchMaxRounds/);
    assert.match(subagents, /setRecursiveMaxDepth/);
    assert.match(subagents, /setRecursiveMaxChildren/);
    assert.match(subagents, /setRecursiveMaxTotalNodes/);
    assert.match(subagents, /setRecursiveMaxConcurrent/);
});

test("Agent Browser is configured from Research without exposing Chrome and Edge choices", () => {
    const researchPage = read("src/app/admin/(dashboard)/research-runtime/page.tsx");
    const browserPanel = read("src/components/research/AgentBrowserPanel.tsx");
    const desktopPage = read("src/app/admin/(dashboard)/desktop-automation/page.tsx");
    const browserRoute = read("src/app/api/agent-browser/open/route.ts");

    assert.match(researchPage, /AgentBrowserPanel/);
    assert.match(browserPanel, /\/api\/agent-browser\/open/);
    assert.match(browserPanel, /agentBrowser\.title/);
    assert.doesNotMatch(browserPanel, /openChrome|openEdge|browserKind/);
    assert.doesNotMatch(desktopPage, /openAgentBrowser|agentBrowser\.openChrome|agentBrowser\.openEdge/);
    assert.match(browserRoute, /\/agent-browser\/open/);
});
