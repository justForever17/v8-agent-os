/* eslint-disable @typescript-eslint/no-explicit-any, react-hooks/immutability -- Mutable synthetic HTTP and browser controls are the fixture boundary. */
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { SpecDocumentConfirmationDialog } from "../src/components/chat/SpecDocumentConfirmationDialog";
import { useLangGraphStream } from "../src/hooks/use-langgraph-stream";
import { FixtureLocale } from "./spec-review-ui-locale";
import type { SpecReviewDecision } from "../src/lib/spec-review";

const runtime = window as any;
const scenario = String(runtime.specReviewScenario || "normal");
const original = "\uFEFF# Review version 1\r\n\r\n- 中文文件，保留空白。\r\n\r\nORIGINAL_END\r\n";
const path = ".v8/specs/spec-a/requirements.md";
const sha256 = async (content: string) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content))), byte => byte.toString(16).padStart(2, "0")).join("");

async function boot() {
    const originalHash = await sha256(original);
    let disk = { content: original, documentSha256: originalHash, documentPath: path };
    if (scenario === "stale") disk = { ...disk, content: original + "\nREMOTE_NEW_VERSION", documentSha256: await sha256(original + "\nREMOTE_NEW_VERSION") };
    const makeApproval = (id: string, document = disk) => ({ id, approval_kind: "spec_stage_approval", request: {
        specId: "spec-a", stage: "requirements", workspacePath: "E:/synthetic-spec-workspace",
        documentSha256: document.documentSha256, documentPath: document.documentPath, summary: "Synthetic review summary",
    } });
    const initialApproval = makeApproval("approval-a", { ...disk, documentSha256: originalHash });
    const log: any[] = [];
    const completed: Record<string, number> = {};
    const gates = new Map<string, () => void>();
    const held = new Set(scenario.startsWith("save-pending") ? ["save"] : []);
    const counts: Record<string, number> = {};
    const fixture: any = runtime.fixture = { original, originalHash, log, completed, ready: false, calls: [], replacements: [],
        release(name: string) { held.delete(name); gates.get(name)?.(); gates.delete(name); },
        disk: () => disk,
        makeApproval,
        async setDisk(content: string) { disk = { ...disk, content, documentSha256: await sha256(content) }; },
    };
    // Sole synthetic boundary: HTTP. Actual Dialog, React, Markdown, hooks and
    // controls below are bundled from the current product sources.
    window.fetch = async (input, init) => {
        const url = new URL(typeof input === "string" ? input : input instanceof Request ? input.url : input.href, location.href);
        const operation = url.pathname.endsWith("/edit") ? "save" : url.pathname.endsWith("refresh-spec-review") ? "refresh"
            : url.pathname.endsWith("/approve") ? "approve" : /^\/api\/specs\/[^/]+$/.test(url.pathname) ? "read" : "unexpected";
        if (operation === "unexpected") throw new Error(`Undeclared fixture request: ${url.pathname}`);
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        log.push({ operation, path: url.pathname, query: url.search, body });
        counts[operation] = (counts[operation] || 0) + 1;
        const count = counts[operation];
        if (held.has(operation)) await new Promise<void>(resolve => gates.set(operation, resolve));
        let payload: any;
        let status = 200;
        if (operation === "read") {
            payload = { ok: true, stages: { requirements: { ...disk, relativePath: disk.documentPath, truncated: scenario === "truncated" } } };
            if (scenario === "get-fail") { status = 503; payload = { detail: "SYNTHETIC_READ_FAILURE" }; }
            if (scenario === "hash-mismatch") payload.stages.requirements.documentSha256 = "f".repeat(64);
        } else if (operation === "save") {
            if (scenario === "save-conflict") { status = 409; payload = { detail: { code: "spec_approval_document_changed", message: "SYNTHETIC_SAVE_CONFLICT" } }; }
            else if (scenario === "save-semantic-fail") payload = { ok: false, kind: "spec_stage_locked", error: "SYNTHETIC_SAVE_LOCKED" };
            else {
                const savedContent = String(body.content) + (scenario === "normalized" ? "\nSERVER_NORMALIZED" : "");
                disk = { content: savedContent, documentSha256: await sha256(savedContent), documentPath: path };
                payload = { ok: true, ...disk };
            }
        } else if (operation === "refresh") {
            payload = { approval: makeApproval("approval-refreshed"), replacesApprovalId: url.pathname.split("/")[3] };
        } else {
            payload = { approval: { ...makeApproval(body?.response?.replaceSpecReview ? "approval-replaced" : url.pathname.split("/")[3]), status: "approved" },
                ...(body?.response?.replaceSpecReview ? { replacesApprovalId: url.pathname.split("/")[3] } : {}) };
            if (scenario === "approve-fail-once" && count === 1) { status = 503; payload = { detail: "SYNTHETIC_APPROVAL_FAILURE" }; }
            if (scenario === "approve-semantic-fail") payload = { ok: false, detail: { message: "SYNTHETIC_APPROVAL_REJECTED" } };
        }
        completed[operation] = (completed[operation] || 0) + 1;
        return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
    };
    function App() {
        const [approval, setApproval] = useState(initialApproval);
        const [open, setOpen] = useState(true);
        const { resolveApproval } = useLangGraphStream({ apiEndpoint: "/unused", conversationId: "fixture-session", onResync: async () => {} });
        fixture.close = () => setOpen(false);
        fixture.open = () => setOpen(true);
        fixture.switchId = (id: string) => setApproval(makeApproval(id));
        fixture.summary = () => setApproval(current => ({ ...current, request: { ...current.request, summary: "Updated summary only" } }));
        fixture.ready = true;
        const onApprove = async (answer: string, review?: SpecReviewDecision) => {
            fixture.calls.push({ answer, review });
            await resolveApproval(review?.approvalId || approval.id, answer, true, review);
        };
        return <SpecDocumentConfirmationDialog isOpen={open} approval={approval} onApprove={onApprove}
            onReject={async answer => { await resolveApproval(approval.id, answer, false); }}
            onViewDetails={() => {}} onCancel={() => setOpen(false)}
            onReplaceApproval={(next, old) => { fixture.replacements.push({ next, old }); setApproval(next as typeof approval); }} />;
    }
    createRoot(document.getElementById("root")!).render(<FixtureLocale><App /></FixtureLocale>);
}
void boot().catch(error => { runtime.fixtureBootError = String(error); });
