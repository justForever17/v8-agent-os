"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import {
    Activity,
    Check,
    ChevronRight,
    CircleAlert,
    Download,
    ExternalLink,
    KeyRound,
    Loader2,
    RefreshCw,
    Search,
    Settings2,
    ShieldCheck,
    SquareTerminal,
    Trash2,
} from "lucide-react";

import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type InstallState = {
    installed: boolean;
    state: string;
    configured: boolean;
    online: boolean;
    externalOwnership?: boolean;
};

type Plugin = {
    id: string;
    displayName: string;
    publisher: string;
    category: string;
    description: string;
    capabilities: string[];
    officialLinks: { homepage: string; documentation: string; repository?: string };
    componentCounts: { cli: number; skills: number; mcp: number; uiAdapters: number; providerAdapters?: number };
    governance: { sideEffects: string[]; approvalClasses: string[]; paidOperations: boolean; workspaceAccess: string };
    installation: InstallState;
};

type MachineComponent = {
    componentId: string;
    state: "registered" | "detected" | "partial" | "missing" | "unknown" | "conflict";
    action: "keep" | "adopt" | "complete" | "install" | "review";
    commands?: string[];
    detectedNames?: string[];
    missingNames?: string[];
    conflicts?: string[];
};
type MachineDiscovery = {
    pluginId: string;
    skillsCli: { available: boolean; package: string; error?: string };
    cli: MachineComponent[];
    skills: MachineComponent[];
    ordinaryMcp: Array<{ componentId: string; serverName: string; enabled: boolean; managedBy: "extensions_runtime" }>;
    summary: { detected: number; needsCompletion: number; conflicts: number; ordinaryMcp: number };
};

type RequirementKind = "secret" | "text" | "url" | "enum" | "boolean" | "oauth" | "file";
type Requirement = {
    id: string;
    kind: RequirementKind;
    required: boolean;
    source: "manifest" | "mcp_schema" | "cli_adapter" | "hint";
    confidence: "authoritative" | "reviewed" | "hint";
    labelKey: string;
    helpKey?: string;
    options?: string[];
    targetName?: string;
    componentId?: string;
    status: "configured" | "missing" | "unknown";
    configured: boolean;
    availableForImport?: boolean;
    discovery?: Array<{ sourceId: string; kind: string; present: boolean; displayPath?: string }>;
};

type RequirementResponse = { pluginId: string; configured: boolean; requirements: Requirement[] };
type Job = {
    jobId: string;
    pluginId: string;
    state: string;
    dryRun: boolean;
    planDigest?: string;
    externalReconciliation?: boolean;
    plan?: {
        approvalRequired?: boolean;
        sideEffects?: string[];
        machineDiscovery?: MachineDiscovery;
        steps?: {
            cli?: Array<{ componentId: string; action?: string; requiresElevation?: boolean; mayRestart?: boolean }>;
            skills?: Array<{ id: string; action?: string; detectedNames?: string[]; missingNames?: string[] }>;
        };
    };
    error?: string;
    createdAt?: string;
};
type Grant = { grantId: string; pluginId: string; scope: "task" | "session"; sessionId: string; runId?: string; componentIds: string[]; granteeType: string; granteeId: string };
type PluginEvent = { id: string; plugin_id?: string; event_type: string; status: string; created_at: string };
type OAuthState = { componentId: string; status: string; error?: string };
type Tab = "store" | "installed" | "grants" | "jobs" | "logs";

const TERMINAL_JOBS = new Set(["planned", "ready", "rolled_back", "rollback_failed", "external_reconciliation_required", "failed", "completed"]);
const ACTIVE_OAUTH = new Set(["connecting", "waiting_for_browser", "exchanging_token"]);
const TABS: Array<{ id: Tab; labelKey: string }> = [
    { id: "store", labelKey: "components.plugins.PluginManagerWorkbench.tab.store" },
    { id: "installed", labelKey: "components.plugins.PluginManagerWorkbench.tab.installed" },
    { id: "grants", labelKey: "components.plugins.PluginManagerWorkbench.tab.grants" },
    { id: "jobs", labelKey: "components.plugins.PluginManagerWorkbench.tab.jobs" },
    { id: "logs", labelKey: "components.plugins.PluginManagerWorkbench.tab.logs" },
];

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, { cache: "no-store", ...init });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = data?.detail;
        throw new Error(detail?.message || data?.error || `Request failed (${response.status})`);
    }
    return data as T;
}

function stateLabel(state: InstallState) {
    if (!state.installed) return { key: "components.plugins.PluginManagerWorkbench.state.notInstalled", tone: "muted" };
    if (!state.configured) return { key: "components.plugins.PluginManagerWorkbench.state.needsConfig", tone: "warn" };
    if (!state.online) return { key: "components.plugins.PluginManagerWorkbench.state.offline", tone: "warn" };
    return { key: "components.plugins.PluginManagerWorkbench.state.ready", tone: "ok" };
}

function StatusDot({ tone }: { tone: string }) {
    return <span aria-hidden className={cn("size-1.5 rounded-full", tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-amber-500" : "bg-muted-foreground/40")} />;
}

export function PluginManagerWorkbench() {
    const t = useT();
    const [requestedPluginId] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("plugin")?.trim().toLowerCase() || "");
    const [plugins, setPlugins] = useState<Plugin[]>([]);
    const [grants, setGrants] = useState<Grant[]>([]);
    const [jobs, setJobs] = useState<Job[]>([]);
    const [events, setEvents] = useState<PluginEvent[]>([]);
    const [requirements, setRequirements] = useState<RequirementResponse | null>(null);
    const [values, setValues] = useState<Record<string, string | boolean>>({});
    const [oauth, setOauth] = useState<OAuthState | null>(null);
    const [tab, setTab] = useState<Tab>("store");
    const [query, setQuery] = useState("");
    const [selectedId, setSelectedId] = useState("");
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [previewJob, setPreviewJob] = useState<Job | null>(null);
    const [machineDiscovery, setMachineDiscovery] = useState<MachineDiscovery | null>(null);

    const load = useCallback(async (refresh = false) => {
        setError("");
        try {
            const [catalogData, grantsData, jobsData, eventsData] = await Promise.all([
                jsonRequest<{ plugins: Plugin[] }>(`/api/plugins/catalog${refresh ? "?refresh=true" : ""}`),
                jsonRequest<{ items: Grant[] }>("/api/plugins/grants"),
                jsonRequest<{ items: Job[] }>("/api/plugins/install-jobs"),
                jsonRequest<{ items: PluginEvent[] }>("/api/plugins/events?limit=120"),
            ]);
            setPlugins(catalogData.plugins || []);
            setGrants(grantsData.items || []);
            setJobs(jobsData.items || []);
            setEvents(eventsData.items || []);
            setSelectedId((current) => current || (catalogData.plugins || []).find((item) => item.id === requestedPluginId)?.id || catalogData.plugins?.[0]?.id || "");
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t("components.plugins.PluginManagerWorkbench.error.load"));
        }
    }, [requestedPluginId, t]);

    const loadRequirements = useCallback(async (pluginId: string) => {
        if (!pluginId) return;
        const data = await jsonRequest<RequirementResponse>(`/api/plugins/${pluginId}/configuration-requirements`);
        setRequirements(data);
    }, []);
    const loadMachineDiscovery = useCallback(async (pluginId: string, refresh = false) => {
        if (!pluginId) return;
        const data = await jsonRequest<MachineDiscovery>(
            `/api/plugins/${pluginId}/machine-discovery${refresh ? "?refresh=true" : ""}`,
        );
        setMachineDiscovery(data);
    }, []);
    const selectedInstalled = Boolean(
        plugins.find((item) => item.id === selectedId)?.installation.installed,
    );

    useEffect(() => { void load(); }, [load]);
    useEffect(() => {
        setValues({});
        setOauth(null);
        setMachineDiscovery(null);
        void loadMachineDiscovery(selectedId).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
        if (!selectedInstalled) {
            setRequirements(null);
            return;
        }
        void loadRequirements(selectedId).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
    }, [loadMachineDiscovery, loadRequirements, selectedId, selectedInstalled]);
    useEffect(() => {
        if (!jobs.some((job) => !TERMINAL_JOBS.has(job.state))) return;
        const timer = window.setInterval(() => void load(), 1500);
        return () => window.clearInterval(timer);
    }, [jobs, load]);
    useEffect(() => {
        if (!oauth || !ACTIVE_OAUTH.has(oauth.status) || !selectedId) return;
        const timer = window.setInterval(async () => {
            try {
                const state = await jsonRequest<OAuthState>(`/api/plugins/${selectedId}/oauth/status?componentId=${encodeURIComponent(oauth.componentId)}`);
                setOauth(state);
                if (state.status === "connected") {
                    await loadRequirements(selectedId);
                    await load();
                }
            } catch (nextError) {
                setOauth((current) => current ? { ...current, status: "failed", error: nextError instanceof Error ? nextError.message : String(nextError) } : current);
            }
        }, 1000);
        return () => window.clearInterval(timer);
    }, [load, loadRequirements, oauth, selectedId]);

    const visiblePlugins = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return plugins.filter((plugin) => {
            if (tab === "installed" && !plugin.installation.installed) return false;
            return !needle || [plugin.displayName, plugin.publisher, plugin.category, plugin.description, ...plugin.capabilities].join(" ").toLowerCase().includes(needle);
        });
    }, [plugins, query, tab]);
    const selected = plugins.find((plugin) => plugin.id === selectedId) || visiblePlugins[0];

    const runAction = async (key: string, action: () => Promise<void>) => {
        setBusy(key);
        setError("");
        setNotice("");
        try { await action(); } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t("components.plugins.PluginManagerWorkbench.error.action"));
        } finally { setBusy(""); }
    };

    const previewInstall = (plugin: Plugin) => runAction(`plan:${plugin.id}`, async () => {
        const job = await jsonRequest<Job>(`/api/plugins/${plugin.id}/install`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dryRun: true }),
        });
        setPreviewJob(job);
        setMachineDiscovery(job.plan?.machineDiscovery || machineDiscovery);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.planReady"));
        await load();
    });

    const executeInstall = (plugin: Plugin) => runAction(`install:${plugin.id}`, async () => {
        const planJob = await jsonRequest<Job>(`/api/plugins/${plugin.id}/install`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dryRun: true }),
        });
        if (!planJob.planDigest) throw new Error(t("components.plugins.PluginManagerWorkbench.error.planRequired"));
        setPreviewJob(planJob);
        setMachineDiscovery(planJob.plan?.machineDiscovery || null);
        if (planJob.plan?.approvalRequired && !window.confirm(t("components.plugins.PluginManagerWorkbench.confirm.systemInstall", { name: plugin.displayName }))) return;
        const job = await jsonRequest<Job>(`/api/plugins/${plugin.id}/install`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dryRun: false, approved: true, planDigest: planJob.planDigest, idempotencyKey: crypto.randomUUID() }),
        });
        setPreviewJob(job);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.installQueued"));
        await load();
    });

    const detect = (plugin: Plugin) => runAction(`detect:${plugin.id}`, async () => {
        setRequirements(await jsonRequest<RequirementResponse>(`/api/plugins/${plugin.id}/configuration-detect`, { method: "POST" }));
    });

    const importExisting = (plugin: Plugin, requirement: Requirement, sourceId: string) => runAction(`import:${requirement.id}`, async () => {
        if (!window.confirm(t("components.plugins.PluginManagerWorkbench.confirm.importCredential", { name: requirement.targetName || requirement.id }))) return;
        await jsonRequest(`/api/plugins/${plugin.id}/configuration-import`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ requirementId: requirement.id, sourceId, confirmed: true }),
        });
        await loadRequirements(plugin.id);
        await load();
    });

    const configure = (plugin: Plugin) => runAction(`configure:${plugin.id}`, async () => {
        await jsonRequest(`/api/plugins/${plugin.id}/configure`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: { ...values, disabled: false } }),
        });
        setValues({});
        await loadRequirements(plugin.id);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.configured"));
        await load();
    });

    const startOAuth = (plugin: Plugin, requirement: Requirement) => runAction(`oauth:${requirement.id}`, async () => {
        const state = await jsonRequest<OAuthState>(`/api/plugins/${plugin.id}/oauth/start`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ componentId: requirement.componentId }),
        });
        setOauth({ ...state, componentId: requirement.componentId || state.componentId });
    });

    const cancelOAuth = (plugin: Plugin, componentId: string) => runAction(`oauth-cancel:${componentId}`, async () => {
        await jsonRequest(`/api/plugins/${plugin.id}/oauth/cancel`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ componentId }),
        });
        setOauth({ componentId, status: "cancelled" });
    });

    const doctor = (plugin: Plugin) => runAction(`doctor:${plugin.id}`, async () => {
        await jsonRequest(`/api/plugins/${plugin.id}/doctor`, { method: "POST" });
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.doctorDone"));
        await load();
    });

    const uninstall = (plugin: Plugin) => runAction(`uninstall:${plugin.id}`, async () => {
        if (!window.confirm(t("components.plugins.PluginManagerWorkbench.confirm.uninstall", { name: plugin.displayName }))) return;
        await jsonRequest(`/api/plugins/${plugin.id}`, { method: "DELETE" });
        setRequirements(null);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.uninstalled"));
        await load();
    });

    return (
        <AdminPageShell className="max-w-[1500px] gap-4">
            <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border/70 pb-4">
                <div>
                    <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground"><SquareTerminal className="size-3.5" /> plugin_manager runtime</div>
                    <h1 className="text-2xl font-semibold tracking-[-0.025em]">{t("components.plugins.PluginManagerWorkbench.title")}</h1>
                    <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.description")}</p>
                </div>
                <Button variant="outline" size="sm" className="min-h-10 rounded-md" onClick={() => void load(true)} disabled={Boolean(busy)}><RefreshCw className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.refresh")}</Button>
            </header>

            <div className="flex items-center gap-1 overflow-x-auto border-b border-border/60">
                {TABS.map((item) => <button key={item.id} onClick={() => setTab(item.id)} className={cn("relative min-h-11 px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", tab === item.id && "text-foreground after:absolute after:inset-x-2 after:bottom-0 after:h-px after:bg-foreground")}>{t(item.labelKey)}</button>)}
            </div>

            {error || notice ? <div role={error ? "alert" : "status"} className={cn("flex items-start gap-2 border px-3 py-2 text-sm", error ? "border-destructive/30 bg-destructive/5 text-destructive" : "border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300")}>{error ? <CircleAlert className="mt-0.5 size-4 shrink-0" /> : <Check className="mt-0.5 size-4 shrink-0" />}<span>{error || notice}</span></div> : null}

            {(tab === "store" || tab === "installed") ? (
                <div className="grid min-h-[620px] grid-cols-1 border border-border/70 lg:grid-cols-[minmax(500px,1fr)_460px]">
                    <section className="border-b border-border/70 lg:border-b-0 lg:border-r">
                        <div className="flex items-center gap-2 border-b border-border/60 p-2.5"><Search className="size-4 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("components.plugins.PluginManagerWorkbench.searchPlaceholder")} className="h-10 rounded-md border-0 bg-transparent px-1 shadow-none focus-visible:ring-0" /></div>
                        <div className="divide-y divide-border/55">
                            {visiblePlugins.map((plugin) => {
                                const state = stateLabel(plugin.installation);
                                return <button key={plugin.id} onClick={() => setSelectedId(plugin.id)} className={cn("grid min-h-16 w-full grid-cols-[36px_minmax(0,1fr)_auto] items-center gap-3 px-3 py-3 text-left transition-colors hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", selected?.id === plugin.id && "bg-muted/55")}>
                                    <Image src={`/api/plugins/${plugin.id}/logo`} alt="" width={32} height={32} unoptimized className="size-8 object-contain" />
                                    <span className="min-w-0"><span className="flex items-center gap-2"><span className="truncate text-sm font-medium">{plugin.displayName}</span><span className="truncate text-xs text-muted-foreground">{plugin.publisher}</span></span><span className="mt-0.5 block truncate text-xs text-muted-foreground">{plugin.description}</span></span>
                                    <span className="flex items-center gap-2 pl-2 text-xs text-muted-foreground"><StatusDot tone={state.tone} />{t(state.key)}<ChevronRight className="size-3.5" /></span>
                                </button>;
                            })}
                        </div>
                    </section>

                    <aside className="bg-muted/[0.14] p-4">
                        {selected ? <div className="space-y-5">
                            <div className="flex items-start gap-3"><Image src={`/api/plugins/${selected.id}/logo`} alt="" width={40} height={40} unoptimized className="size-10 object-contain" /><div className="min-w-0 flex-1"><h2 className="text-base font-semibold">{selected.displayName}</h2><p className="mt-1 text-sm leading-5 text-muted-foreground">{selected.description}</p></div></div>
                            <div className="grid grid-cols-5 border-y border-border/60 py-3 text-center">{[["CLI", selected.componentCounts.cli], ["Skill", selected.componentCounts.skills], ["MCP", selected.componentCounts.mcp], ["UI", selected.componentCounts.uiAdapters], ["Adapter", selected.componentCounts.providerAdapters || 0]].map(([label, count]) => <div key={String(label)} className="border-r border-border/50 last:border-0"><div className="text-sm font-medium">{count}</div><div className="text-[11px] text-muted-foreground">{label}</div></div>)}</div>
                            {machineDiscovery?.pluginId === selected.id ? <div className="space-y-2 border border-border/70 bg-background p-3">
                                <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.machine.title")}</h3><p className="mt-0.5 text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.machine.description")}</p></div><Button type="button" variant="ghost" size="sm" className="min-h-9 rounded-md" onClick={() => void loadMachineDiscovery(selected.id, true)} disabled={Boolean(busy)}><RefreshCw className="mr-1.5 size-3.5" />{t("components.plugins.PluginManagerWorkbench.machine.refresh")}</Button></div>
                                <div className="divide-y divide-border/55 border-y border-border/55">
                                    {[...machineDiscovery.cli.map((item) => ({ kind: "CLI", item })), ...machineDiscovery.skills.map((item) => ({ kind: "Skill", item }))].map(({ kind, item }) => {
                                        const names = kind === "CLI" ? item.commands || [] : [...(item.detectedNames || []), ...(item.missingNames || [])];
                                        return <div key={`${kind}-${item.componentId}`} className="flex items-center justify-between gap-3 py-2 text-xs"><span className="min-w-0 truncate"><span className="font-medium">{kind}</span>{names.length ? <span className="text-muted-foreground"> · {names.join("、")}</span> : null}</span><span className={cn("shrink-0", item.state === "conflict" ? "text-destructive" : item.state === "detected" || item.state === "registered" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")}>{t(`components.plugins.PluginManagerWorkbench.machine.state.${item.state}`)}</span></div>;
                                    })}
                                    {machineDiscovery.ordinaryMcp.map((item) => <div key={item.componentId} className="py-2 text-xs"><div className="flex items-center justify-between gap-3"><span className="font-medium">MCP · {item.serverName}</span><span className="text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.machine.ordinaryMcp")}</span></div><p className="mt-1 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.machine.ordinaryMcpHelp")}</p></div>)}
                                </div>
                                {!machineDiscovery.skillsCli.available ? <p className="text-xs text-destructive">{t("components.plugins.PluginManagerWorkbench.machine.skillsUnavailable")}</p> : null}
                            </div> : <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />{t("components.plugins.PluginManagerWorkbench.machine.loading")}</div>}
                            <div><h3 className="mb-2 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.capabilities")}</h3><div className="flex flex-wrap gap-1.5">{selected.capabilities.map((item) => <span key={item} className="border border-border/70 bg-background px-2 py-1 text-xs">{item}</span>)}</div></div>

                            {selected.installation.installed && requirements?.pluginId === selected.id ? <div className="space-y-3 border-t border-border/60 pt-4">
                                <div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.configuration.title")}</h3><p className="text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.configuration.description")}</p></div><Button variant="outline" size="sm" className="min-h-10 rounded-md" onClick={() => void detect(selected)} disabled={Boolean(busy)}><RefreshCw className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.configuration.detect")}</Button></div>
                                {requirements.requirements.map((requirement) => <RequirementField key={requirement.id} requirement={requirement} value={values[requirement.id]} oauth={oauth} busy={busy} onChange={(value) => setValues((current) => ({ ...current, [requirement.id]: value }))} onImport={(sourceId) => void importExisting(selected, requirement, sourceId)} onOAuth={() => void startOAuth(selected, requirement)} onCancelOAuth={() => void cancelOAuth(selected, requirement.componentId || "")} t={t} />)}
                                {!requirements.requirements.length ? <div className="border border-border/70 bg-background p-3 text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.configuration.none")} <a href={selected.officialLinks.documentation} target="_blank" rel="noreferrer" className="font-medium text-foreground underline-offset-4 hover:underline">{t("components.plugins.PluginManagerWorkbench.officialDocs")}</a></div> : null}
                            </div> : null}

                            {previewJob?.pluginId === selected.id && previewJob.plan ? <div className="border border-border/70 bg-background p-3 text-xs"><div className="mb-2 font-medium">{t("components.plugins.PluginManagerWorkbench.dryRunPlan")}</div><div className="divide-y divide-border/50 border-y border-border/50">{(previewJob.plan.steps?.cli || []).map((step) => <div key={step.componentId} className="flex items-center justify-between gap-3 py-2"><span>CLI</span><span className="text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.machine.action.${step.action || "install"}`)}</span></div>)}{(previewJob.plan.steps?.skills || []).map((step) => <div key={step.id} className="flex items-center justify-between gap-3 py-2"><span>Skill</span><span className="text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.machine.action.${step.action || "install"}`)}</span></div>)}</div><div className="mt-2 text-muted-foreground">{previewJob.plan.approvalRequired ? t("components.plugins.PluginManagerWorkbench.systemApprovalRequired") : t("components.plugins.PluginManagerWorkbench.managedInstall")}</div><TechnicalReferenceDetails className="mt-3" items={[{ label: t("components.common.traceReference"), value: previewJob.planDigest }]} /></div> : null}

                            <div className="flex flex-wrap gap-2 border-t border-border/60 pt-4">
                                {!selected.installation.installed ? <><Button size="sm" variant="outline" className="min-h-10 rounded-md" onClick={() => void previewInstall(selected)} disabled={Boolean(busy)}>{busy === `plan:${selected.id}` ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : <Activity className="mr-2 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.preflight")}</Button><Button size="sm" className="min-h-10 rounded-md" onClick={() => void executeInstall(selected)} disabled={Boolean(busy) || Boolean(machineDiscovery?.summary.conflicts)}>{busy === `install:${selected.id}` ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : <Download className="mr-2 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.complete")}</Button></> : <><Button size="sm" className="min-h-10 rounded-md" onClick={() => void configure(selected)} disabled={Boolean(busy) || !Object.keys(values).length}><Settings2 className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.configure")}</Button><Button size="sm" variant="outline" className="min-h-10 rounded-md" onClick={() => void doctor(selected)} disabled={Boolean(busy)}><Activity className="mr-2 size-3.5" />Doctor</Button><Button size="sm" variant="ghost" className="min-h-10 rounded-md text-destructive hover:text-destructive" onClick={() => void uninstall(selected)} disabled={Boolean(busy)}><Trash2 className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.uninstall")}</Button></>}
                                <a href={selected.officialLinks.documentation} target="_blank" rel="noreferrer" className="ml-auto inline-flex min-h-10 items-center gap-1.5 px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground">{t("components.plugins.PluginManagerWorkbench.officialDocs")}<ExternalLink className="size-3" /></a>
                            </div>
                        </div> : null}
                    </aside>
                </div>
            ) : null}

            {tab === "grants" ? <DataTable empty={t("components.plugins.PluginManagerWorkbench.empty.grants")} rows={grants.map((grant) => ({
                primary: grant.pluginId,
                secondary: `${t(grant.scope === "session" ? "components.plugins.PluginManagerWorkbench.grant.session" : "components.plugins.PluginManagerWorkbench.grant.task")} · ${t(grant.granteeType === "subagent" ? "components.plugins.PluginManagerWorkbench.grant.subagent" : "components.plugins.PluginManagerWorkbench.grant.supervisor")}`,
                detail: t("components.plugins.PluginManagerWorkbench.grant.components", { count: grant.componentIds.length }),
            }))} /> : null}
            {tab === "jobs" ? <DataTable empty={t("components.plugins.PluginManagerWorkbench.empty.jobs")} rows={jobs.map((job) => ({ primary: `${job.pluginId} · ${job.state}`, secondary: job.externalReconciliation ? "external reconciliation" : job.dryRun ? "dry-run" : "install transaction", detail: job.error || job.createdAt || "" }))} /> : null}
            {tab === "logs" ? <DataTable empty={t("components.plugins.PluginManagerWorkbench.empty.logs")} rows={events.map((event) => ({ primary: `${event.plugin_id || "system"} · ${event.event_type}`, secondary: event.status, detail: event.created_at }))} /> : null}
        </AdminPageShell>
    );
}

function RequirementField({ requirement, value, oauth, busy, onChange, onImport, onOAuth, onCancelOAuth, t }: { requirement: Requirement; value?: string | boolean; oauth: OAuthState | null; busy: string; onChange: (value: string | boolean) => void; onImport: (sourceId: string) => void; onOAuth: () => void; onCancelOAuth: () => void; t: ReturnType<typeof useT> }) {
    const id = `plugin-config-${requirement.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    const translatedLabel = requirement.labelKey ? t(requirement.labelKey) : "";
    const label = translatedLabel && translatedLabel !== requirement.labelKey
        ? translatedLabel
        : requirement.targetName || requirement.id;
    const translatedHelp = requirement.helpKey ? t(requirement.helpKey) : "";
    const help = translatedHelp && translatedHelp !== requirement.helpKey ? translatedHelp : "";
    const source = `${t(`components.plugins.PluginManagerWorkbench.configuration.source.${requirement.source}`)} · ${t(`components.plugins.PluginManagerWorkbench.configuration.confidence.${requirement.confidence}`)}`;
    const status = t(`components.plugins.PluginManagerWorkbench.configuration.status.${requirement.status}`);
    const importSource = requirement.discovery?.find((item) => item.present)?.sourceId;
    const oauthForField = oauth?.componentId === requirement.componentId ? oauth : null;
    return <div className="rounded-md border border-border/70 bg-background p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2"><label htmlFor={id} className="text-sm font-medium">{label}{requirement.required ? <span className="ml-1 text-destructive">*</span> : null}</label><span className={cn("rounded-full border px-2 py-0.5 text-[10px]", requirement.status === "configured" ? "border-emerald-500/30 text-emerald-700 dark:text-emerald-300" : "border-border text-muted-foreground")}>{status}</span><span className="text-[10px] text-muted-foreground">{source}</span></div>
        {help ? <p className="mb-2 text-xs leading-5 text-muted-foreground">{help}</p> : null}
        {requirement.kind === "oauth" ? <div className="flex flex-wrap items-center gap-2"><Button id={id} type="button" size="sm" className="min-h-10 rounded-md" onClick={onOAuth} disabled={Boolean(busy) || requirement.configured}><ShieldCheck className="mr-2 size-3.5" />{requirement.configured ? t("components.plugins.PluginManagerWorkbench.configuration.authorized") : t("components.plugins.PluginManagerWorkbench.configuration.authorize")}</Button>{oauthForField && ACTIVE_OAUTH.has(oauthForField.status) ? <Button type="button" size="sm" variant="outline" className="min-h-10 rounded-md" onClick={onCancelOAuth}><Loader2 className="mr-2 size-3.5 animate-spin" />{oauthForField.status} · {t("components.plugins.PluginManagerWorkbench.configuration.cancel")}</Button> : null}{oauthForField?.error ? <span className="text-xs text-destructive">{oauthForField.error}</span> : null}</div>
        : requirement.kind === "boolean" ? <label htmlFor={id} className="flex min-h-10 items-center gap-2 text-sm"><input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} className="size-4 accent-primary" />{t("components.plugins.PluginManagerWorkbench.configuration.enabled")}</label>
        : requirement.kind === "enum" ? <select id={id} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><option value="">{t("components.plugins.PluginManagerWorkbench.configuration.select")}</option>{(requirement.options || []).map((option) => <option key={option} value={option}>{option}</option>)}</select>
        : <Input id={id} type={requirement.kind === "secret" ? "password" : requirement.kind === "url" ? "url" : "text"} autoComplete="off" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} placeholder={requirement.configured && requirement.kind === "secret" ? t("components.plugins.PluginManagerWorkbench.configuration.secretConfigured") : ""} className="h-10 rounded-md" />}
        {importSource ? <Button type="button" size="sm" variant="outline" className="mt-2 min-h-10 rounded-md" onClick={() => onImport(importSource)} disabled={Boolean(busy)}><KeyRound className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.configuration.importExisting")}</Button> : null}
    </div>;
}

function DataTable({ rows, empty }: { rows: Array<{ primary: string; secondary: string; detail: string }>; empty: string }) {
    if (!rows.length) return <div className="border border-dashed border-border px-4 py-16 text-center text-sm text-muted-foreground">{empty}</div>;
    return <div className="divide-y divide-border/60 border border-border/70">{rows.map((row, index) => <div key={`${row.primary}-${index}`} className="grid gap-1 px-3 py-3 md:grid-cols-[minmax(220px,1fr)_180px_1.4fr]"><div className="text-sm font-medium">{row.primary}</div><div className="text-xs text-muted-foreground">{row.secondary}</div><div className="truncate text-xs text-muted-foreground">{row.detail}</div></div>)}</div>;
}
