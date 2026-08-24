"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
    ArrowLeft,
    Check,
    ChevronDown,
    Code2,
    Eye,
    FileCode2,
    LoaderCircle,
    MousePointer2,
    RefreshCw,
    Save,
    Undo2,
    Unplug,
    X,
} from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";


type PreviewSession = {
    patchSessionId: string;
    sessionId: string;
    mode: "static" | "dev";
    entryPath?: string | null;
    targetUrl?: string | null;
    state: string;
    previewOrigin: string;
    previewUrl: string;
};

type SourceCandidate = {
    candidateId: string;
    workspacePath: string;
    selector: string;
    sourceKind: "css" | "html_style" | "html_text" | string;
    declarations: Record<string, string>;
    reason: string;
};

type MappedSelection = {
    selectionRef: string;
    selector: string;
    tagName: string;
    label: string;
    computedStyles: Record<string, string>;
    textContent?: string;
    textEditable?: boolean;
    sourceCandidates: SourceCandidate[];
    writable: boolean;
    unsupportedReason?: string | null;
    allowedProperties: string[];
};

type RawSelection = {
    selector: string;
    tagName?: string;
    label?: string;
    computedStyles?: Record<string, string>;
    textContent?: string;
    textEditable?: boolean;
    rules?: Array<Record<string, unknown>>;
};

type CommitResult = {
    transactionId: string;
    workspacePath: string;
    selector: string;
    changes: Record<string, string>;
    diff: string;
    beforeHash: string;
    afterHash: string;
};

type PendingVerification = {
    transactionId: string;
    selector: string;
    expectedStyles: Record<string, string>;
    attempts: number;
};

type BridgeMessage = {
    type?: string;
    patchSessionId?: string;
    selection?: RawSelection;
    computedStyles?: Record<string, string>;
    requestId?: string;
    ok?: boolean;
    reason?: string;
    observedStyles?: Record<string, string>;
};

type VerificationState = "idle" | "saving" | "reloading" | "verified" | "failed" | "undone";

const PROPERTY_SECTIONS = [
    {
        id: "layout",
        labelKey: "web.uiPatch.section.layout",
        properties: [
            "display", "width", "height", "min-width", "min-height", "max-width", "max-height",
            "gap", "row-gap", "column-gap", "flex", "flex-direction", "flex-wrap",
            "align-items", "justify-content", "grid-template-columns", "grid-template-rows",
        ],
    },
    {
        id: "spacing",
        labelKey: "web.uiPatch.section.spacing",
        properties: [
            "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
            "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
        ],
    },
    {
        id: "position",
        labelKey: "web.uiPatch.section.position",
        properties: ["position", "top", "right", "bottom", "left", "z-index", "overflow"],
    },
    {
        id: "surface",
        labelKey: "web.uiPatch.section.surface",
        properties: [
            "background-color", "background", "border", "border-width", "border-style", "border-color",
            "border-radius", "box-shadow", "opacity",
        ],
    },
    {
        id: "typography",
        labelKey: "web.uiPatch.section.typography",
        properties: ["color", "font-size", "font-weight", "line-height", "letter-spacing", "text-align"],
    },
] as const;

const ENUM_VALUES: Record<string, string[]> = {
    display: ["block", "inline", "inline-block", "flex", "inline-flex", "grid", "inline-grid", "none", "contents"],
    position: ["static", "relative", "absolute", "fixed", "sticky"],
    overflow: ["visible", "hidden", "clip", "scroll", "auto"],
    "flex-direction": ["row", "row-reverse", "column", "column-reverse"],
    "flex-wrap": ["nowrap", "wrap", "wrap-reverse"],
    "align-items": ["normal", "stretch", "center", "start", "end", "flex-start", "flex-end", "baseline"],
    "justify-content": ["normal", "center", "start", "end", "flex-start", "flex-end", "space-between", "space-around", "space-evenly", "stretch"],
    "border-style": ["none", "dotted", "dashed", "solid", "double", "inset", "outset"],
    "text-align": ["start", "end", "left", "right", "center", "justify"],
};

const VIEWPORTS = [
    { id: "fluid", width: 0, label: "Fluid" },
    { id: "desktop", width: 1440, label: "1440" },
    { id: "laptop", width: 1280, label: "1280" },
    { id: "compact", width: 1024, label: "1024" },
] as const;

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
        cache: "no-store",
    });
    const payload = await response.json().catch(() => ({})) as T & { detail?: unknown; error?: unknown };
    if (!response.ok) throw new Error(String(payload.detail || payload.error || `HTTP ${response.status}`));
    return payload;
}

function safeReturnPath(value: string | null) {
    return value && value.startsWith("/chat") ? value : "/chat";
}

const PROPERTY_LABEL_KEYS: Record<string, string> = {
    display: "web.uiPatch.property.display",
    width: "web.uiPatch.property.width",
    height: "web.uiPatch.property.height",
    "min-width": "web.uiPatch.property.minWidth",
    "min-height": "web.uiPatch.property.minHeight",
    "max-width": "web.uiPatch.property.maxWidth",
    "max-height": "web.uiPatch.property.maxHeight",
    gap: "web.uiPatch.property.gap",
    "row-gap": "web.uiPatch.property.rowGap",
    "column-gap": "web.uiPatch.property.columnGap",
    flex: "web.uiPatch.property.flex",
    "flex-direction": "web.uiPatch.property.flexDirection",
    "flex-wrap": "web.uiPatch.property.flexWrap",
    "align-items": "web.uiPatch.property.alignItems",
    "justify-content": "web.uiPatch.property.justifyContent",
    "grid-template-columns": "web.uiPatch.property.gridColumns",
    "grid-template-rows": "web.uiPatch.property.gridRows",
    padding: "web.uiPatch.property.padding",
    "padding-top": "web.uiPatch.property.paddingTop",
    "padding-right": "web.uiPatch.property.paddingRight",
    "padding-bottom": "web.uiPatch.property.paddingBottom",
    "padding-left": "web.uiPatch.property.paddingLeft",
    margin: "web.uiPatch.property.margin",
    "margin-top": "web.uiPatch.property.marginTop",
    "margin-right": "web.uiPatch.property.marginRight",
    "margin-bottom": "web.uiPatch.property.marginBottom",
    "margin-left": "web.uiPatch.property.marginLeft",
    position: "web.uiPatch.property.position",
    top: "web.uiPatch.property.top",
    right: "web.uiPatch.property.right",
    bottom: "web.uiPatch.property.bottom",
    left: "web.uiPatch.property.left",
    "z-index": "web.uiPatch.property.zIndex",
    overflow: "web.uiPatch.property.overflow",
    "background-color": "web.uiPatch.property.backgroundColor",
    background: "web.uiPatch.property.background",
    border: "web.uiPatch.property.border",
    "border-width": "web.uiPatch.property.borderWidth",
    "border-style": "web.uiPatch.property.borderStyle",
    "border-color": "web.uiPatch.property.borderColor",
    "border-radius": "web.uiPatch.property.borderRadius",
    "box-shadow": "web.uiPatch.property.boxShadow",
    opacity: "web.uiPatch.property.opacity",
    color: "web.uiPatch.property.color",
    "font-size": "web.uiPatch.property.fontSize",
    "font-weight": "web.uiPatch.property.fontWeight",
    "line-height": "web.uiPatch.property.lineHeight",
    "letter-spacing": "web.uiPatch.property.letterSpacing",
    "text-align": "web.uiPatch.property.textAlign",
};

function propertyLabel(property: string, t: ReturnType<typeof useT>) {
    const key = PROPERTY_LABEL_KEYS[property];
    return key ? t(key) : property.replaceAll("-", " ");
}

function isHexColor(value: string) {
    return /^#[0-9a-f]{6}$/i.test(value.trim());
}

function DiffView({ diff }: { diff: string }) {
    if (!diff) return null;
    return (
        <pre className="max-h-56 overflow-auto border-t border-border/70 bg-[#0d1117] p-3 font-mono text-[11px] leading-5 text-slate-300">
            {diff.split("\n").map((line, index) => (
                <span
                    key={`${index}:${line.slice(0, 24)}`}
                    className={`block whitespace-pre ${
                        line.startsWith("+") && !line.startsWith("+++")
                            ? "bg-emerald-500/15 text-emerald-300"
                            : line.startsWith("-") && !line.startsWith("---")
                                ? "bg-rose-500/15 text-rose-300"
                                : line.startsWith("@@")
                                    ? "text-sky-300"
                                    : ""
                    }`}
                >
                    {line || " "}
                </span>
            ))}
        </pre>
    );
}

export function UiPatchWorkbench() {
    const t = useT();
    const router = useRouter();
    const searchParams = useSearchParams();
    const sessionId = String(searchParams.get("sessionId") || "").trim();
    const initialEntryPath = String(searchParams.get("entryPath") || "").trim();
    const initialTargetUrl = String(searchParams.get("targetUrl") || "").trim();
    const returnTo = safeReturnPath(searchParams.get("returnTo"));

    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const patchSessionRef = useRef("");
    const selectionSequenceRef = useRef(0);
    const previewComputedRef = useRef<Record<string, string>>({});
    const previewResolverRef = useRef<((value: Record<string, string>) => void) | null>(null);
    const pendingVerificationRef = useRef<PendingVerification | null>(null);
    const verificationTimerRef = useRef<number | null>(null);
    const autoStartedRef = useRef(false);

    const [sourceMode, setSourceMode] = useState<"static" | "dev">(initialTargetUrl ? "dev" : "static");
    const [entryPath, setEntryPath] = useState(initialEntryPath);
    const [targetUrl, setTargetUrl] = useState(initialTargetUrl || "http://127.0.0.1:3000");
    const [preview, setPreview] = useState<PreviewSession | null>(null);
    const [previewOrigin, setPreviewOrigin] = useState("");
    const [starting, setStarting] = useState(false);
    const [ready, setReady] = useState(false);
    const [error, setError] = useState("");
    const [interactionMode, setInteractionMode] = useState<"select" | "interact">("select");
    const [viewportWidth, setViewportWidth] = useState(0);
    const [selection, setSelection] = useState<MappedSelection | null>(null);
    const [mappingSelection, setMappingSelection] = useState(false);
    const [candidateId, setCandidateId] = useState("");
    const [changes, setChanges] = useState<Record<string, string>>({});
    const [lastCommit, setLastCommit] = useState<CommitResult | null>(null);
    const [verificationState, setVerificationState] = useState<VerificationState>("idle");

    const selectedCandidate = useMemo(
        () => selection?.sourceCandidates.find((candidate) => candidate.candidateId === candidateId) || selection?.sourceCandidates[0] || null,
        [candidateId, selection?.sourceCandidates],
    );
    const styleEditingEnabled = Boolean(selection?.writable && selectedCandidate?.sourceKind !== "html_text");
    const textEditingEnabled = Boolean(
        selection?.writable
        && selection.textEditable
        && selectedCandidate
        && (selectedCandidate.sourceKind === "html_style" || selectedCandidate.sourceKind === "html_text"),
    );

    const postToPreview = useCallback((message: Record<string, unknown>) => {
        if (!preview || !previewOrigin || !iframeRef.current?.contentWindow) return;
        iframeRef.current.contentWindow.postMessage(
            { ...message, patchSessionId: preview.patchSessionId },
            previewOrigin,
        );
    }, [preview, previewOrigin]);

    const sendVerification = useCallback(() => {
        const pending = pendingVerificationRef.current;
        if (!pending) return;
        pending.attempts += 1;
        const requestId = `${pending.transactionId}:${pending.attempts}`;
        postToPreview({
            type: "v8-ui-patch:verify",
            requestId,
            selector: pending.selector,
            expectedStyles: pending.expectedStyles,
        });
    }, [postToPreview]);

    const mapRawSelection = useCallback(async (rawSelection: RawSelection) => {
        if (!preview || !sessionId) return;
        const sequence = ++selectionSequenceRef.current;
        setMappingSelection(true);
        setError("");
        try {
            const mapped = await jsonRequest<MappedSelection>(
                `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/previews/${encodeURIComponent(preview.patchSessionId)}/selections`,
                { method: "POST", body: JSON.stringify({ selection: rawSelection }) },
            );
            if (sequence !== selectionSequenceRef.current) return;
            setSelection(mapped);
            setCandidateId(mapped.sourceCandidates[0]?.candidateId || "");
            setChanges({});
            previewComputedRef.current = {};
            setLastCommit(null);
            setVerificationState("idle");
        } catch (reason) {
            if (sequence === selectionSequenceRef.current) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (sequence === selectionSequenceRef.current) setMappingSelection(false);
        }
    }, [preview, sessionId]);

    const recordVerification = useCallback(async (
        transactionId: string,
        status: "verified" | "failed",
        observedStyles: Record<string, string>,
        reason = "",
    ) => {
        if (!sessionId) return;
        await jsonRequest(
            `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/transactions/${encodeURIComponent(transactionId)}/verification`,
            { method: "POST", body: JSON.stringify({ status, observedStyles, reason }) },
        );
    }, [sessionId]);

    useEffect(() => {
        const onMessage = (event: MessageEvent<BridgeMessage>) => {
            if (!preview || event.origin !== previewOrigin || event.source !== iframeRef.current?.contentWindow) return;
            const message = event.data || {};
            if (message.patchSessionId !== preview.patchSessionId) return;
            if (message.type === "v8-ui-patch:ready") {
                setReady(true);
                postToPreview({ type: "v8-ui-patch:set-mode", mode: interactionMode });
                if (pendingVerificationRef.current) {
                    if (verificationTimerRef.current) window.clearTimeout(verificationTimerRef.current);
                    verificationTimerRef.current = window.setTimeout(sendVerification, 350);
                }
                return;
            }
            if (message.type === "v8-ui-patch:selected" && message.selection) {
                void mapRawSelection(message.selection);
                return;
            }
            if (message.type === "v8-ui-patch:preview-applied") {
                const computed = message.computedStyles || {};
                previewComputedRef.current = computed;
                previewResolverRef.current?.(computed);
                previewResolverRef.current = null;
                return;
            }
            if (message.type === "v8-ui-patch:verification") {
                const pending = pendingVerificationRef.current;
                if (!pending || !String(message.requestId || "").startsWith(`${pending.transactionId}:`)) return;
                if (message.ok) {
                    pendingVerificationRef.current = null;
                    setVerificationState("verified");
                    void recordVerification(pending.transactionId, "verified", message.observedStyles || {})
                        .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
                    postToPreview({ type: "v8-ui-patch:restore-selection", selector: pending.selector });
                } else if (pending.attempts < 3) {
                    verificationTimerRef.current = window.setTimeout(sendVerification, 700);
                } else {
                    pendingVerificationRef.current = null;
                    setVerificationState("failed");
                    void recordVerification(pending.transactionId, "failed", message.observedStyles || {}, message.reason || "computed_style_mismatch")
                        .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
                    postToPreview({ type: "v8-ui-patch:restore-selection", selector: pending.selector });
                }
            }
        };
        window.addEventListener("message", onMessage);
        return () => window.removeEventListener("message", onMessage);
    }, [interactionMode, mapRawSelection, postToPreview, preview, previewOrigin, recordVerification, sendVerification]);

    useEffect(() => {
        postToPreview({ type: "v8-ui-patch:set-mode", mode: interactionMode });
    }, [interactionMode, postToPreview]);

    useEffect(() => {
        if (!selection || !Object.keys(changes).length) {
            postToPreview({ type: "v8-ui-patch:clear-preview" });
            previewComputedRef.current = {};
            return;
        }
        postToPreview({ type: "v8-ui-patch:apply-preview", changes });
    }, [changes, postToPreview, selection]);

    useEffect(() => {
        patchSessionRef.current = preview?.patchSessionId || "";
    }, [preview?.patchSessionId]);

    useEffect(() => () => {
        const patchSessionId = patchSessionRef.current;
        if (!patchSessionId || !sessionId) return;
        void fetch(
            `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/previews/${encodeURIComponent(patchSessionId)}`,
            { method: "DELETE", keepalive: true },
        );
    }, [sessionId]);

    useEffect(() => () => {
        if (verificationTimerRef.current) window.clearTimeout(verificationTimerRef.current);
    }, []);

    const startPreview = useCallback(async () => {
        if (!sessionId || starting) return;
        setStarting(true);
        setError("");
        setReady(false);
        setSelection(null);
        setChanges({});
        setLastCommit(null);
        setVerificationState("idle");
        try {
            const payload = await jsonRequest<PreviewSession>(
                `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/previews`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        parentOrigin: window.location.origin,
                        ...(sourceMode === "static" ? { entryPath: entryPath.trim() } : { targetUrl: targetUrl.trim() }),
                    }),
                },
            );
            setPreview(payload);
            setPreviewOrigin(new URL(payload.previewUrl).origin);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setStarting(false);
        }
    }, [entryPath, sessionId, sourceMode, starting, targetUrl]);

    useEffect(() => {
        if (autoStartedRef.current || !sessionId || (!initialEntryPath && !initialTargetUrl)) return;
        autoStartedRef.current = true;
        void startPreview();
    }, [initialEntryPath, initialTargetUrl, sessionId, startPreview]);

    const requestPreviewComputed = useCallback(() => new Promise<Record<string, string>>((resolve) => {
        if (!Object.keys(changes).length) {
            resolve({});
            return;
        }
        let settled = false;
        previewResolverRef.current = (value) => {
            if (settled) return;
            settled = true;
            resolve(value);
        };
        postToPreview({ type: "v8-ui-patch:apply-preview", changes });
        window.setTimeout(() => {
            if (settled) return;
            settled = true;
            previewResolverRef.current = null;
            resolve(previewComputedRef.current);
        }, 600);
    }), [changes, postToPreview]);

    const savePatch = useCallback(async () => {
        if (!preview || !selection || !selectedCandidate || !Object.keys(changes).length || verificationState === "saving") return;
        setVerificationState("saving");
        setError("");
        try {
            const expectedStyles = await requestPreviewComputed();
            const changedProperties = Object.keys(changes);
            const missingPreviewValues = changedProperties.filter((property) => (
                property === "__text_content"
                    ? !Object.prototype.hasOwnProperty.call(expectedStyles, property)
                    : !String(expectedStyles[property] || "").trim()
            ));
            if (missingPreviewValues.length) {
                throw new Error(t("web.uiPatch.previewVerificationUnavailable"));
            }
            const result = await jsonRequest<CommitResult>(
                `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/previews/${encodeURIComponent(preview.patchSessionId)}/commits`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        selectionRef: selection.selectionRef,
                        candidateId: selectedCandidate.candidateId,
                        changes,
                    }),
                },
            );
            setLastCommit(result);
            pendingVerificationRef.current = {
                transactionId: result.transactionId,
                selector: result.selector,
                expectedStyles,
                attempts: 0,
            };
            setVerificationState("reloading");
            setReady(false);
            postToPreview({ type: "v8-ui-patch:reload" });
        } catch (reason) {
            setVerificationState("failed");
            setError(reason instanceof Error ? reason.message : String(reason));
        }
    }, [changes, preview, requestPreviewComputed, selectedCandidate, selection, sessionId, verificationState, postToPreview, t]);

    const undoPatch = useCallback(async () => {
        if (!lastCommit || !sessionId) return;
        setError("");
        try {
            await jsonRequest(
                `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/transactions/${encodeURIComponent(lastCommit.transactionId)}/undo`,
                { method: "POST", body: "{}" },
            );
            pendingVerificationRef.current = null;
            setVerificationState("undone");
            setSelection(null);
            setChanges({});
            setReady(false);
            postToPreview({ type: "v8-ui-patch:reload" });
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        }
    }, [lastCommit, postToPreview, sessionId]);

    const closePreview = useCallback(async () => {
        const current = preview;
        setPreview(null);
        setPreviewOrigin("");
        setReady(false);
        setSelection(null);
        setChanges({});
        setLastCommit(null);
        pendingVerificationRef.current = null;
        if (!current || !sessionId) return;
        try {
            await jsonRequest(
                `/api/ui-patch/sessions/${encodeURIComponent(sessionId)}/previews/${encodeURIComponent(current.patchSessionId)}`,
                { method: "DELETE" },
            );
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        }
    }, [preview, sessionId]);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                event.preventDefault();
                void savePatch();
            } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && lastCommit) {
                event.preventDefault();
                void undoPatch();
            } else if (event.key === "Escape") {
                setChanges({});
                setSelection(null);
                postToPreview({ type: "v8-ui-patch:clear-preview" });
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [lastCommit, postToPreview, savePatch, undoPatch]);

    const baseValue = useCallback((property: string) => {
        if (property === "__text_content") return selection?.textContent || "";
        const declaration = selectedCandidate?.declarations[property];
        return String(declaration || selection?.computedStyles[property] || "").replace(/\s*!important\s*$/i, "").trim();
    }, [selectedCandidate?.declarations, selection?.computedStyles, selection?.textContent]);

    const updateProperty = useCallback((property: string, value: string) => {
        const normalized = property === "__text_content" ? value : value.trim();
        setChanges((current) => {
            const next = { ...current };
            if ((property !== "__text_content" && !normalized) || normalized === baseValue(property)) delete next[property];
            else next[property] = normalized;
            return next;
        });
    }, [baseValue]);

    const verificationLabel = verificationState === "verified"
        ? t("web.uiPatch.status.verified")
        : verificationState === "failed"
            ? t("web.uiPatch.status.failed")
            : verificationState === "reloading"
                ? t("web.uiPatch.status.verifying")
                : verificationState === "undone"
                    ? t("web.uiPatch.status.undone")
                    : "";

    if (!sessionId) {
        return (
            <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-background px-8 text-center">
                <Unplug className="h-7 w-7 text-muted-foreground" />
                <div className="text-sm font-medium">{t("web.uiPatch.missingSession")}</div>
                <button type="button" onClick={() => router.push("/chat")} className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">
                    {t("web.uiPatch.backToTask")}
                </button>
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-0 w-full flex-col bg-background">
            <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/70 px-2">
                <button type="button" onClick={() => router.push(returnTo)} className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label={t("web.uiPatch.backToTask")}>
                    <ArrowLeft className="h-4 w-4" />
                </button>
                <div className="flex min-w-0 items-center gap-2">
                    <FileCode2 className="h-4 w-4 text-primary" />
                    <span className="text-sm font-semibold">{t("web.uiPatch.title")}</span>
                    {preview ? <span className="max-w-[36vw] truncate font-mono text-[10px] text-muted-foreground">{preview.entryPath || preview.targetUrl}</span> : null}
                </div>
                <div className="ml-auto flex items-center gap-1.5">
                    {verificationLabel ? (
                        <span className={`inline-flex h-6 items-center gap-1 rounded border px-2 text-[10px] ${verificationState === "verified" ? "border-emerald-500/35 text-emerald-600 dark:text-emerald-400" : verificationState === "failed" ? "border-rose-500/35 text-rose-600 dark:text-rose-400" : "border-border text-muted-foreground"}`}>
                            {verificationState === "verified" ? <Check className="h-3 w-3" /> : verificationState === "reloading" ? <LoaderCircle className="h-3 w-3 animate-spin" /> : null}
                            {verificationLabel}
                        </span>
                    ) : null}
                    {lastCommit ? (
                        <button type="button" onClick={() => void undoPatch()} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">
                            <Undo2 className="h-3.5 w-3.5" />{t("web.uiPatch.undo")}
                        </button>
                    ) : null}
                    <button
                        type="button"
                        disabled={!selection?.writable || !selectedCandidate || !Object.keys(changes).length || verificationState === "saving" || verificationState === "reloading"}
                        onClick={() => void savePatch()}
                        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                    >
                        {verificationState === "saving" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                        {t("web.uiPatch.save")}
                    </button>
                </div>
            </header>

            {!preview ? (
                <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-8">
                    <div className="w-full max-w-xl border border-border bg-card">
                        <div className="border-b border-border px-5 py-4">
                            <h1 className="text-base font-semibold">{t("web.uiPatch.start.title")}</h1>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("web.uiPatch.start.description")}</p>
                        </div>
                        <div className="flex border-b border-border">
                            <button type="button" onClick={() => setSourceMode("static")} className={`h-9 flex-1 border-b-2 text-xs ${sourceMode === "static" ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{t("web.uiPatch.source.static")}</button>
                            <button type="button" onClick={() => setSourceMode("dev")} className={`h-9 flex-1 border-b-2 text-xs ${sourceMode === "dev" ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{t("web.uiPatch.source.dev")}</button>
                        </div>
                        <div className="space-y-3 p-5">
                            <label className="block text-xs font-medium">
                                {sourceMode === "static" ? t("web.uiPatch.entryPath") : t("web.uiPatch.targetUrl")}
                                <input
                                    value={sourceMode === "static" ? entryPath : targetUrl}
                                    onChange={(event) => sourceMode === "static" ? setEntryPath(event.target.value) : setTargetUrl(event.target.value)}
                                    onKeyDown={(event) => { if (event.key === "Enter") void startPreview(); }}
                                    placeholder={sourceMode === "static" ? "index.html" : "http://127.0.0.1:3000"}
                                    className="mt-1.5 h-9 w-full rounded-md border border-border bg-background px-3 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                                />
                            </label>
                            <div className="border-l-2 border-amber-500/50 pl-3 text-[11px] leading-5 text-muted-foreground">
                                {sourceMode === "static" ? t("web.uiPatch.source.staticHint") : t("web.uiPatch.source.devHint")}
                            </div>
                            {error ? <div role="alert" className="border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-600 dark:text-rose-300">{error}</div> : null}
                            <div className="flex justify-end">
                                <button type="button" disabled={starting || (sourceMode === "static" ? !entryPath.trim() : !targetUrl.trim())} onClick={() => void startPreview()} className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-4 text-xs font-medium text-primary-foreground disabled:opacity-40">
                                    {starting ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                                    {t("web.uiPatch.start.action")}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            ) : (
                <div className="flex min-h-0 flex-1">
                    <section className="flex min-w-0 flex-1 flex-col bg-muted/25">
                        <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border/70 bg-background px-2">
                            <div className="flex h-7 items-center rounded-md border border-border bg-muted/35 p-0.5">
                                <button type="button" onClick={() => setInteractionMode("select")} className={`inline-flex h-6 items-center gap-1 rounded px-2 text-[11px] ${interactionMode === "select" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                                    <MousePointer2 className="h-3 w-3" />{t("web.uiPatch.mode.select")}
                                </button>
                                <button type="button" onClick={() => setInteractionMode("interact")} className={`inline-flex h-6 items-center gap-1 rounded px-2 text-[11px] ${interactionMode === "interact" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                                    <Eye className="h-3 w-3" />{t("web.uiPatch.mode.interact")}
                                </button>
                            </div>
                            <div className="mx-1 h-4 w-px bg-border" />
                            {VIEWPORTS.map((viewport) => (
                                <button key={viewport.id} type="button" onClick={() => setViewportWidth(viewport.width)} className={`h-6 rounded px-2 font-mono text-[10px] ${viewportWidth === viewport.width ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}>{viewport.label}</button>
                            ))}
                            <button type="button" onClick={() => { setReady(false); postToPreview({ type: "v8-ui-patch:reload" }); }} className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.uiPatch.reload")}>
                                <RefreshCw className="h-3.5 w-3.5" />
                            </button>
                            <button type="button" onClick={() => void closePreview()} className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.uiPatch.disconnect")}>
                                <X className="h-3.5 w-3.5" />
                            </button>
                        </div>
                        <div className="relative min-h-0 flex-1 overflow-auto p-3">
                            {!ready ? (
                                <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/65 backdrop-blur-[1px]">
                                    <div className="inline-flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />{t("web.uiPatch.loadingPreview")}</div>
                                </div>
                            ) : null}
                            <div className="mx-auto h-full min-h-[520px] overflow-hidden border border-border bg-white shadow-[0_12px_36px_rgba(15,23,42,0.10)]" style={{ width: viewportWidth ? `${viewportWidth}px` : "100%", minWidth: viewportWidth ? `${viewportWidth}px` : "720px" }}>
                                <iframe ref={iframeRef} title={t("web.uiPatch.previewTitle")} src={preview.previewUrl} className="h-full w-full border-0 bg-white" allow="clipboard-read; clipboard-write" />
                            </div>
                        </div>
                    </section>

                    <aside className="flex w-[360px] shrink-0 flex-col border-l border-border bg-background">
                        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
                            <Code2 className="h-3.5 w-3.5 text-muted-foreground" />
                            <span className="text-xs font-medium">{t("web.uiPatch.inspector")}</span>
                            {mappingSelection ? <LoaderCircle className="ml-auto h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
                        </div>
                        {!selection ? (
                            <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-8 text-center">
                                <MousePointer2 className="h-6 w-6 text-muted-foreground/65" />
                                <div className="mt-3 text-sm font-medium">{t("web.uiPatch.emptySelection.title")}</div>
                                <div className="mt-1 text-xs leading-5 text-muted-foreground">{t("web.uiPatch.emptySelection.description")}</div>
                            </div>
                        ) : (
                            <>
                                <div className="border-b border-border px-3 py-2.5">
                                    <div className="flex items-center gap-2">
                                        <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-foreground">{selection.tagName || "element"}</code>
                                        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={selection.label}>{selection.label || selection.selector}</span>
                                    </div>
                                    <div className="mt-1.5 truncate font-mono text-[10px] text-muted-foreground" title={selection.selector}>{selection.selector}</div>
                                    {selection.sourceCandidates.length ? (
                                        <label className="mt-2 block text-[10px] text-muted-foreground">
                                            {t("web.uiPatch.sourceRule")}
                                            <div className="relative mt-1">
                                                <select value={candidateId} onChange={(event) => { setCandidateId(event.target.value); setChanges({}); }} className="h-8 w-full appearance-none rounded-md border border-border bg-background px-2 pr-7 font-mono text-[10px] text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/15">
                                                    {selection.sourceCandidates.map((candidate) => <option key={candidate.candidateId} value={candidate.candidateId}>{candidate.workspacePath} · {candidate.selector}</option>)}
                                                </select>
                                                <ChevronDown className="pointer-events-none absolute right-2 top-2 h-3.5 w-3.5" />
                                            </div>
                                        </label>
                                    ) : (
                                        <div className="mt-2 border-l-2 border-amber-500/60 pl-2 text-[11px] leading-5 text-muted-foreground">
                                            {t("web.uiPatch.unsupportedSource")}
                                        </div>
                                    )}
                                </div>
                                <div className="min-h-0 flex-1 overflow-auto">
                                    <details open className="group border-b border-border/70">
                                        <summary className="flex h-8 cursor-pointer list-none items-center px-3 text-[11px] font-medium text-foreground hover:bg-muted/45">
                                            {t("web.uiPatch.content.text")}
                                            <ChevronDown className="ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform group-open:rotate-180" />
                                        </summary>
                                        <div className="space-y-1.5 px-3 pb-3">
                                            <textarea
                                                id="ui-patch-text-content"
                                                value={Object.prototype.hasOwnProperty.call(changes, "__text_content") ? changes.__text_content : (selection.textContent || "")}
                                                disabled={!textEditingEnabled}
                                                onChange={(event) => updateProperty("__text_content", event.target.value)}
                                                className="min-h-20 w-full resize-y rounded border border-border bg-background px-2 py-1.5 text-[11px] leading-4 outline-none disabled:opacity-45 focus:border-primary focus:ring-2 focus:ring-primary/15"
                                            />
                                            {!textEditingEnabled ? <p className="text-[10px] leading-4 text-muted-foreground">{t("web.uiPatch.content.readOnly")}</p> : null}
                                        </div>
                                    </details>
                                    {PROPERTY_SECTIONS.map((section) => (
                                        <details key={section.id} open className="group border-b border-border/70">
                                            <summary className="flex h-8 cursor-pointer list-none items-center px-3 text-[11px] font-medium text-foreground hover:bg-muted/45">
                                                {t(section.labelKey)}
                                                <ChevronDown className="ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform group-open:rotate-180" />
                                            </summary>
                                            <div className="pb-1">
                                                {section.properties.map((property) => {
                                                    const base = baseValue(property);
                                                    const value = Object.prototype.hasOwnProperty.call(changes, property) ? changes[property] : base;
                                                    const changed = Object.prototype.hasOwnProperty.call(changes, property);
                                                    const options = ENUM_VALUES[property];
                                                    return (
                                                        <div key={property} className="grid min-h-8 grid-cols-[116px_minmax(0,1fr)_24px] items-center gap-1 px-3 py-0.5 hover:bg-muted/30">
                                                            <label htmlFor={`ui-patch-${property}`} className={`truncate text-[10px] ${changed ? "text-primary" : "text-muted-foreground"}`}>{propertyLabel(property, t)}</label>
                                                            {options ? (
                                                                <select id={`ui-patch-${property}`} value={value} disabled={!styleEditingEnabled} onChange={(event) => updateProperty(property, event.target.value)} className="h-7 min-w-0 rounded border border-border bg-background px-2 text-[11px] outline-none disabled:opacity-45 focus:border-primary focus:ring-2 focus:ring-primary/15">
                                                                    {!options.includes(value) ? <option value={value}>{value || "—"}</option> : null}
                                                                    {options.map((option) => <option key={option} value={option}>{option}</option>)}
                                                                </select>
                                                            ) : (
                                                                <div className="flex min-w-0 items-center gap-1">
                                                                     {(property === "color" || property.includes("color")) && isHexColor(value) ? <input type="color" value={value} disabled={!styleEditingEnabled} onChange={(event) => updateProperty(property, event.target.value)} className="h-6 w-6 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0" aria-label={`${property} color`} /> : null}
                                                                     <input id={`ui-patch-${property}`} value={value} disabled={!styleEditingEnabled} onChange={(event) => updateProperty(property, event.target.value)} onBlur={(event) => updateProperty(property, event.target.value)} className="h-7 min-w-0 flex-1 rounded border border-border bg-background px-2 font-mono text-[10px] outline-none disabled:opacity-45 focus:border-primary focus:ring-2 focus:ring-primary/15" />
                                                                </div>
                                                            )}
                                                             <button type="button" disabled={!changed} onClick={() => updateProperty(property, base)} className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground disabled:opacity-0 hover:bg-muted hover:text-foreground" aria-label={`${t("web.uiPatch.resetProperty")} ${propertyLabel(property, t)}`}>
                                                                <Undo2 className="h-3 w-3" />
                                                            </button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </details>
                                    ))}
                                </div>
                                <div className="shrink-0 border-t border-border bg-background">
                                    <div className="flex min-h-9 items-center gap-2 px-3 text-[10px] text-muted-foreground">
                                        <span>{Object.keys(changes).length ? t("web.uiPatch.pendingChanges", { count: Object.keys(changes).length }) : t("web.uiPatch.noChanges")}</span>
                                        {error ? <span className="ml-auto max-w-48 truncate text-rose-600 dark:text-rose-400" title={error}>{error}</span> : null}
                                    </div>
                                    <DiffView diff={lastCommit?.diff || ""} />
                                </div>
                            </>
                        )}
                    </aside>
                </div>
            )}
        </div>
    );
}
