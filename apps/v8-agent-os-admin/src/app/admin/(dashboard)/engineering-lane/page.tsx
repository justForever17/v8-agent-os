"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Code2, Loader2, Play, RefreshCw, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type DiagnosticsProviders = {
    git: boolean;
    command: boolean;
    lspBestEffort: boolean;
};

type EngineeringLaneConfig = {
    enabled: boolean;
    triggerMode: "auto" | "force" | "off";
    contextPackBudget: number;
    evidenceGraphEnabled: boolean;
    evidenceGraphBudget: number;
    codingPlannerContractEnabled: boolean;
    worksetGovernanceMode: "soft_gate" | "read_only" | "off";
    maxCriticalFiles: number;
    proofLedgerEnabled: boolean;
    autoProofCollectionEnabled: boolean;
    proofCollectionScope: "engineering_active" | "force_only" | "off";
    diagnosticsProviders: DiagnosticsProviders;
    worksetRiskMode: "soft_gate" | "read_only" | "off";
    suppressDailyMemory: boolean;
    suppressMemoryMap: boolean;
    rankedWorkflowPathCount: number;
};

type DiagnosticItem = {
    source?: string;
    kind?: string;
    command?: string;
    tool?: string;
    returnCode?: number | null;
    summary?: string;
    severity?: string;
    rawPreview?: string;
    truncated?: boolean;
};

type ProofEntry = {
    id: string;
    sessionId?: string;
    runId?: string;
    mode?: string;
    patchIntent?: string;
    verificationStatus?: string;
    readSet?: string[];
    writeSet?: string[];
    changedFiles?: string[];
    commands?: Array<Record<string, unknown>>;
    diagnostics?: {
        items?: DiagnosticItem[];
        gitSummary?: Record<string, unknown>;
        lspProvider?: Record<string, unknown>;
        worksetRisk?: Record<string, unknown>;
        contextPackDigest?: Record<string, unknown>;
    };
    residualRisks?: string[];
    metadata?: Record<string, unknown>;
    createdAt?: string;
};

const DEFAULT_CONFIG: EngineeringLaneConfig = {
    enabled: true,
    triggerMode: "auto",
    contextPackBudget: 2400,
    evidenceGraphEnabled: true,
    evidenceGraphBudget: 1800,
    codingPlannerContractEnabled: true,
    worksetGovernanceMode: "soft_gate",
    maxCriticalFiles: 24,
    proofLedgerEnabled: true,
    autoProofCollectionEnabled: true,
    proofCollectionScope: "engineering_active",
    diagnosticsProviders: { git: true, command: true, lspBestEffort: true },
    worksetRiskMode: "read_only",
    suppressDailyMemory: true,
    suppressMemoryMap: true,
    rankedWorkflowPathCount: 3,
};

function asConfig(value: unknown): EngineeringLaneConfig {
    const raw = (value && typeof value === "object" ? value : {}) as Partial<EngineeringLaneConfig>;
    const providers = (raw.diagnosticsProviders || {}) as Partial<DiagnosticsProviders>;
    return {
        ...DEFAULT_CONFIG,
        ...raw,
        triggerMode: raw.triggerMode === "force" || raw.triggerMode === "off" ? raw.triggerMode : "auto",
        proofCollectionScope:
            raw.proofCollectionScope === "force_only" || raw.proofCollectionScope === "off"
                ? raw.proofCollectionScope
                : "engineering_active",
        worksetRiskMode: raw.worksetRiskMode === "off" ? "off" : (raw.worksetRiskMode === "soft_gate" ? "soft_gate" : "read_only"),
        worksetGovernanceMode:
            raw.worksetGovernanceMode === "off" || raw.worksetGovernanceMode === "read_only"
                ? raw.worksetGovernanceMode
                : "soft_gate",
        diagnosticsProviders: {
            git: providers.git ?? true,
            command: providers.command ?? true,
            lspBestEffort: providers.lspBestEffort ?? true,
        },
        contextPackBudget: Number(raw.contextPackBudget || DEFAULT_CONFIG.contextPackBudget),
        evidenceGraphBudget: Number(raw.evidenceGraphBudget || DEFAULT_CONFIG.evidenceGraphBudget),
        maxCriticalFiles: Number(raw.maxCriticalFiles || DEFAULT_CONFIG.maxCriticalFiles),
        rankedWorkflowPathCount: Number(raw.rankedWorkflowPathCount || DEFAULT_CONFIG.rankedWorkflowPathCount),
    };
}

function JsonDebug({ value }: { value: unknown }) {
    return (
        <details className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <summary className="cursor-pointer text-xs font-medium uppercase tracking-[0.18em] text-slate-500">JSON</summary>
            <pre className="mt-3 max-h-[360px] overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100">
                {JSON.stringify(value ?? {}, null, 2)}
            </pre>
        </details>
    );
}

function StatusPill({ value }: { value?: string }) {
    const normalized = String(value || "planned");
    const palette =
        normalized === "verified"
            ? "bg-emerald-100 text-emerald-700"
            : normalized === "failed_verification"
              ? "bg-rose-100 text-rose-700"
              : normalized === "unverified"
                ? "bg-amber-100 text-amber-700"
                : "bg-slate-100 text-slate-600";
    return <span className={`rounded-full px-3 py-1 text-xs font-medium ${palette}`}>{normalized}</span>;
}

function FieldList({ items, empty }: { items?: string[]; empty: string }) {
    if (!items?.length) {
        return <p className="text-sm text-slate-500">{empty}</p>;
    }
    return (
        <div className="flex flex-wrap gap-2">
            {items.slice(0, 24).map((item) => (
                <span key={item} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">
                    {item}
                </span>
            ))}
        </div>
    );
}

export default function EngineeringLanePage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<EngineeringLaneConfig> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [running, setRunning] = useState(false);
    const [proofLoading, setProofLoading] = useState(true);
    const [refreshingProof, setRefreshingProof] = useState(false);
    const [dryRunText, setDryRunText] = useState("修复 admin 页面抖动并补充回归测试");
    const [dryRunMode, setDryRunMode] = useState<"auto" | "force" | "off">("auto");
    const [dryRunResult, setDryRunResult] = useState<Record<string, unknown> | null>(null);
    const [proofEntries, setProofEntries] = useState<ProofEntry[]>([]);
    const [selectedProofId, setSelectedProofId] = useState<string>("");
    const [proofStatusFilter, setProofStatusFilter] = useState("all");
    const [proofSessionFilter, setProofSessionFilter] = useState("");
    const [proofRunFilter, setProofRunFilter] = useState("");

    const config = useMemo(() => asConfig(envelope?.data), [envelope]);
    const selectedProof = proofEntries.find((entry) => entry.id === selectedProofId) || proofEntries[0] || null;

    const load = async () => {
        setLoading(true);
        try {
            const next = await fetchConfigDomain<EngineeringLaneConfig>("engineering-lane");
            setEnvelope({ ...next, data: asConfig(next.data) });
        } finally {
            setLoading(false);
        }
    };

    const loadProof = async () => {
        setProofLoading(true);
        try {
            const params = new URLSearchParams();
            params.set("limit", "30");
            if (proofStatusFilter !== "all") params.set("status", proofStatusFilter);
            if (proofSessionFilter.trim()) params.set("sessionId", proofSessionFilter.trim());
            if (proofRunFilter.trim()) params.set("runId", proofRunFilter.trim());
            const response = await fetch(`/api/engineering-lane/proof-ledger?${params.toString()}`, { cache: "no-store" });
            const data = await response.json().catch(() => ({}));
            const items = Array.isArray(data.items) ? data.items : [];
            setProofEntries(items);
            setSelectedProofId((current) => (items.some((item: ProofEntry) => item.id === current) ? current : items[0]?.id || ""));
        } finally {
            setProofLoading(false);
        }
    };

    useEffect(() => {
        void Promise.all([load(), loadProof()]);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const patchConfig = (patch: Partial<EngineeringLaneConfig>) => {
        if (!envelope) return;
        setEnvelope({ ...envelope, data: { ...config, ...patch } });
    };

    const save = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<EngineeringLaneConfig>("engineering-lane", { data: config });
            setEnvelope({ ...next, data: asConfig(next.data) });
        } finally {
            setSaving(false);
        }
    };

    const runDryRun = async () => {
        setRunning(true);
        try {
            const response = await fetch("/api/engineering-lane/dry-run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ userQuery: dryRunText, engineeringMode: dryRunMode }),
            });
            setDryRunResult(await response.json().catch(() => ({})));
        } finally {
            setRunning(false);
        }
    };

    const refreshSelectedProof = async () => {
        if (!selectedProof?.sessionId || !selectedProof.runId) return;
        setRefreshingProof(true);
        try {
            await fetch("/api/engineering-lane/proof-ledger/refresh", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sessionId: selectedProof.sessionId, runId: selectedProof.runId }),
            });
            await loadProof();
        } finally {
            setRefreshingProof(false);
        }
    };

    const triggerDecision = (dryRunResult?.triggerDecision || {}) as Record<string, unknown>;
    const contextPack = (dryRunResult?.contextPack || {}) as Record<string, unknown>;
    const evidenceGraph = ((dryRunResult?.evidenceGraphDigest || contextPack.evidenceGraphDigest || {}) as Record<string, unknown>);
    const codingPlanner = ((dryRunResult?.codingPlannerContractPreview || contextPack.codingPlannerContractPreview || {}) as Record<string, unknown>);
    const dryRunSoftGate = ((dryRunResult?.worksetSoftGateDecision || contextPack.worksetSoftGateDecision || {}) as Record<string, unknown>);
    const repoBrief = (contextPack.repoBrief || {}) as Record<string, unknown>;
    const gitSummary = (contextPack.gitSummary || {}) as Record<string, unknown>;
    const memorySuppression = (contextPack.memorySuppression || {}) as Record<string, unknown>;
    const rankedPaths = Array.isArray((contextPack as { workflowRankedPaths?: unknown[] }).workflowRankedPaths)
        ? ((contextPack as { workflowRankedPaths?: unknown[] }).workflowRankedPaths || [])
        : [];
    const diagnostics = selectedProof?.diagnostics?.items || [];
    const worksetRisk = selectedProof?.diagnostics?.worksetRisk || {};

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.engineeringLane.title"
                description="app.admin.dashboard.engineeringLane.description"
                badges={["app.admin.dashboard.engineeringLane.hybridOverlay"]}
                actions={
                    <Button onClick={save} disabled={saving || loading}>
                        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.engineeringLane.save")}
                    </Button>
                }
            />

            <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
                <ConfigCard
                    title="app.admin.dashboard.engineeringLane.configTitle"
                    description="app.admin.dashboard.engineeringLane.configDescription"
                    bodyHeight="auto"
                    footer={envelope ? <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={Boolean(envelope.reloadRequired)} /> : null}
                >
                    {loading ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            {t("app.admin.dashboard.engineeringLane.loading")}
                        </div>
                    ) : (
                        <div className="space-y-5">
                            <div className="flex items-center justify-between rounded-xl border border-slate-200 p-4">
                                <div>
                                    <Label>{t("app.admin.dashboard.engineeringLane.enabled")}</Label>
                                    <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.enabledHint")}</p>
                                </div>
                                <Switch checked={config.enabled} onCheckedChange={(enabled) => patchConfig({ enabled })} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.triggerMode")}</Label>
                                <Select value={config.triggerMode} onValueChange={(value) => patchConfig({ triggerMode: value as EngineeringLaneConfig["triggerMode"] })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="auto">auto</SelectItem>
                                        <SelectItem value="force">force</SelectItem>
                                        <SelectItem value="off">off</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.contextBudget")}</Label>
                                <Input type="number" value={config.contextPackBudget} onChange={(event) => patchConfig({ contextPackBudget: Number(event.target.value) })} />
                            </div>
                            <div className="grid gap-3">
                                {[
                                    ["evidenceGraphEnabled", "evidenceGraphEnabled"],
                                    ["codingPlannerContractEnabled", "codingPlannerContractEnabled"],
                                ].map(([key, label]) => (
                                    <div key={key} className="flex items-center justify-between rounded-xl border border-slate-200 p-3">
                                        <Label>{t(`app.admin.dashboard.engineeringLane.${label}`)}</Label>
                                        <Switch checked={Boolean(config[key as keyof EngineeringLaneConfig])} onCheckedChange={(value) => patchConfig({ [key]: value } as Partial<EngineeringLaneConfig>)} />
                                    </div>
                                ))}
                            </div>
                            <div className="grid gap-3 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.engineeringLane.evidenceGraphBudget")}</Label>
                                    <Input type="number" value={config.evidenceGraphBudget} onChange={(event) => patchConfig({ evidenceGraphBudget: Number(event.target.value) })} />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.engineeringLane.maxCriticalFiles")}</Label>
                                    <Input type="number" value={config.maxCriticalFiles} onChange={(event) => patchConfig({ maxCriticalFiles: Number(event.target.value) })} />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.worksetGovernanceMode")}</Label>
                                <Select value={config.worksetGovernanceMode} onValueChange={(value) => patchConfig({ worksetGovernanceMode: value as EngineeringLaneConfig["worksetGovernanceMode"], worksetRiskMode: value as EngineeringLaneConfig["worksetRiskMode"] })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="soft_gate">soft_gate</SelectItem>
                                        <SelectItem value="read_only">read_only</SelectItem>
                                        <SelectItem value="off">off</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.rankedPathCount")}</Label>
                                <Input type="number" min={1} max={5} value={config.rankedWorkflowPathCount} onChange={(event) => patchConfig({ rankedWorkflowPathCount: Number(event.target.value) })} />
                            </div>
                            <div className="grid gap-3">
                                {[
                                    ["proofLedgerEnabled", "proofLedger"],
                                    ["autoProofCollectionEnabled", "autoProofCollection"],
                                    ["suppressDailyMemory", "suppressDaily"],
                                    ["suppressMemoryMap", "suppressMap"],
                                ].map(([key, label]) => (
                                    <div key={key} className="flex items-center justify-between rounded-xl border border-slate-200 p-3">
                                        <Label>{t(`app.admin.dashboard.engineeringLane.${label}`)}</Label>
                                        <Switch checked={Boolean(config[key as keyof EngineeringLaneConfig])} onCheckedChange={(value) => patchConfig({ [key]: value } as Partial<EngineeringLaneConfig>)} />
                                    </div>
                                ))}
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.proofScope")}</Label>
                                <Select value={config.proofCollectionScope} onValueChange={(value) => patchConfig({ proofCollectionScope: value as EngineeringLaneConfig["proofCollectionScope"] })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="engineering_active">engineering_active</SelectItem>
                                        <SelectItem value="force_only">force_only</SelectItem>
                                        <SelectItem value="off">off</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.engineeringLane.diagnosticsProviders")}</Label>
                                <div className="grid gap-2 text-sm">
                                    {(["git", "command", "lspBestEffort"] as const).map((key) => (
                                        <div key={key} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
                                            <span>{key}</span>
                                            <Switch
                                                checked={config.diagnosticsProviders[key]}
                                                onCheckedChange={(value) => patchConfig({ diagnosticsProviders: { ...config.diagnosticsProviders, [key]: value } })}
                                            />
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}
                </ConfigCard>

                <div className="space-y-6">
                    <ConfigCard title="app.admin.dashboard.engineeringLane.contextPackTitle" description="app.admin.dashboard.engineeringLane.contextPackDescription" bodyScroll="auto" bodyHeight={520}>
                        <div className="space-y-4">
                            <div className="grid gap-3 md:grid-cols-[1fr_160px]">
                                <Textarea className="min-h-[112px]" value={dryRunText} onChange={(event) => setDryRunText(event.target.value)} />
                                <div className="space-y-3">
                                    <Select value={dryRunMode} onValueChange={(value) => setDryRunMode(value as "auto" | "force" | "off")}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="auto">auto</SelectItem>
                                            <SelectItem value="force">force</SelectItem>
                                            <SelectItem value="off">off</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Button className="w-full" onClick={runDryRun} disabled={running || !dryRunText.trim()}>
                                        {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                                        {t("app.admin.dashboard.engineeringLane.runDryRun")}
                                    </Button>
                                </div>
                            </div>

                            {dryRunResult ? (
                                <div className="space-y-4">
                                    <div className="grid gap-3 md:grid-cols-4">
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">trigger</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{String(triggerDecision.active ?? false)}</div>
                                            <p className="mt-1 text-xs text-slate-500">{String(triggerDecision.reason || "")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">repo</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{repoBrief.repoDetected ? "yes" : "no"}</div>
                                            <p className="mt-1 truncate text-xs text-slate-500">{String(repoBrief.repoRoot || repoBrief.workspaceRoot || "")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">tokens</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{String(dryRunResult.contextPackEstimatedTokens || 0)}</div>
                                            <p className="mt-1 text-xs text-slate-500">budget {String(dryRunResult.contextPackBudget || "-")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">workflow paths</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{rankedPaths.length}</div>
                                            <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.workflowHintKept")}</p>
                                        </div>
                                    </div>
                                    <div className="grid gap-4 lg:grid-cols-2">
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.evidenceGraphTitle")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>repo: {String(evidenceGraph.repoDetected ?? false)}</span>
                                                <span>branch: {String(evidenceGraph.branch || "-")}</span>
                                                <span>files: {String(((evidenceGraph.fileInventoryDigest as Record<string, unknown> | undefined)?.totalFiles) || 0)}</span>
                                                <span>changed: {String(((evidenceGraph.dirtyState as Record<string, unknown> | undefined)?.changedFileCount) || 0)}</span>
                                            </div>
                                            <FieldList
                                                items={(Array.isArray(evidenceGraph.criticalFileCandidates) ? evidenceGraph.criticalFileCandidates : [])
                                                    .map((item) => (item && typeof item === "object" ? String((item as Record<string, unknown>).path || "") : ""))
                                                    .filter(Boolean)
                                                    .slice(0, 10)}
                                                empty={t("app.admin.dashboard.engineeringLane.none")}
                                            />
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.codingPlannerTitle")}</h3>
                                            <div className="mt-3 space-y-3 text-sm text-slate-600">
                                                <div>
                                                    <div className="mb-1 text-xs uppercase tracking-[0.18em] text-slate-400">writeSet</div>
                                                    <FieldList items={(Array.isArray(codingPlanner.writeSet) ? codingPlanner.writeSet : []).map(String)} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                                </div>
                                                <div>
                                                    <div className="mb-1 text-xs uppercase tracking-[0.18em] text-slate-400">verification</div>
                                                    <FieldList
                                                        items={(Array.isArray(codingPlanner.verificationMatrix) ? codingPlanner.verificationMatrix : [])
                                                            .map((item) => (item && typeof item === "object" ? String((item as Record<string, unknown>).command || (item as Record<string, unknown>).kind || "") : String(item)))
                                                            .filter(Boolean)}
                                                        empty={t("app.admin.dashboard.engineeringLane.none")}
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="grid gap-4 lg:grid-cols-2">
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.gitEvidence")}</h3>
                                            <pre className="mt-3 max-h-[180px] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                                                {String(gitSummary.statusShort || gitSummary.diffStat || gitSummary.stagedDiffStat || t("app.admin.dashboard.engineeringLane.noGitEvidence"))}
                                            </pre>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.memorySuppression")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>daily: {String(memorySuppression.suppressDailyMemory ?? false)}</span>
                                                <span>map: {String(memorySuppression.suppressMemoryMap ?? false)}</span>
                                                <span>workflow: {String(memorySuppression.workflowHintsRetained ?? true)}</span>
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.softGateTitle")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>risk: {String(dryRunSoftGate.risk || "not_evaluated")}</span>
                                                <span>warning: {String(dryRunSoftGate.warning ?? false)}</span>
                                                <span>{String(dryRunSoftGate.suggestedAction || "")}</span>
                                            </div>
                                            <FieldList items={(Array.isArray(dryRunSoftGate.outsideWriteSet) ? dryRunSoftGate.outsideWriteSet : []).map(String)} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                        </div>
                                    </div>
                                    <JsonDebug value={dryRunResult} />
                                </div>
                            ) : (
                                <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                    <Code2 className="mb-3 h-5 w-5" />
                                    {t("app.admin.dashboard.engineeringLane.noDryRun")}
                                </div>
                            )}
                        </div>
                    </ConfigCard>

                    <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
                        <ConfigCard title="app.admin.dashboard.engineeringLane.proofTitle" description="app.admin.dashboard.engineeringLane.proofDescription" bodyScroll="auto" bodyHeight={520}>
                            <div className="space-y-4">
                                <div className="grid gap-2">
                                    <Select value={proofStatusFilter} onValueChange={setProofStatusFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all</SelectItem>
                                            <SelectItem value="verified">verified</SelectItem>
                                            <SelectItem value="unverified">unverified</SelectItem>
                                            <SelectItem value="failed_verification">failed_verification</SelectItem>
                                            <SelectItem value="planned">planned</SelectItem>
                                            <SelectItem value="observed_no_change">observed_no_change</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.sessionFilter")} value={proofSessionFilter} onChange={(event) => setProofSessionFilter(event.target.value)} />
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.runFilter")} value={proofRunFilter} onChange={(event) => setProofRunFilter(event.target.value)} />
                                    <Button variant="outline" onClick={loadProof} disabled={proofLoading}>
                                        {proofLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                        {t("app.admin.dashboard.engineeringLane.refreshList")}
                                    </Button>
                                </div>
                                {proofEntries.length ? (
                                    <div className="grid gap-3">
                                        {proofEntries.map((entry) => (
                                            <button
                                                key={entry.id}
                                                type="button"
                                                onClick={() => setSelectedProofId(entry.id)}
                                                className={`rounded-xl border p-4 text-left transition ${selectedProof?.id === entry.id ? "border-slate-900 bg-slate-50" : "border-slate-200 hover:border-slate-300"}`}
                                            >
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-medium text-slate-900">{entry.patchIntent || entry.id}</div>
                                                        <div className="mt-1 truncate text-xs text-slate-500">{entry.runId || entry.createdAt || ""}</div>
                                                    </div>
                                                    <StatusPill value={entry.verificationStatus} />
                                                </div>
                                                {entry.changedFiles?.length ? <p className="mt-2 truncate text-xs text-slate-500">{entry.changedFiles.slice(0, 4).join(", ")}</p> : null}
                                            </button>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                        {t("app.admin.dashboard.engineeringLane.noProof")}
                                    </div>
                                )}
                            </div>
                        </ConfigCard>

                        <div className="grid gap-6">
                            <ConfigCard title="app.admin.dashboard.engineeringLane.proofDetailTitle" description="app.admin.dashboard.engineeringLane.proofDetailDescription" bodyScroll="auto" bodyHeight={360}>
                                {selectedProof ? (
                                    <div className="space-y-4">
                                        <div className="flex flex-wrap items-center justify-between gap-3">
                                            <div>
                                                <h3 className="text-base font-semibold text-slate-900">{selectedProof.patchIntent || selectedProof.id}</h3>
                                                <p className="mt-1 text-xs text-slate-500">{selectedProof.sessionId || "-"} · {selectedProof.runId || "-"}</p>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <StatusPill value={selectedProof.verificationStatus} />
                                                <Button variant="outline" size="sm" onClick={refreshSelectedProof} disabled={refreshingProof || !selectedProof.sessionId || !selectedProof.runId}>
                                                    {refreshingProof ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                                    {t("app.admin.dashboard.engineeringLane.refreshProof")}
                                                </Button>
                                            </div>
                                        </div>
                                        <div className="grid gap-4 lg:grid-cols-2">
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.changedFiles")}</h4>
                                                <FieldList items={selectedProof.changedFiles} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.residualRisks")}</h4>
                                                {selectedProof.residualRisks?.length ? (
                                                    <div className="space-y-2">
                                                        {selectedProof.residualRisks.map((risk) => (
                                                            <div key={risk} className="flex gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
                                                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                                <span>{risk}</span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <p className="text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.none")}</p>
                                                )}
                                            </div>
                                        </div>
                                        <JsonDebug value={selectedProof} />
                                    </div>
                                ) : (
                                    <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                                )}
                            </ConfigCard>

                            <div className="grid gap-6 xl:grid-cols-2">
                                <ConfigCard title="app.admin.dashboard.engineeringLane.diagnosticsTitle" description="app.admin.dashboard.engineeringLane.diagnosticsDescription" bodyScroll="auto" bodyHeight={360}>
                                    {diagnostics.length ? (
                                        <div className="space-y-3">
                                            {diagnostics.map((item, index) => (
                                                <div key={`${item.source}-${item.kind}-${index}`} className="rounded-xl border border-slate-200 p-3">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{item.source} · {item.kind}</span>
                                                        {item.returnCode !== undefined ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">rc={String(item.returnCode)}</span> : null}
                                                    </div>
                                                    <p className="mt-2 text-sm text-slate-700">{item.summary}</p>
                                                    {item.command ? <p className="mt-1 truncate text-xs text-slate-500">{item.command}</p> : null}
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noDiagnostics")}</div>
                                    )}
                                </ConfigCard>

                                <ConfigCard title="app.admin.dashboard.engineeringLane.worksetTitle" description="app.admin.dashboard.engineeringLane.worksetDescription" bodyScroll="auto" bodyHeight={360}>
                                    {selectedProof ? (
                                        <div className="space-y-4">
                                            <div className="rounded-xl border border-slate-200 p-4">
                                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">risk</div>
                                                <div className="mt-1 text-lg font-semibold text-slate-900">{String(worksetRisk.risk || "not_evaluated")}</div>
                                                {worksetRisk.note ? <p className="mt-1 text-xs text-slate-500">{String(worksetRisk.note)}</p> : null}
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">readSet</h4>
                                                <FieldList items={selectedProof.readSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">writeSet</h4>
                                                <FieldList items={selectedProof.writeSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <JsonDebug value={worksetRisk} />
                                        </div>
                                    ) : (
                                        <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                                    )}
                                </ConfigCard>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </AdminPageShell>
    );
}
