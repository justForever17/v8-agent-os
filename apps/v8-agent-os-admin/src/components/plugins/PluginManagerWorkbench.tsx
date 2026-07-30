"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import {
    Activity,
    Check,
    ChevronDown,
    ChevronRight,
    CircleAlert,
    Cloud,
    Download,
    ExternalLink,
    FolderOpen,
    Gamepad2,
    HardDrive,
    KeyRound,
    Loader2,
    MonitorPlay,
    PackageCheck,
    RefreshCw,
    Search,
    Settings2,
    ShieldCheck,
    Trash2,
} from "lucide-react";

import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchAdminJson, peekAdminJsonCache, primeAdminJsonCache } from "@/lib/admin-client-cache";
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
    setupAdapter?: "godot_v1";
};

type MachineComponent = {
    componentId: string;
    componentType?: "cli" | "skill" | "mcp";
    displayName?: string;
    description?: string;
    state: "registered" | "detected" | "partial" | "missing" | "unknown" | "conflict";
    action: "keep" | "adopt" | "complete" | "install" | "update" | "review";
    commands?: string[];
    detectedNames?: string[];
    missingNames?: string[];
    conflicts?: string[];
    installedVersion?: string;
    availableVersion?: string;
    runtimeVersion?: string;
    protocolVersion?: string;
    versionState?: "current" | "available" | "unknown" | "unsupported" | "review_required";
    updateSupported?: boolean;
    members?: Array<{ name: string; description?: string }>;
};
type MachineDiscovery = {
    pluginId: string;
    skillsCli: { available: boolean; package: string; version?: string; probeOk?: boolean; error?: string };
    cli: MachineComponent[];
    skills: MachineComponent[];
    mcp: MachineComponent[];
    components: MachineComponent[];
    ordinaryMcp: Array<{ componentId: string; serverName: string; enabled: boolean; managedBy: "extensions_runtime" }>;
    summary: {
        detected: number;
        needsCompletion: number;
        conflicts: number;
        ordinaryMcp: number;
        updatesAvailable?: number;
        presentUnits?: number;
        totalUnits?: number;
        missingUnits?: number;
        coverage?: "none" | "partial" | "complete" | "blocked";
    };
};

type RequirementKind = "secret" | "text" | "url" | "enum" | "boolean" | "oauth" | "cli_login" | "file";
const FEATURED_PLUGIN_ORDER = ["office-suite", "godot"] as const;

type GodotSetupStep = {
    state: "missing" | "invalid" | "blocked" | "unchecked" | "offline" | "ready";
    detail?: string;
    path?: string;
    version?: string;
    rawVersion?: string;
    upgradeRecommended?: boolean;
    recommendedVersion?: string;
    value?: string;
    endpoint?: string;
    serverName?: string;
    toolCount?: number;
};
type GodotSetup = {
    pluginId: string;
    adapter: "godot_v1";
    godotExecutable: string;
    projectPath: string;
    scenario: string;
    status: {
        steps: {
            application: GodotSetupStep;
            project: GodotSetupStep;
            scenario: GodotSetupStep;
            mcp: GodotSetupStep;
        };
        readyForInstall: boolean;
        editorOnline: boolean;
        offlinePrerequisitesReady: boolean;
        blockingReasons: string[];
    };
};
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
            mcp?: Array<{ id: string; action?: string; serverName?: string }>;
        };
    };
    error?: string;
    createdAt?: string;
    steps?: Array<{
        ordinal: number;
        type: string;
        state: string;
        details?: { componentType?: string; componentId?: string; action?: string };
        createdAt?: string;
        finishedAt?: string;
    }>;
    progress?: {
        phase: string;
        completedComponents: number;
        totalComponents: number;
        currentComponent?: { componentType?: string; componentId?: string; action?: string };
        lastCompletedComponent?: { componentType?: string; componentId?: string; action?: string };
    };
};
type Grant = { grantId: string; pluginId: string; scope: "task" | "session"; sessionId: string; runId?: string; componentIds: string[]; granteeType: string; granteeId: string };
type PluginEvent = { id: string; plugin_id?: string; event_type: string; status: string; created_at: string };
type AuthorizationFlowState = { componentId: string; status: string; flow: "mcp_oauth" | "cli_login"; authorizationUrl?: string; browserOpened?: boolean; interactionHint?: "browser_callback" | "device_code_clipboard"; error?: string };
type Tab = "store" | "installed" | "grants" | "jobs" | "logs";

const TERMINAL_JOBS = new Set(["planned", "ready", "rolled_back", "rollback_failed", "external_reconciliation_required", "failed", "completed"]);
const ACTIVE_AUTHORIZATION = new Set(["connecting", "waiting_for_browser", "exchanging_token"]);
const TABS: Array<{ id: Tab; labelKey: string }> = [
    { id: "store", labelKey: "components.plugins.PluginManagerWorkbench.tab.store" },
    { id: "installed", labelKey: "components.plugins.PluginManagerWorkbench.tab.installed" },
    { id: "grants", labelKey: "components.plugins.PluginManagerWorkbench.tab.grants" },
    { id: "jobs", labelKey: "components.plugins.PluginManagerWorkbench.tab.jobs" },
    { id: "logs", labelKey: "components.plugins.PluginManagerWorkbench.tab.logs" },
];
const PLUGIN_CATALOG_URL = "/api/plugins/catalog";
const PLUGIN_GRANTS_URL = "/api/plugins/grants";
const PLUGIN_JOBS_URL = "/api/plugins/install-jobs";
const PLUGIN_EVENTS_URL = "/api/plugins/events?limit=120";
type PluginCatalogPayload = { plugins: Plugin[] };
type PluginGrantsPayload = { items: Grant[] };
type PluginJobsPayload = { items: Job[] };
type PluginEventsPayload = { items: PluginEvent[] };

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
    const [plugins, setPlugins] = useState<Plugin[]>(() => peekAdminJsonCache<PluginCatalogPayload>(PLUGIN_CATALOG_URL)?.plugins || []);
    const [grants, setGrants] = useState<Grant[]>(() => peekAdminJsonCache<PluginGrantsPayload>(PLUGIN_GRANTS_URL)?.items || []);
    const [jobs, setJobs] = useState<Job[]>(() => peekAdminJsonCache<PluginJobsPayload>(PLUGIN_JOBS_URL)?.items || []);
    const [events, setEvents] = useState<PluginEvent[]>(() => peekAdminJsonCache<PluginEventsPayload>(PLUGIN_EVENTS_URL)?.items || []);
    const [requirements, setRequirements] = useState<RequirementResponse | null>(null);
    const [values, setValues] = useState<Record<string, string | boolean>>({});
    const [authorization, setAuthorization] = useState<AuthorizationFlowState | null>(null);
    const [tab, setTab] = useState<Tab>("store");
    const [query, setQuery] = useState("");
    const [selectedId, setSelectedId] = useState("");
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [previewJob, setPreviewJob] = useState<Job | null>(null);
    const [installJob, setInstallJob] = useState<Job | null>(null);
    const [machineDiscovery, setMachineDiscovery] = useState<MachineDiscovery | null>(null);
    const [godotSetup, setGodotSetup] = useState<GodotSetup | null>(null);
    const godotSetupRequest = useRef(0);

    const load = useCallback(async (refreshCatalog = false, force = false) => {
        setError("");
        try {
            const catalogRequest = refreshCatalog
                ? jsonRequest<PluginCatalogPayload>(`${PLUGIN_CATALOG_URL}?refresh=true`).then((payload) => {
                    primeAdminJsonCache(PLUGIN_CATALOG_URL, payload);
                    return payload;
                })
                : fetchAdminJson<PluginCatalogPayload>(PLUGIN_CATALOG_URL, { force });
            const [catalogData, grantsData, jobsData, eventsData] = await Promise.all([
                catalogRequest,
                fetchAdminJson<PluginGrantsPayload>(PLUGIN_GRANTS_URL, { force }),
                fetchAdminJson<PluginJobsPayload>(PLUGIN_JOBS_URL, { force }),
                fetchAdminJson<PluginEventsPayload>(PLUGIN_EVENTS_URL, { force }),
            ]);
            setPlugins(catalogData.plugins || []);
            setGrants(grantsData.items || []);
            setJobs(jobsData.items || []);
            setEvents(eventsData.items || []);
            setInstallJob((current) => {
                const active = (jobsData.items || []).find((job) => !job.dryRun && !TERMINAL_JOBS.has(job.state));
                if (active) return active;
                return current;
            });
            const initialPlugin = (catalogData.plugins || []).find((item) => item.id === requestedPluginId)
                || FEATURED_PLUGIN_ORDER.map((id) => (catalogData.plugins || []).find((item) => item.id === id)).find(Boolean)
                || catalogData.plugins?.[0];
            setSelectedId((current) => current || initialPlugin?.id || "");
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
    const loadGodotSetup = useCallback(async (pluginId: string, refresh = false) => {
        if (!pluginId) return;
        const requestId = ++godotSetupRequest.current;
        const data = await jsonRequest<GodotSetup>(
            `/api/plugins/${pluginId}/setup${refresh ? "/refresh" : ""}`,
            refresh ? { method: "POST" } : undefined,
        );
        if (requestId === godotSetupRequest.current) setGodotSetup(data);
    }, []);
    const selectedInstalled = Boolean(
        plugins.find((item) => item.id === selectedId)?.installation.installed,
    );
    const selectedSetupAdapter = plugins.find((item) => item.id === selectedId)?.setupAdapter;

    useEffect(() => { void load(); }, [load]);
    useEffect(() => {
        setValues({});
        setAuthorization(null);
        setMachineDiscovery(null);
        setGodotSetup(null);
        void loadMachineDiscovery(selectedId).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
        if (selectedSetupAdapter === "godot_v1") {
            void loadGodotSetup(selectedId).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
        }
        if (!selectedInstalled) {
            setRequirements(null);
            return;
        }
        void loadRequirements(selectedId).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
    }, [loadGodotSetup, loadMachineDiscovery, loadRequirements, selectedId, selectedInstalled, selectedSetupAdapter]);
    useEffect(() => {
        if (!installJob || TERMINAL_JOBS.has(installJob.state)) return;
        let cancelled = false;
        let polling = false;
        const poll = async () => {
            if (polling) return;
            polling = true;
            try {
                const nextJob = await jsonRequest<Job>(`${PLUGIN_JOBS_URL}/${installJob.jobId}`);
                if (cancelled) return;
                setInstallJob(nextJob);
                setJobs((current) => [nextJob, ...current.filter((item) => item.jobId !== nextJob.jobId)]);
                if (TERMINAL_JOBS.has(nextJob.state)) {
                    if (nextJob.state === "ready") {
                        setNotice(t("components.plugins.PluginManagerWorkbench.notice.installCompleted"));
                    } else if (nextJob.state === "external_reconciliation_required") {
                        setNotice(t("components.plugins.PluginManagerWorkbench.notice.installNeedsAttention"));
                    } else {
                        setError(t("components.plugins.PluginManagerWorkbench.error.installFailed"));
                    }
                    await load(false, true);
                    await loadMachineDiscovery(nextJob.pluginId, true);
                    if (nextJob.state === "ready") await loadRequirements(nextJob.pluginId);
                }
            } catch (nextError) {
                if (!cancelled) setError(nextError instanceof Error ? nextError.message : t("components.plugins.PluginManagerWorkbench.error.action"));
            } finally {
                polling = false;
            }
        };
        void poll();
        const timer = window.setInterval(() => void poll(), 900);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [installJob?.jobId, installJob?.state, load, loadMachineDiscovery, loadRequirements, t]);
    useEffect(() => {
        if (!authorization || !ACTIVE_AUTHORIZATION.has(authorization.status) || !selectedId) return;
        const timer = window.setInterval(async () => {
            try {
                const endpoint = authorization.flow === "cli_login" ? "cli-login" : "oauth";
                const state = await jsonRequest<Omit<AuthorizationFlowState, "flow">>(`/api/plugins/${selectedId}/${endpoint}/status?componentId=${encodeURIComponent(authorization.componentId)}`);
                setAuthorization({ ...state, flow: authorization.flow });
                if (state.status === "connected") {
                    await loadRequirements(selectedId);
                    await load(false, true);
                }
            } catch (nextError) {
                setAuthorization((current) => current ? { ...current, status: "failed", error: nextError instanceof Error ? nextError.message : String(nextError) } : current);
            }
        }, 1000);
        return () => window.clearInterval(timer);
    }, [authorization, load, loadRequirements, selectedId]);

    const visiblePlugins = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return plugins.filter((plugin) => {
            if (tab === "installed" && !plugin.installation.installed) return false;
            return !needle || [plugin.displayName, plugin.publisher, plugin.category, plugin.description, ...plugin.capabilities].join(" ").toLowerCase().includes(needle);
        }).sort((left, right) => {
            const leftPriority = FEATURED_PLUGIN_ORDER.indexOf(left.id as (typeof FEATURED_PLUGIN_ORDER)[number]);
            const rightPriority = FEATURED_PLUGIN_ORDER.indexOf(right.id as (typeof FEATURED_PLUGIN_ORDER)[number]);
            if (leftPriority === rightPriority) return 0;
            if (leftPriority < 0) return 1;
            if (rightPriority < 0) return -1;
            return leftPriority - rightPriority;
        });
    }, [plugins, query, tab]);
    const selected = plugins.find((plugin) => plugin.id === selectedId) || visiblePlugins[0];
    const selectedDiscovery = machineDiscovery?.pluginId === selected?.id ? machineDiscovery : null;
    const selectedMissingUnits = selectedDiscovery?.summary.missingUnits ?? selectedDiscovery?.summary.needsCompletion ?? 0;
    const selectedPresentUnits = selectedDiscovery?.summary.presentUnits ?? selectedDiscovery?.summary.detected ?? 0;
    const selectedUpdatesAvailable = selectedDiscovery?.summary.updatesAvailable ?? 0;
    const selectedNeedsInstall = Boolean(selected && (!selected.installation.installed || selectedMissingUnits > 0 || selectedUpdatesAvailable > 0));
    const selectedNeedsCompletion = selectedPresentUnits > 0 && selectedMissingUnits > 0;
    const selectedNeedsUpdate = Boolean(selected?.installation.installed && selectedMissingUnits === 0 && selectedUpdatesAvailable > 0);
    const selectedInstallJob = installJob?.pluginId === selected?.id ? installJob : null;
    const selectedInstallActive = Boolean(selectedInstallJob && !TERMINAL_JOBS.has(selectedInstallJob.state));
    const selectedSetupReady = selected?.setupAdapter !== "godot_v1"
        || Boolean(godotSetup?.pluginId === selected.id && godotSetup.status.readyForInstall);
    const selectedHasEditableConfiguration = Boolean(
        requirements
        && selected
        && requirements.pluginId === selected.id
        && requirements.requirements.some((requirement) => requirement.kind !== "oauth" && requirement.kind !== "cli_login"),
    );

    const runAction = async (key: string, action: () => Promise<void>) => {
        setBusy(key);
        setError("");
        setNotice("");
        try { await action(); } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t("components.plugins.PluginManagerWorkbench.error.action"));
        } finally { setBusy(""); }
    };

    const persistGodotSetup = async (values: Partial<Pick<GodotSetup, "godotExecutable" | "projectPath" | "scenario">>) => {
        if (!selected || selected.setupAdapter !== "godot_v1") return;
        const requestId = ++godotSetupRequest.current;
        const setup = await jsonRequest<GodotSetup>(`/api/plugins/${selected.id}/setup`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ values }),
        });
        if (requestId !== godotSetupRequest.current) return;
        setGodotSetup(setup);
        setPreviewJob(null);
        await loadMachineDiscovery(selected.id, true);
        await load(false, true);
        setNotice(t("components.plugins.PluginManagerWorkbench.godot.saved"));
    };

    const updateGodotSetup = (values: Partial<Pick<GodotSetup, "godotExecutable" | "projectPath" | "scenario">>) => runAction("godot:setup", () => persistGodotSetup(values));

    const pickGodotExecutable = () => runAction("godot:application", async () => {
        const shell = window.v8osShell;
        if (!shell?.isShell || !shell.selectGodotExecutable) {
            throw new Error(t("components.plugins.PluginManagerWorkbench.godot.desktopRequired"));
        }
        const result = await shell.selectGodotExecutable();
        if (result.cancelled) return;
        if (!result.ok || !result.path) throw new Error(result.error || t("components.plugins.PluginManagerWorkbench.error.action"));
        await persistGodotSetup({ godotExecutable: result.path });
    });

    const pickGodotProject = () => runAction("godot:project", async () => {
        const shell = window.v8osShell;
        if (!shell?.isShell || !shell.selectGodotProjectDirectory) {
            throw new Error(t("components.plugins.PluginManagerWorkbench.godot.desktopRequired"));
        }
        const result = await shell.selectGodotProjectDirectory();
        if (result.cancelled) return;
        if (!result.ok || !result.path) throw new Error(result.error || t("components.plugins.PluginManagerWorkbench.error.action"));
        await persistGodotSetup({ projectPath: result.path });
    });

    const refreshGodotMcp = () => runAction("godot:mcp", async () => {
        if (!selected || selected.setupAdapter !== "godot_v1") return;
        const requestId = ++godotSetupRequest.current;
        const setup = await jsonRequest<GodotSetup>(`/api/plugins/${selected.id}/setup/refresh`, { method: "POST" });
        if (requestId !== godotSetupRequest.current) return;
        setGodotSetup(setup);
        if (setup.status.editorOnline) setNotice(t("components.plugins.PluginManagerWorkbench.godot.connectionReady"));
    });

    const previewInstall = (plugin: Plugin) => runAction(`plan:${plugin.id}`, async () => {
        const job = await jsonRequest<Job>(`/api/plugins/${plugin.id}/install`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dryRun: true }),
        });
        setPreviewJob(job);
        setMachineDiscovery(job.plan?.machineDiscovery || machineDiscovery);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.planReady"));
        await load(false, true);
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
        setInstallJob(job);
        setJobs((current) => [job, ...current.filter((item) => item.jobId !== job.jobId)]);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.installQueued"));
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
        await load(false, true);
    });

    const configure = (plugin: Plugin) => runAction(`configure:${plugin.id}`, async () => {
        await jsonRequest(`/api/plugins/${plugin.id}/configure`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: { ...values, disabled: false } }),
        });
        setValues({});
        await loadRequirements(plugin.id);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.configured"));
        await load(false, true);
    });

    const startOAuth = (plugin: Plugin, requirement: Requirement) => runAction(`oauth:${requirement.id}`, async () => {
        const state = await jsonRequest<Omit<AuthorizationFlowState, "flow">>(`/api/plugins/${plugin.id}/oauth/start`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ componentId: requirement.componentId }),
        });
        setAuthorization({ ...state, componentId: requirement.componentId || state.componentId, flow: "mcp_oauth" });
    });

    const startCliLogin = (plugin: Plugin, requirement: Requirement) => runAction(`cli-login:${requirement.id}`, async () => {
        const state = await jsonRequest<Omit<AuthorizationFlowState, "flow">>(`/api/plugins/${plugin.id}/cli-login/start`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ componentId: requirement.componentId, force: requirement.configured }),
        });
        setAuthorization({ ...state, componentId: requirement.componentId || state.componentId, flow: "cli_login" });
    });

    const cancelOAuth = (plugin: Plugin, componentId: string) => runAction(`oauth-cancel:${componentId}`, async () => {
        const endpoint = authorization?.flow === "cli_login" ? "cli-login" : "oauth";
        await jsonRequest(`/api/plugins/${plugin.id}/${endpoint}/cancel`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ componentId }),
        });
        setAuthorization((current) => current ? { ...current, componentId, status: "cancelled" } : null);
    });

    const doctor = (plugin: Plugin) => runAction(`doctor:${plugin.id}`, async () => {
        await jsonRequest(`/api/plugins/${plugin.id}/doctor`, { method: "POST" });
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.doctorDone"));
        await load(false, true);
    });

    const uninstall = (plugin: Plugin) => runAction(`uninstall:${plugin.id}`, async () => {
        if (!window.confirm(t("components.plugins.PluginManagerWorkbench.confirm.uninstall", { name: plugin.displayName }))) return;
        await jsonRequest(`/api/plugins/${plugin.id}`, { method: "DELETE" });
        setRequirements(null);
        setNotice(t("components.plugins.PluginManagerWorkbench.notice.uninstalled"));
        await load(false, true);
    });

    return (
        <AdminPageShell className="max-w-[1500px] gap-4">
            <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border/70 pb-4">
                <div>
                    <h1 className="text-2xl font-semibold tracking-[-0.025em]">{t("components.plugins.PluginManagerWorkbench.title")}</h1>
                    <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.description")}</p>
                </div>
                <Button variant="outline" size="sm" className="min-h-10 rounded-md" onClick={() => void load(true, true)} disabled={Boolean(busy)}><RefreshCw className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.refresh")}</Button>
            </header>

            <div className="flex items-center gap-1 overflow-x-auto border-b border-border/60">
                {TABS.map((item) => <button key={item.id} onClick={() => setTab(item.id)} className={cn("relative min-h-11 px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", tab === item.id && "text-foreground after:absolute after:inset-x-2 after:bottom-0 after:h-px after:bg-foreground")}>{t(item.labelKey)}</button>)}
            </div>

            {error || notice ? <div role={error ? "alert" : "status"} className={cn("flex items-start gap-2 border px-3 py-2 text-sm", error ? "border-destructive/30 bg-destructive/5 text-destructive" : "border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300")}>{error ? <CircleAlert className="mt-0.5 size-4 shrink-0" /> : <Check className="mt-0.5 size-4 shrink-0" />}<span>{error || notice}</span></div> : null}

            {(tab === "store" || tab === "installed") ? (
                <div className="grid grid-cols-1 border border-border/70 lg:h-[calc(100vh-230px)] lg:min-h-[620px] lg:max-h-[820px] lg:grid-cols-[minmax(500px,1fr)_460px] lg:overflow-hidden">
                    <section className="flex min-h-0 flex-col border-b border-border/70 lg:border-b-0 lg:border-r">
                        <div className="flex items-center gap-2 border-b border-border/60 p-2.5"><Search className="size-4 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("components.plugins.PluginManagerWorkbench.searchPlaceholder")} className="h-10 rounded-md border-0 bg-transparent px-1 shadow-none focus-visible:ring-0" /></div>
                        <div className="max-h-[420px] divide-y divide-border/55 overflow-y-auto overscroll-contain [scrollbar-gutter:stable] lg:max-h-none lg:min-h-0 lg:flex-1">
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

                    <aside className="bg-muted/[0.14] p-4 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:[scrollbar-gutter:stable]">
                        {selected ? <div className="space-y-5">
                            <div className="flex items-start gap-3"><Image src={`/api/plugins/${selected.id}/logo`} alt="" width={40} height={40} unoptimized className="size-10 object-contain" /><div className="min-w-0 flex-1"><h2 className="text-base font-semibold">{selected.displayName}</h2><p className="mt-1 text-sm leading-5 text-muted-foreground">{selected.description}</p></div></div>
                            <ComponentVersionStrip plugin={selected} discovery={selectedDiscovery} busy={busy} installActive={selectedInstallActive} onUpdate={() => void executeInstall(selected)} t={t} />
                            {selected.setupAdapter === "godot_v1"
                                ? godotSetup?.pluginId === selected.id
                                    ? <GodotSetupPanel setup={godotSetup} discovery={selectedDiscovery} busy={busy} onPickApplication={() => void pickGodotExecutable()} onPickProject={() => void pickGodotProject()} onScenarioChange={(scenario) => void updateGodotSetup({ scenario })} onRefreshMcp={() => void refreshGodotMcp()} t={t} />
                                    : <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />{t("components.plugins.PluginManagerWorkbench.godot.loading")}</div>
                                : selectedDiscovery
                                    ? <MachineDiscoveryPanel discovery={selectedDiscovery} pluginId={selected.id} requirements={requirements} busy={busy} onRefresh={() => void loadMachineDiscovery(selected.id, true)} t={t} />
                                    : <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />{t("components.plugins.PluginManagerWorkbench.machine.loading")}</div>}
                            <div><h3 className="mb-2 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.capabilities")}</h3><div className="flex flex-wrap gap-1.5">{selected.capabilities.map((item) => <span key={item} className="border border-border/70 bg-background px-2 py-1 text-xs">{item}</span>)}</div></div>

                            {selected.setupAdapter !== "godot_v1" && selected.installation.installed && requirements?.pluginId === selected.id ? <div className="space-y-3 border-t border-border/60 pt-4">
                                <div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.configuration.title")}</h3><p className="text-xs text-muted-foreground">{t(selected.id === "volcengine-mediakit" ? "components.plugins.PluginManagerWorkbench.mediaKit.configurationSummary" : "components.plugins.PluginManagerWorkbench.configuration.description")}</p></div><Button variant="outline" size="sm" className="min-h-10 rounded-md" onClick={() => void detect(selected)} disabled={Boolean(busy)}><RefreshCw className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.configuration.detect")}</Button></div>
                                {selected.id === "volcengine-mediakit" ? <MediaKitConfigurationPanel requirements={requirements.requirements} values={values} localEnabled={Boolean(selectedDiscovery?.cli.some((item) => item.state === "registered" || item.state === "detected"))} onChange={(requirementId, value) => setValues((current) => ({ ...current, [requirementId]: value }))} t={t} /> : requirements.requirements.map((requirement) => <RequirementField key={requirement.id} requirement={requirement} value={values[requirement.id]} authorization={authorization} busy={busy} onChange={(value) => setValues((current) => ({ ...current, [requirement.id]: value }))} onImport={(sourceId) => void importExisting(selected, requirement, sourceId)} onOAuth={() => void startOAuth(selected, requirement)} onCliLogin={() => void startCliLogin(selected, requirement)} onCancelAuthorization={() => void cancelOAuth(selected, requirement.componentId || "")} t={t} />)}
                                {!requirements.requirements.length ? <div className="border border-border/70 bg-background p-3 text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.configuration.none")} <a href={selected.officialLinks.documentation} target="_blank" rel="noreferrer" className="font-medium text-foreground underline-offset-4 hover:underline">{t("components.plugins.PluginManagerWorkbench.officialDocs")}</a></div> : null}
                            </div> : null}

                            {selectedInstallJob ? <InstallProgressCard job={selectedInstallJob} t={t} /> : previewJob?.pluginId === selected.id && previewJob.plan ? <div className="border border-border/70 bg-background p-3 text-xs"><div className="mb-2 font-medium">{t("components.plugins.PluginManagerWorkbench.dryRunPlan")}</div><div className="divide-y divide-border/50 border-y border-border/50">{(previewJob.plan.steps?.cli || []).map((step) => <div key={step.componentId} className="flex items-center justify-between gap-3 py-2"><span>CLI</span><span className="text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.machine.action.${step.action || "install"}`)}</span></div>)}{(previewJob.plan.steps?.skills || []).map((step) => <div key={step.id} className="flex items-center justify-between gap-3 py-2"><span>Skill</span><span className="text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.machine.action.${step.action || "install"}`)}</span></div>)}{(previewJob.plan.steps?.mcp || []).map((step) => <div key={step.id} className="flex items-center justify-between gap-3 py-2"><span>MCP</span><span className="text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.machine.action.${step.action || "install"}`)}</span></div>)}</div><div className="mt-2 text-muted-foreground">{previewJob.plan.approvalRequired ? t("components.plugins.PluginManagerWorkbench.systemApprovalRequired") : t("components.plugins.PluginManagerWorkbench.managedInstall")}</div><TechnicalReferenceDetails className="mt-3" items={[{ label: t("components.common.traceReference"), value: previewJob.planDigest }]} /></div> : null}

                            <div className="flex flex-wrap gap-2 border-t border-border/60 pt-4">
                                {selectedNeedsInstall ? <><Button size="sm" variant="outline" className="min-h-10 rounded-md" onClick={() => void previewInstall(selected)} disabled={Boolean(busy) || selectedInstallActive || !selectedSetupReady}>{busy === `plan:${selected.id}` ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : <Activity className="mr-2 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.preflight")}</Button><Button size="sm" className="min-h-10 rounded-md" onClick={() => void executeInstall(selected)} disabled={Boolean(busy) || selectedInstallActive || Boolean(selectedDiscovery?.summary.conflicts) || !selectedSetupReady}>{busy === `install:${selected.id}` || selectedInstallActive ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : <Download className="mr-2 size-3.5" />}{t(selectedNeedsCompletion ? "components.plugins.PluginManagerWorkbench.complete" : selectedNeedsUpdate ? "components.plugins.PluginManagerWorkbench.update" : "components.plugins.PluginManagerWorkbench.install")}</Button></> : null}
                                {selected.installation.installed ? <>{selectedHasEditableConfiguration ? <Button size="sm" className="min-h-10 rounded-md" onClick={() => void configure(selected)} disabled={Boolean(busy) || !Object.keys(values).length}><Settings2 className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.configure")}</Button> : null}<Button size="sm" variant="outline" className="min-h-10 rounded-md" onClick={() => void doctor(selected)} disabled={Boolean(busy)}><Activity className="mr-2 size-3.5" />Doctor</Button><Button size="sm" variant="ghost" className="min-h-10 rounded-md text-destructive hover:text-destructive" onClick={() => void uninstall(selected)} disabled={Boolean(busy)}><Trash2 className="mr-2 size-3.5" />{t("components.plugins.PluginManagerWorkbench.uninstall")}</Button></> : null}
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

function GodotSetupPanel({ setup, discovery, busy, onPickApplication, onPickProject, onScenarioChange, onRefreshMcp, t }: { setup: GodotSetup; discovery: MachineDiscovery | null; busy: string; onPickApplication: () => void; onPickProject: () => void; onScenarioChange: (scenario: string) => void; onRefreshMcp: () => void; t: ReturnType<typeof useT> }) {
    const steps = setup.status.steps;
    const localCliReady = Boolean(discovery?.cli.some((item) => item.state === "registered" || item.state === "detected"));
    const mcpPrerequisitesReady = steps.application.state === "ready" && steps.project.state === "ready" && steps.scenario.state === "ready";
    const scenarioOptions = ["2d", "2.5d", "3d"];
    const statusLabel = (step: GodotSetupStep) => t(`components.plugins.PluginManagerWorkbench.godot.state.${step.state}`);
    return <section className="border border-border/70 bg-background">
        <div className="flex items-start justify-between gap-3 border-b border-border/60 p-3">
            <div><h3 className="flex items-center gap-2 text-sm font-medium"><Gamepad2 className="size-4" />{t("components.plugins.PluginManagerWorkbench.godot.title")}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.godot.description")}</p></div>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.godot.stepCount")}</span>
        </div>
        <div className="divide-y divide-border/55">
            <div className="grid gap-2 p-3 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center">
                <span className="text-xs font-medium text-muted-foreground">1</span>
                <div><div className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.godot.downloadTitle")}</div><p className="mt-0.5 text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.godot.downloadHelp")}</p></div>
                <a href="https://godotengine.org/download/windows/" target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"><Download className="size-3.5" />{t("components.plugins.PluginManagerWorkbench.godot.download")}</a>
            </div>
            <div className="grid gap-2 p-3 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center">
                <span className="text-xs font-medium text-muted-foreground">2</span>
                <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.godot.applicationTitle")}</span><CompactState active={steps.application.state === "ready"} activeLabel={steps.application.version ? `Godot ${steps.application.version}` : statusLabel(steps.application)} inactiveLabel={statusLabel(steps.application)} /></div><p className="mt-1 truncate text-xs text-muted-foreground" title={setup.godotExecutable}>{setup.godotExecutable || t("components.plugins.PluginManagerWorkbench.godot.applicationEmpty")}</p>{steps.application.upgradeRecommended ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{t("components.plugins.PluginManagerWorkbench.godot.upgradeRecommended", { version: steps.application.recommendedVersion || "4.7" })}</p> : null}</div>
                <Button type="button" variant="outline" size="sm" className="min-h-11 rounded-md" onClick={onPickApplication} disabled={Boolean(busy)}>{busy === "godot:application" ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <FolderOpen className="mr-1.5 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.godot.chooseApplication")}</Button>
            </div>
            <div className="grid gap-2 p-3 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center">
                <span className="text-xs font-medium text-muted-foreground">3</span>
                <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.godot.projectTitle")}</span><CompactState active={steps.project.state === "ready"} activeLabel={statusLabel(steps.project)} inactiveLabel={statusLabel(steps.project)} /></div><p className="mt-1 truncate text-xs text-muted-foreground" title={setup.projectPath}>{setup.projectPath || t("components.plugins.PluginManagerWorkbench.godot.projectEmpty")}</p></div>
                <Button type="button" variant="outline" size="sm" className="min-h-11 rounded-md" onClick={onPickProject} disabled={Boolean(busy) || steps.application.state !== "ready"}>{busy === "godot:project" ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <FolderOpen className="mr-1.5 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.godot.chooseProject")}</Button>
            </div>
            <div className="grid gap-2 p-3 sm:grid-cols-[24px_minmax(0,1fr)]">
                <span className="pt-3 text-xs font-medium text-muted-foreground">4</span>
                <div><div className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.godot.scenarioTitle")}</div><div className="mt-2 grid grid-cols-3 gap-1 rounded-md bg-muted/60 p-1" role="group" aria-label={t("components.plugins.PluginManagerWorkbench.godot.scenarioTitle")}>{scenarioOptions.map((scenario) => <button key={scenario} type="button" aria-pressed={setup.scenario === scenario} onClick={() => onScenarioChange(scenario)} disabled={Boolean(busy) || steps.project.state !== "ready"} className={cn("min-h-11 rounded px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", setup.scenario === scenario ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{scenario.toUpperCase()}</button>)}</div></div>
            </div>
            <div className="grid gap-2 p-3 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center">
                <span className="text-xs font-medium text-muted-foreground">5</span>
                <div><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.godot.mcpTitle")}</span><CompactState active={steps.mcp.state === "ready"} activeLabel={statusLabel(steps.mcp)} inactiveLabel={statusLabel(steps.mcp)} /></div><p className="mt-1 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.godot.mcpHelp")} <a href="https://github.com/hi-godot/godot-ai" target="_blank" rel="noreferrer" className="font-medium text-foreground underline-offset-4 hover:underline">godot-ai<ExternalLink className="ml-1 inline size-3" /></a></p>{steps.mcp.state === "offline" ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{t("components.plugins.PluginManagerWorkbench.godot.mcpOffline")}</p> : null}</div>
                <Button type="button" variant="outline" size="sm" className="min-h-11 rounded-md" onClick={onRefreshMcp} disabled={Boolean(busy) || !mcpPrerequisitesReady}>{busy === "godot:mcp" ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}{t("components.plugins.PluginManagerWorkbench.godot.checkConnection")}</Button>
            </div>
        </div>
        <div className="grid gap-2 border-t border-border/60 p-3 sm:grid-cols-2">
            <div className="flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2.5"><span className="flex items-center gap-2 text-xs font-medium"><MonitorPlay className="size-4" />{t("components.plugins.PluginManagerWorkbench.godot.onlineMode")}</span><CompactState active={setup.status.editorOnline} activeLabel={t("components.plugins.PluginManagerWorkbench.godot.available")} inactiveLabel={t("components.plugins.PluginManagerWorkbench.godot.unavailable")} /></div>
            <div className="flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2.5"><span className="flex items-center gap-2 text-xs font-medium"><PackageCheck className="size-4" />{t("components.plugins.PluginManagerWorkbench.godot.offlineMode")}</span><CompactState active={localCliReady} activeLabel={t("components.plugins.PluginManagerWorkbench.godot.available")} inactiveLabel={t(setup.status.offlinePrerequisitesReady ? "components.plugins.PluginManagerWorkbench.godot.readyToInstall" : "components.plugins.PluginManagerWorkbench.godot.unavailable")} /></div>
        </div>
        {discovery ? <details className="border-t border-border/60 px-3 py-2.5 text-xs"><summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">{t("components.plugins.PluginManagerWorkbench.machine.technicalDetails")}</summary><div className="mt-2 space-y-1 text-muted-foreground"><div>{t("components.plugins.PluginManagerWorkbench.godot.cliComponents", { count: discovery.cli.length })}</div><div>{t("components.plugins.PluginManagerWorkbench.godot.skillComponents", { count: discovery.skills.length })}</div></div></details> : null}
    </section>;
}

function compactVersion(value?: string) {
    const normalized = String(value || "").trim().replace(/^v(?=\d)/i, "");
    if (!normalized) return "";
    if (/^[a-f0-9]{12,}$/i.test(normalized)) return normalized.slice(0, 8);
    return normalized.length > 16 ? `${normalized.slice(0, 14)}…` : normalized;
}

function ComponentVersionStrip({ plugin, discovery, busy, installActive, onUpdate, t }: { plugin: Plugin; discovery: MachineDiscovery | null; busy: string; installActive: boolean; onUpdate: () => void; t: ReturnType<typeof useT> }) {
    const entries = [
        { label: "CLI", count: plugin.componentCounts.cli, components: discovery?.cli || [] },
        { label: "Skill", count: plugin.componentCounts.skills, components: discovery?.skills || [] },
        { label: "MCP", count: plugin.componentCounts.mcp, components: discovery?.mcp || [] },
        { label: "UI", count: plugin.componentCounts.uiAdapters, components: [] as MachineComponent[] },
        { label: "Adapter", count: plugin.componentCounts.providerAdapters || 0, components: [] as MachineComponent[] },
    ].filter((item) => Number(item.count) > 0);

    return <div className="relative flex border-y border-border/60 py-3 text-center">
        {entries.map((entry, index) => {
            const installedVersions = [...new Set(entry.components.map((item) => item.runtimeVersion || item.installedVersion).filter(Boolean))];
            const availableVersions = [...new Set(entry.components.map((item) => item.availableVersion).filter(Boolean))];
            const version = installedVersions.length === 1
                ? compactVersion(installedVersions[0])
                : installedVersions.length > 1
                    ? t("components.plugins.PluginManagerWorkbench.machine.version.multiple")
                    : t("components.plugins.PluginManagerWorkbench.machine.version.unknown");
            const updateAvailable = entry.components.some((item) => item.action === "update" && item.updateSupported);
            const members = new Map<string, string>();
            entry.components.forEach((component) => {
                const componentMembers = component.members?.length
                    ? component.members
                    : [{ name: component.displayName || component.componentId, description: component.description }];
                componentMembers.forEach((member) => {
                    if (member.name && !members.has(member.name)) members.set(member.name, member.description || component.description || "");
                });
            });
            const popupAlignment = index === 0 ? "left-0" : index === entries.length - 1 ? "right-0" : "left-1/2 -translate-x-1/2";
            if (!entry.components.length) {
                return <div key={entry.label} className="min-w-0 flex-1 border-r border-border/50 last:border-0"><div className="text-sm font-medium">{entry.count}</div><div className="text-[11px] text-muted-foreground">{entry.label}</div></div>;
            }
            return <details key={entry.label} className="group relative min-w-0 flex-1 border-r border-border/50 last:border-0">
                <summary className="list-none cursor-pointer select-none rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden" aria-label={t("components.plugins.PluginManagerWorkbench.machine.version.open", { type: entry.label })}>
                    <div className="text-sm font-medium">{entry.count}</div>
                    <div className="flex items-center justify-center gap-1 text-[11px] text-muted-foreground"><span>{entry.label}</span><span className={cn("max-w-16 truncate text-[9px] tabular-nums", updateAvailable && "text-primary")}>{version}</span><ChevronDown className="size-3 transition-transform group-open:rotate-180" /></div>
                </summary>
                <div className={cn("invisible absolute top-[calc(100%+10px)] z-50 w-[min(320px,calc(100vw-40px))] rounded-md border border-border bg-popover p-3 text-left text-popover-foreground opacity-0 shadow-lg transition-opacity group-open:visible group-open:opacity-100 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100", popupAlignment)}>
                    <div className="mb-2 flex items-center justify-between gap-3"><span className="text-xs font-medium">{t("components.plugins.PluginManagerWorkbench.machine.version.title", { type: entry.label })}</span><span className="text-[10px] text-muted-foreground">{version}</span></div>
                    <div className="max-h-64 divide-y divide-border/55 overflow-y-auto overscroll-contain [scrollbar-width:thin]">
                        {[...members.entries()].map(([name, description]) => <div key={name} className="py-2 first:pt-0 last:pb-0"><div className="break-all text-xs font-medium">{name}</div>{description ? <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">{description}</p> : null}</div>)}
                    </div>
                    {entry.components.map((component) => <div key={component.componentId} className="mt-2 flex min-w-0 items-center justify-between gap-2 text-[10px] text-muted-foreground"><span className="truncate">{component.displayName || component.componentId}</span><span className="shrink-0 tabular-nums">{compactVersion(component.runtimeVersion || component.installedVersion) || "—"}{component.availableVersion && component.availableVersion !== component.installedVersion ? ` → ${compactVersion(component.availableVersion)}` : ""}</span></div>)}
                    {entry.label === "Skill" && discovery?.skillsCli.version ? <p className="mt-2 text-[10px] text-muted-foreground">skills CLI {compactVersion(discovery.skillsCli.version)}</p> : null}
                    {entry.label === "MCP" && entry.components.some((item) => item.protocolVersion) ? <p className="mt-2 text-[10px] text-muted-foreground">MCP {entry.components.map((item) => item.protocolVersion).filter(Boolean).join(" · ")}</p> : null}
                    {updateAvailable ? <Button type="button" size="sm" className="mt-3 min-h-9 w-full rounded-md" onClick={onUpdate} disabled={Boolean(busy) || installActive}>{Boolean(busy) || installActive ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}{availableVersions.length === 1 ? t("components.plugins.PluginManagerWorkbench.machine.version.updateTo", { version: compactVersion(availableVersions[0]) }) : t("components.plugins.PluginManagerWorkbench.update")}</Button> : null}
                </div>
            </details>;
        })}
    </div>;
}

function MachineDiscoveryPanel({ discovery, pluginId, requirements, busy, onRefresh, t }: { discovery: MachineDiscovery; pluginId: string; requirements: RequirementResponse | null; busy: string; onRefresh: () => void; t: ReturnType<typeof useT> }) {
    const rows = [
        ...discovery.cli.map((item) => ({ kind: "CLI", item })),
        ...discovery.skills.map((item) => ({ kind: "Skill", item })),
        ...(discovery.mcp || []).map((item) => ({ kind: "MCP", item })),
    ];
    const localEnabled = discovery.cli.some((item) => item.state === "registered" || item.state === "detected");
    const mediaKitRequirements = requirements?.pluginId === pluginId ? requirements.requirements : [];
    const cloudConfigured = Boolean(mediaKitRequirements.find((item) => item.targetName === "MEDIAKIT_API_KEY")?.configured);
    const details = <div className="divide-y divide-border/55 border-y border-border/55">
        {rows.map(({ kind, item }) => {
            const names = kind === "CLI"
                ? item.commands || []
                : kind === "MCP"
                    ? item.members?.map((member) => member.name) || []
                    : [...new Set([...(item.detectedNames || []), ...(item.missingNames || []), ...(item.conflicts || [])])];
            return <div key={`${kind}-${item.componentId}`} className="flex items-center justify-between gap-3 py-2 text-xs"><span className="min-w-0 truncate"><span className="font-medium">{kind}</span>{names.length ? <span className="text-muted-foreground"> · {names.join("、")}</span> : null}</span><span className={cn("shrink-0", item.state === "conflict" ? "text-destructive" : item.state === "detected" || item.state === "registered" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")}>{t(`components.plugins.PluginManagerWorkbench.machine.state.${item.state}`)}</span></div>;
        })}
        {discovery.ordinaryMcp.map((item) => <div key={item.componentId} className="py-2 text-xs"><div className="flex items-center justify-between gap-3"><span className="font-medium">MCP · {item.serverName}</span><span className="text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.machine.ordinaryMcp")}</span></div></div>)}
    </div>;

    if (pluginId === "volcengine-mediakit") {
        return <section className="space-y-3 border border-border/70 bg-background p-3">
            <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.mediaKit.modeTitle")}</h3><Button type="button" variant="ghost" size="sm" className="min-h-9 rounded-md" onClick={onRefresh} disabled={Boolean(busy)}><RefreshCw className="mr-1.5 size-3.5" />{t("components.plugins.PluginManagerWorkbench.machine.refresh")}</Button></div>
            <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-md border border-border/70 bg-muted/20 p-3"><div className="flex items-center justify-between gap-2"><span className="flex items-center gap-2 text-sm font-medium"><HardDrive className="size-4" />{t("components.plugins.PluginManagerWorkbench.mediaKit.localTitle")}</span><CompactState active={localEnabled} activeLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.enabled")} inactiveLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.disabled")} /></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.mediaKit.localSummary")}</p></div>
                <div className="rounded-md border border-border/70 bg-muted/20 p-3"><div className="flex items-center justify-between gap-2"><span className="flex items-center gap-2 text-sm font-medium"><Cloud className="size-4" />{t("components.plugins.PluginManagerWorkbench.mediaKit.cloudTitle")}</span><CompactState active={cloudConfigured} activeLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.configured")} inactiveLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.notConfigured")} /></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.mediaKit.cloudSummary")}</p></div>
            </div>
            <details className="text-xs"><summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">{t("components.plugins.PluginManagerWorkbench.machine.technicalDetails")}</summary><div className="mt-2">{details}</div></details>
        </section>;
    }

    return <section className="space-y-2 border border-border/70 bg-background p-3">
        <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-medium">{t("components.plugins.PluginManagerWorkbench.machine.title")}</h3><p className="mt-0.5 text-xs text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.machine.description")}</p></div><Button type="button" variant="ghost" size="sm" className="min-h-9 rounded-md" onClick={onRefresh} disabled={Boolean(busy)}><RefreshCw className="mr-1.5 size-3.5" />{t("components.plugins.PluginManagerWorkbench.machine.refresh")}</Button></div>
        {details}
        {!discovery.skillsCli.available ? <p className="text-xs text-destructive">{t("components.plugins.PluginManagerWorkbench.machine.skillsUnavailable")}</p> : null}
    </section>;
}

function CompactState({ active, activeLabel, inactiveLabel }: { active: boolean; activeLabel: string; inactiveLabel: string }) {
    return <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium", active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-muted text-muted-foreground")}><span className={cn("size-1.5 rounded-full", active ? "bg-emerald-500" : "bg-muted-foreground/40")} />{active ? activeLabel : inactiveLabel}</span>;
}

function MediaKitConfigurationPanel({ requirements, values, localEnabled, onChange, t }: { requirements: Requirement[]; values: Record<string, string | boolean>; localEnabled: boolean; onChange: (requirementId: string, value: string) => void; t: ReturnType<typeof useT> }) {
    const apiKey = requirements.find((item) => item.targetName === "MEDIAKIT_API_KEY");
    const outputPath = requirements.find((item) => item.targetName === "MEDIAKIT_OUTPUT_PATH");
    const cloudConfigured = Boolean(apiKey?.configured || (apiKey && values[apiKey.id]));
    return <div className="grid gap-3">
        <section className="rounded-md border border-border/70 bg-background p-3">
            <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm font-medium"><HardDrive className="size-4" />{t("components.plugins.PluginManagerWorkbench.mediaKit.localTitle")}</div><CompactState active={localEnabled} activeLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.enabled")} inactiveLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.disabled")} /></div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.mediaKit.localConfigHelp")}</p>
            {outputPath ? <div className="mt-3"><label htmlFor="mediakit-output-path" className="mb-1.5 block text-xs font-medium">{t("components.plugins.PluginManagerWorkbench.mediaKit.outputPath")}</label><Input id="mediakit-output-path" value={String(values[outputPath.id] ?? "")} onChange={(event) => onChange(outputPath.id, event.target.value)} placeholder="~/.mediakit/temp" className="h-10 rounded-md" /></div> : null}
        </section>
        <section className="rounded-md border border-border/70 bg-background p-3">
            <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm font-medium"><Cloud className="size-4" />{t("components.plugins.PluginManagerWorkbench.mediaKit.cloudTitle")}</div><CompactState active={cloudConfigured} activeLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.configured")} inactiveLabel={t("components.plugins.PluginManagerWorkbench.mediaKit.notConfigured")} /></div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.mediaKit.cloudConfigHelp")}</p>
            {apiKey ? <div className="mt-3"><div className="mb-1.5 flex items-center justify-between gap-3"><label htmlFor="mediakit-api-key" className="text-xs font-medium">{t("components.plugins.PluginManagerWorkbench.mediaKit.apiKey")}</label><a href="https://console.volcengine.com/imp/ai-mediakit/settings" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">{t("components.plugins.PluginManagerWorkbench.mediaKit.getKey")}<ExternalLink className="size-3" /></a></div><Input id="mediakit-api-key" type="password" autoComplete="new-password" value={String(values[apiKey.id] ?? "")} onChange={(event) => onChange(apiKey.id, event.target.value)} placeholder={apiKey.configured ? t("components.plugins.PluginManagerWorkbench.configuration.secretConfigured") : ""} className="h-10 rounded-md" /></div> : null}
        </section>
    </div>;
}

function InstallProgressCard({ job, t }: { job: Job; t: ReturnType<typeof useT> }) {
    const progress = job.progress;
    const active = !TERMINAL_JOBS.has(job.state);
    const failed = ["failed", "rolled_back", "rollback_failed"].includes(job.state);
    const total = progress?.totalComponents || 0;
    const completed = progress?.completedComponents || 0;
    const percent = job.state === "ready" ? 100 : total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const current = progress?.currentComponent;
    const componentSteps = (job.steps || []).filter((step) => step.type === "component").slice(-4);
    return <section aria-live="polite" className={cn("space-y-3 rounded-md border p-3", failed ? "border-destructive/30 bg-destructive/5" : "border-primary/20 bg-primary/[0.035]")}>
        <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-sm font-medium">{active ? <Loader2 className="size-4 animate-spin text-primary" /> : failed ? <CircleAlert className="size-4 text-destructive" /> : <Check className="size-4 text-emerald-600" />}{t("components.plugins.PluginManagerWorkbench.installProgress.title")}</div><p className="mt-1 text-xs text-muted-foreground">{t(`components.plugins.PluginManagerWorkbench.installProgress.phase.${job.state}`)}</p></div>{total > 0 ? <span className="text-xs tabular-nums text-muted-foreground">{t("components.plugins.PluginManagerWorkbench.installProgress.count", { completed, total })}</span> : null}</div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full transition-[width] duration-300", failed ? "bg-destructive" : "bg-primary")} style={{ width: `${percent}%` }} /></div>
        {current?.componentId ? <div className="flex min-w-0 items-center gap-2 rounded-md bg-background/80 px-2.5 py-2 text-xs"><Loader2 className="size-3.5 shrink-0 animate-spin text-primary" /><span className="shrink-0 font-medium">{formatComponentType(current.componentType)}</span><span className="truncate text-muted-foreground">{current.componentId}</span></div> : null}
        {componentSteps.length ? <div className="space-y-1">{componentSteps.map((step) => <div key={`${step.ordinal}-${step.state}`} className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">{step.state === "completed" ? <Check className="size-3.5 shrink-0 text-emerald-600" /> : step.state === "failed" ? <CircleAlert className="size-3.5 shrink-0 text-destructive" /> : <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />}<span className="shrink-0">{formatComponentType(step.details?.componentType)}</span><span className="truncate">{step.details?.componentId}</span></div>)}</div> : null}
        {failed ? <p className="text-xs text-destructive">{t("components.plugins.PluginManagerWorkbench.installProgress.failedHelp")}</p> : null}
    </section>;
}

function formatComponentType(value?: string) {
    const labels: Record<string, string> = { cli: "CLI", skill: "Skill", mcp: "MCP", ui_adapter: "UI", provider_adapter: "Adapter" };
    return labels[value || ""] || value || "";
}

function RequirementField({ requirement, value, authorization, busy, onChange, onImport, onOAuth, onCliLogin, onCancelAuthorization, t }: { requirement: Requirement; value?: string | boolean; authorization: AuthorizationFlowState | null; busy: string; onChange: (value: string | boolean) => void; onImport: (sourceId: string) => void; onOAuth: () => void; onCliLogin: () => void; onCancelAuthorization: () => void; t: ReturnType<typeof useT> }) {
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
    const authorizationForField = authorization?.componentId === requirement.componentId ? authorization : null;
    return <div className="rounded-md border border-border/70 bg-background p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2"><label htmlFor={id} className="text-sm font-medium">{label}{requirement.required ? <span className="ml-1 text-destructive">*</span> : null}</label><span className={cn("rounded-full border px-2 py-0.5 text-[10px]", requirement.status === "configured" ? "border-emerald-500/30 text-emerald-700 dark:text-emerald-300" : "border-border text-muted-foreground")}>{status}</span><span className="text-[10px] text-muted-foreground">{source}</span></div>
        {help ? <p className="mb-2 text-xs leading-5 text-muted-foreground">{help}</p> : null}
        {requirement.kind === "oauth" || requirement.kind === "cli_login" ? <div className="flex flex-wrap items-center gap-2"><Button id={id} type="button" size="sm" className="min-h-10 rounded-md" onClick={requirement.kind === "cli_login" ? onCliLogin : onOAuth} disabled={Boolean(busy) || (requirement.kind === "oauth" && requirement.configured)}><ShieldCheck className="mr-2 size-3.5" />{requirement.configured ? t(requirement.kind === "cli_login" ? "components.plugins.PluginManagerWorkbench.configuration.reauthorize" : "components.plugins.PluginManagerWorkbench.configuration.authorized") : t(requirement.kind === "cli_login" ? "components.plugins.PluginManagerWorkbench.configuration.cliLogin" : "components.plugins.PluginManagerWorkbench.configuration.authorize")}</Button>{authorizationForField && ACTIVE_AUTHORIZATION.has(authorizationForField.status) ? <Button type="button" size="sm" variant="outline" className="min-h-10 rounded-md" onClick={onCancelAuthorization}><Loader2 className="mr-2 size-3.5 animate-spin" />{t("components.plugins.PluginManagerWorkbench.configuration.waitingForBrowser")} · {t("components.plugins.PluginManagerWorkbench.configuration.cancel")}</Button> : null}{authorizationForField?.authorizationUrl && ACTIVE_AUTHORIZATION.has(authorizationForField.status) ? <a href={authorizationForField.authorizationUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"><ExternalLink className="size-3.5" />{t("components.plugins.PluginManagerWorkbench.configuration.openAuthorizationPage")}</a> : null}{requirement.kind === "cli_login" && authorizationForField && ACTIVE_AUTHORIZATION.has(authorizationForField.status) ? <span className="basis-full text-xs leading-5 text-muted-foreground">{t(authorizationForField.interactionHint === "device_code_clipboard" ? "components.plugins.PluginManagerWorkbench.configuration.deviceCodeClipboard" : "components.plugins.PluginManagerWorkbench.configuration.browserCallback")}</span> : null}{authorizationForField?.error ? <span className="text-xs text-destructive">{authorizationForField.error}</span> : null}</div>
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
