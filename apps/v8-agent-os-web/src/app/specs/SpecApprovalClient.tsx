"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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
            setError("请先输入当前工作区路径。");
            return;
        }
        setBusy(true);
        setError("");
        try {
            const query = new URLSearchParams({ workspace_path: workspacePath.trim(), include_archived: "false", limit: "120" });
            const payload = await readJson<{ specs?: SpecSummary[] }>(await fetch(`/api/specs?${query.toString()}`, { cache: "no-store" }), "无法读取 Spec 列表");
            const nextSpecs = Array.isArray(payload.specs) ? payload.specs : [];
            setSpecs(nextSpecs);
            const nextSelected = selectedSpecId || initialSpecId || nextSpecs[0]?.specId || "";
            setSelectedSpecId(nextSelected);
        } catch (err) {
            setError(err instanceof Error ? err.message : "无法读取 Spec 列表");
        } finally {
            setBusy(false);
        }
    }, [initialSpecId, selectedSpecId, workspacePath]);

    const loadSpecDetail = useCallback(async (specId: string) => {
        if (!workspacePath.trim() || !specId) {
            setDetail(null);
            return;
        }
        setBusy(true);
        setError("");
        try {
            const query = new URLSearchParams({ workspace_path: workspacePath.trim(), max_chars: "160000" });
            const payload = await readJson<SpecDetail>(await fetch(`/api/specs/${encodeURIComponent(specId)}?${query.toString()}`, { cache: "no-store" }), "无法读取 Spec 文档");
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
            setError(err instanceof Error ? err.message : "无法读取 Spec 文档");
        } finally {
            setBusy(false);
        }
    }, [initialStage, selectedStage, workspacePath]);

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
            setError("请先选择 Spec 和阶段。");
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
                action === "approve" ? "批准失败" : action === "revise" ? "打回失败" : "编辑失败",
            );
            setComment("");
            setReplacement("");
            await loadSpecDetail(selectedSpecId);
            await loadSpecs();
        } catch (err) {
            setError(err instanceof Error ? err.message : "操作失败");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex h-full min-h-0 w-full flex-col bg-zinc-50 text-zinc-950 dark:bg-zinc-950 dark:text-zinc-50">
            <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-zinc-200 bg-white/90 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/90">
                <div>
                    <div className="text-sm font-semibold uppercase tracking-[0.24em] text-rose-500">Spec Approval</div>
                    <div className="text-xs text-zinc-500">长文档审批、段落批注、局部替换和阶段批准</div>
                </div>
                <div className="ml-auto flex min-w-[280px] flex-1 items-center gap-2 md:max-w-[720px]">
                    <input
                        className="h-10 flex-1 rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={workspacePath}
                        onChange={(event) => setWorkspacePath(event.target.value)}
                        placeholder="输入工作区路径，例如 E:\\Projects\\test3"
                    />
                    <button
                        className="h-10 rounded-md bg-zinc-950 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-zinc-950"
                        disabled={busy}
                        onClick={() => void loadSpecs()}
                    >
                        读取
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
                                        <span>{spec.pipelineControl?.runtimeExecutionAllowed ? "可执行" : "审批中"}</span>
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
                        {detail?.stages?.[selectedStage]?.truncated ? <span className="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-800">内容已截断，可按段落续读</span> : null}
                    </div>
                    <article className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
                        <pre className="whitespace-pre-wrap break-words font-sans text-[15px] leading-7 text-zinc-900 dark:text-zinc-100">
                            {stageContent || "请选择一个 Spec 阶段。"}
                        </pre>
                    </article>
                </main>

                <aside className="min-h-0 overflow-y-auto border-l border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">Review</div>
                    <div className="mt-3 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                        <div className="font-semibold">{selectedSpec?.featureName || "未选择 Spec"}</div>
                        <div className="mt-2 text-xs text-zinc-500">下一步：{selectedSpec?.pipelineControl?.nextStage || "unknown"}</div>
                        <div className="mt-1 text-xs text-zinc-500">阻塞：{selectedSpec?.pipelineControl?.blockedByApproval || "无"}</div>
                    </div>

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">段落 ID</label>
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

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">批注 / 修改原因</label>
                    <textarea
                        className="mt-2 min-h-28 w-full resize-y rounded-md border border-zinc-200 bg-white p-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        placeholder="说明为什么批准、打回或局部替换。"
                    />

                    <label className="mt-5 block text-xs font-semibold text-zinc-500">局部替换 / 追加内容</label>
                    <textarea
                        className="mt-2 min-h-40 w-full resize-y rounded-md border border-zinc-200 bg-white p-3 text-sm outline-none focus:border-rose-400 dark:border-zinc-800 dark:bg-zinc-900"
                        value={replacement}
                        onChange={(event) => setReplacement(event.target.value)}
                        placeholder="填写后可按段落 ID 替换；不填段落 ID 时追加到当前阶段末尾。"
                    />

                    <div className="mt-5 grid gap-2">
                        <button
                            className="h-11 rounded-md bg-emerald-600 text-sm font-semibold text-white disabled:opacity-50"
                            disabled={busy || !selectedSpecId}
                            onClick={() => void postStageAction("approve")}
                        >
                            批准进入下一阶段
                        </button>
                        <button
                            className="h-11 rounded-md border border-amber-300 bg-amber-50 text-sm font-semibold text-amber-800 disabled:opacity-50 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
                            disabled={busy || !selectedSpecId || !comment.trim()}
                            onClick={() => void postStageAction("revise")}
                        >
                            打回重写
                        </button>
                        <button
                            className="h-11 rounded-md border border-zinc-300 text-sm font-semibold disabled:opacity-50 dark:border-zinc-700"
                            disabled={busy || !selectedSpecId || !replacement.trim()}
                            onClick={() => void postStageAction("edit")}
                        >
                            局部替换 / 追加
                        </button>
                    </div>
                </aside>
            </div>
        </div>
    );
}
