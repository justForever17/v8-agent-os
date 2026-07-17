"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useT } from "@/components/providers/LocaleProvider";

type SpecSummary = {
    specId?: string;
    featureName?: string;
    specKind?: string;
    lifecycle?: string;
    currentStage?: string;
    updatedAt?: string;
    pipelineControl?: {
        nextStage?: string;
        runtimeExecutionAllowed?: boolean;
        blockedByApproval?: string | null;
        staleStages?: string[];
        approvedStages?: string[];
    };
    documents?: Record<string, { ids?: string[]; status?: string; version?: number; relativePath?: string }>;
};

type SpecStage = {
    stage: string;
    content: string;
    truncated?: boolean;
    ids?: string[];
    documentRef?: string;
};

type SpecDetail = {
    ok?: boolean;
    spec?: SpecSummary;
    stages?: Record<string, SpecStage>;
};

const STAGES = ["requirements", "bugfix", "design", "tasks"];

function normalizeError(payload: unknown, fallback: string) {
    if (payload && typeof payload === "object" && "detail" in payload) {
        return String((payload as { detail?: unknown }).detail || fallback);
    }
    if (payload && typeof payload === "object" && "error" in payload) {
        return String((payload as { error?: unknown }).error || fallback);
    }
    return fallback;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(normalizeError(payload, fallback));
    }
    return payload as T;
}

export default function SpecApprovalClient({
    initialWorkspacePath,
    initialSpecId = "",
    initialStage = "",
}: {
    initialWorkspacePath: string;
    initialSpecId?: string;
    initialStage?: string;
}) {
    const t = useT();
    const normalizedInitialStage = STAGES.includes(initialStage.trim().toLowerCase())
        ? initialStage.trim().toLowerCase()
        : "requirements";
    const [workspacePath, setWorkspacePath] = useState(initialWorkspacePath);
    const [specs, setSpecs] = useState<SpecSummary[]>([]);
    const [selectedSpecId, setSelectedSpecId] = useState(initialSpecId);
    const [selectedStage, setSelectedStage] = useState(normalizedInitialStage);
    const [detail, setDetail] = useState<SpecDetail | null>(null);
    const [sectionRef, setSectionRef] = useState("");
    const [comment, setComment] = useState("");
    const [replacement, setReplacement] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");

    const selectedSpec = useMemo(
        () => specs.find((item) => item.specId === selectedSpecId) || detail?.spec || null,
        [detail?.spec, selectedSpecId, specs],
    );
    const stageContent = detail?.stages?.[selectedStage]?.content || "";
    const stageIds = detail?.stages?.[selectedStage]?.ids || selectedSpec?.documents?.[selectedStage]?.ids || [];
    const availableStages = useMemo(() => {
        const fromDetail = Object.keys(detail?.stages || {});
        const fromSummary = Object.keys(selectedSpec?.documents || {});
        return STAGES.filter((stage) => fromDetail.includes(stage) || fromSummary.includes(stage));
    }, [detail?.stages, selectedSpec?.documents]);

    const loadSpecs = useCallback(async () => {
        if (!workspacePath.trim()) {
            setError(t("web.specReview.workspaceRequired"));
            return;
        }
        setBusy(true);
        setError("");
        try {
            const query = new URLSearchParams({ workspace_path: workspacePath.trim(), include_archived: "false", limit: "120" });
            const payload = await readJson<{ specs?: SpecSummary[] }>(await fetch(`/api/specs?${query.toString()}`, { cache: "no-store" }), t("web.specReview.listLoadFailed"));
            const nextSpecs = Array.isArray(payload.specs) ? payload.specs : [];
            setSpecs(nextSpecs);
            const nextSelected = selectedSpecId || initialSpecId || nextSpecs[0]?.specId || "";
            setSelectedSpecId(nextSelected);
        } catch (err) {
            setError(err instanceof Error ? err.message : t("web.specReview.listLoadFailed"));
        } finally {
            setBusy(false);
        }
    }, [initialSpecId, selectedSpecId, t, workspacePath]);

    const loadSpecDetail = useCallback(async (specId: string) => {
        if (!workspacePath.trim() || !specId) {
            setDetail(null);
            return;
        }
        setBusy(true);
        setError("");
        try {
            const query = new URLSearchParams({ workspace_path: workspacePath.trim(), max_chars: "160000" });
            const payload = await readJson<SpecDetail>(await fetch(`/api/specs/${encodeURIComponent(specId)}?${query.toString()}`, { cache: "no-store" }), t("web.specReview.documentLoadFailed"));
            setDetail(payload);
            const preferredStage = STAGES.includes(initialStage.trim().toLowerCase())
                ? initialStage.trim().toLowerCase()
                : selectedStage;
            const firstStage = STAGES.find((stage) => payload.stages?.[stage]);
            if (payload.stages?.[preferredStage]) {
                setSelectedStage(preferredStage);
            } else if (firstStage && !payload.stages?.[selectedStage]) {
                setSelectedStage(firstStage);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : t("web.specReview.documentLoadFailed"));
        } finally {
            setBusy(false);
        }
    }, [initialStage, selectedStage, t, workspacePath]);

    useEffect(() => {
        if (initialWorkspacePath) {
            void loadSpecs();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (selectedSpecId) {
            void loadSpecDetail(selectedSpecId);
        }
    }, [loadSpecDetail, selectedSpecId]);

    const postStageAction = async (action: "approve" | "revise" | "edit") => {
        if (!selectedSpecId || !selectedStage) {
            setError(t("web.specReview.selectionRequired"));
            return;
        }
        setBusy(true);
        setError("");
        try {
            const body =
                action === "approve"
                    ? { workspacePath: workspacePath.trim(), comment }
                    : action === "revise"
                        ? { workspacePath: workspacePath.trim(), comment, sectionRef }
                        : {
                            workspacePath: workspacePath.trim(),
                            action: sectionRef ? "replace_section" : "append_section",
                            sectionRef,
                            content: replacement,
                            reason: comment || "web_spec_approval_edit",
                        };
            await readJson<Record<string, unknown>>(
                await fetch(`/api/specs/${encodeURIComponent(selectedSpecId)}/stages/${encodeURIComponent(selectedStage)}/${action}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                }),
                action === "approve" ? t("web.specReview.approveFailed") : action === "revise" ? t("web.specReview.reviseFailed") : t("web.specReview.editFailed"),
            );
            setComment("");
            setReplacement("");
            await loadSpecDetail(selectedSpecId);
            await loadSpecs();
        } catch (err) {
            setError(err instanceof Error ? err.message : t("web.specReview.actionFailed"));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex h-full min-h-0 w-full flex-col bg-zinc-50 text-zinc-950 dark:bg-zinc-950 dark:text-zinc-50">
            <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-zinc-200 bg-white/90 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/90">
                <div>
                    <div className="text-sm font-semibold uppercase tracking-[0.24em] text-rose-500">Spec Approval</div>
                    <div className="text-xs text-zinc-500">{t("web.specReview.subtitle")}</div>
                </div>
                <div className="ml-auto flex min-w-[280px] flex-1 items-center gap-2 md:max-w-[720px]">
                    <input
                        className="h-10 flex-1 rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={workspacePath}
                        onChange={(event) => setWorkspacePath(event.target.value)}
                        placeholder={t("web.specReview.workspacePlaceholder")}
                    />
                    <button
                        className="h-10 rounded-md bg-zinc-950 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-zinc-950"
                        disabled={busy}
                        onClick={() => void loadSpecs()}
                    >
                        {t("web.specReview.load")}
                    </button>
                </div>
            </header>

            {error ? <div className="shrink-0 border-b border-rose-200 bg-rose-50 px-5 py-2 text-sm text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200">{error}</div> : null}

            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)_360px]">
                <aside className="min-h-0 overflow-y-auto border-r border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
                    <div className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">Specs</div>
                    <div className="space-y-2">
                        {specs.map((spec) => {
                            const active = spec.specId === selectedSpecId;
                            return (
                                <button
                                    key={spec.specId}
                                    className={`w-full rounded-md border px-3 py-3 text-left transition ${active ? "border-rose-400 bg-rose-50 dark:bg-rose-950/30" : "border-zinc-200 bg-white hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900"}`}
                                    onClick={() => {
                                        setSelectedSpecId(spec.specId || "");
                                        setSectionRef("");
                                    }}
                                >
                                    <div className="line-clamp-2 text-sm font-semibold">{spec.featureName || spec.specId}</div>
                                    <div className="mt-2 flex flex-wrap gap-1 text-[11px] text-zinc-500">
                                        <span>{spec.currentStage || "unknown"}</span>
                                        <span>·</span>
                                        <span>{spec.pipelineControl?.runtimeExecutionAllowed ? t("web.specReview.executable") : t("web.specReview.inReview")}</span>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </aside>

                <main className="flex min-h-0 flex-col overflow-hidden">
                    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
                        {availableStages.map((stage) => (
                            <button
                                key={stage}
                                className={`rounded-full px-3 py-1.5 text-xs font-medium ${stage === selectedStage ? "bg-rose-500 text-white" : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"}`}
                                onClick={() => setSelectedStage(stage)}
                            >
                                {stage}
                            </button>
                        ))}
                        {detail?.stages?.[selectedStage]?.truncated ? <span className="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-800">{t("web.specReview.truncated")}</span> : null}
                    </div>
                    <article className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
                        <pre className="whitespace-pre-wrap break-words font-sans text-[15px] leading-7 text-zinc-900 dark:text-zinc-100">
                            {stageContent || t("web.specReview.chooseStage")}
                        </pre>
                    </article>
                </main>

                <aside className="min-h-0 overflow-y-auto border-l border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">Review</div>
                    <div className="mt-3 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                        <div className="font-semibold">{selectedSpec?.featureName || t("web.specReview.noSelection")}</div>
                        <div className="mt-2 text-xs text-zinc-500">{t("web.specReview.nextStage", { value: selectedSpec?.pipelineControl?.nextStage || "unknown" })}</div>
                        <div className="mt-1 text-xs text-zinc-500">{t("web.specReview.blockedBy", { value: selectedSpec?.pipelineControl?.blockedByApproval || t("web.specReview.none") })}</div>
                    </div>

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">{t("web.specReview.sectionId")}</label>
                    <input
                        className="mt-2 h-10 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={sectionRef}
                        onChange={(event) => setSectionRef(event.target.value)}
                        placeholder="REQ-001 / DES-001 / TASK-001"
                    />
                    {stageIds.length ? (
                        <div className="mt-2 flex flex-wrap gap-1">
                            {stageIds.slice(0, 36).map((id) => (
                                <button
                                    key={id}
                                    className={`rounded-full px-2 py-1 text-[11px] ${sectionRef === id ? "bg-rose-500 text-white" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}
                                    onClick={() => setSectionRef(id)}
                                >
                                    {id}
                                </button>
                            ))}
                        </div>
                    ) : null}

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">{t("web.specReview.comment")}</label>
                    <textarea
                        className="mt-2 min-h-28 w-full resize-y rounded-md border border-zinc-200 bg-white p-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        placeholder={t("web.specReview.commentPlaceholder")}
                    />

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">{t("web.specReview.replacement")}</label>
                    <textarea
                        className="mt-2 min-h-40 w-full resize-y rounded-md border border-zinc-200 bg-white p-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={replacement}
                        onChange={(event) => setReplacement(event.target.value)}
                        placeholder={t("web.specReview.replacementPlaceholder")}
                    />

                    <div className="mt-5 grid gap-2">
                        <button
                            className="h-11 rounded-md bg-emerald-600 text-sm font-semibold text-white disabled:opacity-50"
                            disabled={busy || !selectedSpecId}
                            onClick={() => void postStageAction("approve")}
                        >
                            {t("web.specReview.approve")}
                        </button>
                        <button
                            className="h-11 rounded-md border border-amber-300 bg-amber-50 text-sm font-semibold text-amber-800 disabled:opacity-50 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
                            disabled={busy || !selectedSpecId || !comment.trim()}
                            onClick={() => void postStageAction("revise")}
                        >
                            {t("web.specReview.revise")}
                        </button>
                        <button
                            className="h-11 rounded-md border border-zinc-300 text-sm font-semibold disabled:opacity-50 dark:border-zinc-700"
                            disabled={busy || !selectedSpecId || !replacement.trim()}
                            onClick={() => void postStageAction("edit")}
                        >
                            {t("web.specReview.edit")}
                        </button>
                    </div>
                </aside>
            </div>
        </div>
    );
}

export function SpecApprovalLoading() {
    const t = useT();
    return <div className="p-6 text-sm text-muted-foreground">{t("web.specReview.loading")}</div>;
}
