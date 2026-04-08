"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, PackageCheck, Plus, RefreshCw, Save, Server, Terminal, Upload, Wrench } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { lt } from "@/lib/locale";

type ExtensionCatalogResponse = {
    startupState?: "cold" | "refreshing" | "ready" | "error";
    snapshotFreshness?: "cold" | "cached" | "live";
    lastRefreshAt?: string | null;
    lastRefreshError?: string | null;
    fingerprint?: string | null;
    changedAt?: string | null;
    lastSkillInventoryChange?: {
        reason?: string | null;
        changedAt?: string | null;
        fingerprint?: string | null;
        addedSkills?: string[];
        removedSkills?: string[];
        updatedSkills?: string[];
    } | null;
    skillsStartupState?: string | null;
    mcpStartupState?: string | null;
    runtime?: {
        startupState?: string | null;
        snapshotFreshness?: string | null;
        lastRefreshAt?: string | null;
        lastRefreshError?: string | null;
        skillsStartupState?: string | null;
        mcpStartupState?: string | null;
    };
    summary: { skillCount: number; mcpServerCount: number; connectedMcpServerCount: number; mcpToolCount: number };
    skillDependencyPolicy?: {
        mode?: string;
        pythonTarget?: string;
        systemWideInstallAllowed?: boolean;
        nodeGlobalInstallAllowed?: boolean;
    };
    skills: {
        root: string;
        roots?: string[];
        fingerprint?: string | null;
        changedAt?: string | null;
        items: Array<{ name: string; description: string; path: string }>;
    };
    mcp: {
        servers: Array<{
            name: string;
            status: "connected" | "disabled" | "error";
            toolCount: number;
            tools: Array<{ name: string; description: string }>;
            transport: string;
            target: string;
        }>;
    };
};

type ExtensionHealthResponse = {
    startupState?: "cold" | "refreshing" | "ready" | "error";
    snapshotFreshness?: "cold" | "cached" | "live";
    lastRefreshAt?: string | null;
    lastRefreshError?: string | null;
    skillsStartupState?: string | null;
    mcpStartupState?: string | null;
    runtime?: {
        startupState?: string | null;
        snapshotFreshness?: string | null;
        lastRefreshAt?: string | null;
        lastRefreshError?: string | null;
        skillsStartupState?: string | null;
        mcpStartupState?: string | null;
        silk?: {
            available?: boolean;
            version?: string | null;
            toolRoot?: string;
        };
    };
    summary: ExtensionCatalogResponse["summary"];
    skillDependencyPolicy?: ExtensionCatalogResponse["skillDependencyPolicy"];
    mcp: { statusBreakdown: Record<string, number> };
    silk?: {
        available?: boolean;
        version?: string | null;
        toolRoot?: string;
    };
};

type SkillInstallResult = {
    source: string;
    targetRoot: string;
    installed: Array<{ name: string; path: string }>;
    conflicts: Array<{ name?: string; path?: string; reason?: string }>;
    warnings: string[];
};

type SysModel = {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
    providerName?: string;
};

type ExtensionsConfigData = {
    rerankPolicy?: { enabled?: boolean };
    modelBindings?: { rerankerModel?: string; fallbackRerankerModel?: string };
};

type StructuredValidationPayload = {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
};

type TranslateFn = (value: string | ReturnType<typeof lt>) => string;

function statusLabel(status: string, t: (value: string | ReturnType<typeof lt>) => string) {
    if (status === "connected") return t(lt("已连接", "Connected"));
    if (status === "disabled") return t(lt("已停用", "Disabled"));
    return t(lt("连接异常", "Connection issue"));
}

function modelValue(model: SysModel) {
    return String(model.modelId || model.id || "").trim();
}

function modelLabel(model: SysModel) {
    const providerName = model.provider?.name || model.providerName || "";
    return `${model.name || modelValue(model)}${providerName ? ` (${providerName})` : ""}`;
}

function StatPill({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
        </div>
    );
}

function extractValidationPayload(payload: unknown): StructuredValidationPayload | null {
    if (!payload || typeof payload !== "object") return null;
    const record = payload as Record<string, unknown>;
    const detail = record.detail;
    if (detail && typeof detail === "object") {
        return detail as StructuredValidationPayload;
    }
    if (typeof record.error === "string") {
        return { message: record.error };
    }
    if (typeof detail === "string") {
        return { message: detail };
    }
    return null;
}

function localizeSkillZipValidationPayload(payload: StructuredValidationPayload | null, t: TranslateFn): string | null {
    if (!payload) return null;
    const details = payload.details || {};
    switch (payload.code) {
        case "invalid_file_type":
            return t(lt("当前只接受 .zip 压缩包。", "Only .zip archives are accepted."));
        case "empty_archive":
            return t(lt("压缩包中没有可导入的文件。", "The archive does not contain any importable files."));
        case "invalid_root_structure": {
            const rootFiles = Array.isArray(details.rootFiles) ? details.rootFiles.filter((item): item is string => typeof item === "string") : [];
            return rootFiles.length > 0
                ? t(lt(`压缩包顶层必须只有一个目录，不能直接放散文件。检测到：${rootFiles.join("、")}`, `The ZIP must contain exactly one top-level folder. Root files detected: ${rootFiles.join(", ")}`))
                : t(lt("压缩包顶层必须只有一个目录，不能直接放散文件。", "The ZIP must contain exactly one top-level folder and cannot place loose files at the root."));
        }
        case "multiple_root_directories": {
            const rootEntries = Array.isArray(details.rootEntries) ? details.rootEntries.filter((item): item is string => typeof item === "string") : [];
            return rootEntries.length > 0
                ? t(lt(`压缩包顶层必须只包含一个目录。检测到：${rootEntries.join("、")}`, `The ZIP must contain exactly one top-level directory. Detected: ${rootEntries.join(", ")}`))
                : t(lt("压缩包顶层必须只包含一个目录。", "The ZIP must contain exactly one top-level directory."));
        }
        case "missing_skill_manifest":
            return t(lt("压缩包里至少要有一个目录，并且目录内至少存在一个 SKILL.md 文件。", "The archive must contain a folder structure with at least one SKILL.md file inside."));
        case "invalid_zip":
            return t(lt("上传文件不是合法的 ZIP 压缩包。", "The uploaded file is not a valid ZIP archive."));
        default:
            return typeof payload.message === "string" && payload.message.trim() ? payload.message : null;
    }
}

function localizeMcpValidationPayload(payload: StructuredValidationPayload | null, t: TranslateFn): string | null {
    if (!payload) return null;
    switch (payload.code) {
        case "invalid_payload":
            return t(lt("MCP JSON 必须是对象。", "The MCP JSON payload must be an object."));
        case "invalid_server_map":
            return t(lt("`mcpServers` 必须是对象映射。", "`mcpServers` must be an object map."));
        case "empty_server_map":
            return t(lt("MCP JSON 至少需要包含一个 server。", "The MCP JSON must include at least one server."));
        case "empty_server_name":
            return t(lt("MCP server 名称不能为空。", "Each MCP server must have a non-empty name."));
        case "invalid_server_payload":
            return t(lt("每个 MCP server 的配置都必须是对象。", "Each MCP server entry must be an object."));
        case "invalid_command":
            return t(lt("MCP server 的 command 必须是字符串。", "The MCP server command field must be a string."));
        case "invalid_url":
            return t(lt("MCP server 的 url 必须是字符串。", "The MCP server url field must be a string."));
        case "invalid_args":
            return t(lt("MCP server 的 args 必须是数组。", "The MCP server args field must be an array."));
        case "invalid_env":
            return t(lt("MCP server 的 env 必须是对象。", "The MCP server env field must be an object."));
        case "invalid_headers":
            return t(lt("MCP server 的 headers 必须是对象。", "The MCP server headers field must be an object."));
        case "missing_target":
            return t(lt("每个启用中的 MCP server 至少需要提供 command 或 url。", "Every enabled MCP server must provide either a command or a url."));
        default:
            return typeof payload.message === "string" && payload.message.trim() ? payload.message : null;
    }
}

function validateMcpJsonInput(raw: string, t: TranslateFn): { parsed: Record<string, unknown>; serverCount: number } {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(t(lt("MCP JSON 必须是对象。", "The MCP JSON payload must be an object.")));
    }
    const serverMap = ("mcpServers" in parsed ? parsed.mcpServers : parsed) as Record<string, unknown>;
    if (!serverMap || typeof serverMap !== "object" || Array.isArray(serverMap)) {
        throw new Error(t(lt("`mcpServers` 必须是对象映射。", "`mcpServers` must be an object map.")));
    }
    const entries = Object.entries(serverMap);
    if (entries.length === 0) {
        throw new Error(t(lt("MCP JSON 至少需要包含一个 server。", "The MCP JSON must include at least one server.")));
    }
    for (const [name, rawServer] of entries) {
        if (!String(name || "").trim()) {
            throw new Error(t(lt("MCP server 名称不能为空。", "Each MCP server must have a non-empty name.")));
        }
        if (!rawServer || typeof rawServer !== "object" || Array.isArray(rawServer)) {
            throw new Error(t(lt(`MCP server \`${name}\` 的配置必须是对象。`, `The MCP server entry \`${name}\` must be an object.`)));
        }
        const server = rawServer as Record<string, unknown>;
        const disabled = Boolean(server.disabled);
        const command = typeof server.command === "string" ? server.command.trim() : "";
        const url = typeof server.url === "string" ? server.url.trim() : "";
        if (!disabled && !command && !url) {
            throw new Error(t(lt(`MCP server \`${name}\` 至少需要提供 command 或 url。`, `The MCP server \`${name}\` must provide either a command or a url.`)));
        }
        if ("args" in server && !Array.isArray(server.args)) {
            throw new Error(t(lt(`MCP server \`${name}\` 的 args 必须是数组。`, `The MCP server \`${name}\` args field must be an array.`)));
        }
        if ("env" in server && (!server.env || typeof server.env !== "object" || Array.isArray(server.env))) {
            throw new Error(t(lt(`MCP server \`${name}\` 的 env 必须是对象。`, `The MCP server \`${name}\` env field must be an object.`)));
        }
        if ("headers" in server && (!server.headers || typeof server.headers !== "object" || Array.isArray(server.headers))) {
            throw new Error(t(lt(`MCP server \`${name}\` 的 headers 必须是对象。`, `The MCP server \`${name}\` headers field must be an object.`)));
        }
    }
    return { parsed, serverCount: entries.length };
}

export default function ExtensionsPage() {
    const t = useT();
    const [catalog, setCatalog] = useState<ExtensionCatalogResponse | null>(null);
    const [health, setHealth] = useState<ExtensionHealthResponse | null>(null);
    const [configEnvelope, setConfigEnvelope] = useState<ConfigRegistryEnvelope<ExtensionsConfigData> | null>(null);
    const [models, setModels] = useState<SysModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [reloading, setReloading] = useState(false);
    const [installingCommand, setInstallingCommand] = useState(false);
    const [uploadingZip, setUploadingZip] = useState(false);
    const [savingMcp, setSavingMcp] = useState(false);
    const [commandInput, setCommandInput] = useState("");
    const [mcpConfigInput, setMcpConfigInput] = useState("");
    const [installResult, setInstallResult] = useState<SkillInstallResult | null>(null);
    const [mcpDialogOpen, setMcpDialogOpen] = useState(false);
    const [zipFileLabel, setZipFileLabel] = useState("");
    const [zipValidationError, setZipValidationError] = useState("");
    const [mcpValidationError, setMcpValidationError] = useState("");
    const [mcpValidationSummary, setMcpValidationSummary] = useState("");
    const fileInputRef = useRef<HTMLInputElement>(null);
    const { toast } = useToast();

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [catalogResponse, healthResponse, config, modelList] = await Promise.all([
                fetch("/api/extensions/catalog", { cache: "no-store" }),
                fetch("/api/extensions/health", { cache: "no-store" }),
                fetchConfigDomain<ExtensionsConfigData>("extensions"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
            ]);
            if (!catalogResponse.ok || !healthResponse.ok) {
                throw new Error(t(lt("扩展信息读取失败", "Failed to load extension data")));
            }
            const [catalogPayload, healthPayload] = await Promise.all([
                catalogResponse.json(),
                healthResponse.json(),
            ] as const);
            setCatalog(catalogPayload);
            setHealth(healthPayload);
            setConfigEnvelope(config);
            setModels(Array.isArray(modelList) ? modelList : []);
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        void loadData();
    }, [loadData]);

    const rerankModels = useMemo(
        () => models.filter((model) => ["RERANK", "RERANKER"].includes((model.type || "").toUpperCase())),
        [models]
    );

    const summaryItems = useMemo(
        () => [
            { label: lt("Skills", "Skills"), value: catalog?.summary.skillCount ?? 0, description: lt("当前已安装并可读取的 Skills 数量。", "The number of installed skills currently available for reading.") },
            { label: lt("MCP 服务", "MCP servers"), value: catalog?.summary.mcpServerCount ?? 0, description: lt("当前登记的 MCP 服务数量。", "The number of MCP servers currently registered.") },
            { label: lt("已连接 MCP", "Connected MCP"), value: catalog?.summary.connectedMcpServerCount ?? 0, description: lt("当前已成功连接的 MCP 服务数量。", "The number of MCP servers currently connected successfully.") },
            { label: lt("MCP 工具", "MCP tools"), value: catalog?.summary.mcpToolCount ?? 0, description: lt("当前可直接调用的 MCP 工具总数。", "The total number of MCP tools currently callable.") },
        ],
        [catalog]
    );

    const updateConfig = (patch: Partial<ExtensionsConfigData>) => {
        if (!configEnvelope) return;
        setConfigEnvelope({
            ...configEnvelope,
            data: {
                ...configEnvelope.data,
                ...patch,
                rerankPolicy: { ...(configEnvelope.data?.rerankPolicy || {}), ...(patch.rerankPolicy || {}) },
                modelBindings: { ...(configEnvelope.data?.modelBindings || {}), ...(patch.modelBindings || {}) },
            },
        });
    };

    const handleSaveConfig = async () => {
        if (!configEnvelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<ExtensionsConfigData>("extensions", {
                data: {
                    rerankPolicy: { enabled: Boolean(configEnvelope.data?.rerankPolicy?.enabled) },
                    modelBindings: { rerankerModel: String(configEnvelope.data?.modelBindings?.rerankerModel || "").trim() },
                },
            });
            setConfigEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
            toast({ title: t(lt("扩展候选重排已保存", "Extensions candidate reranking saved")) });
        } catch (error) {
            toast({
                title: t(lt("保存失败", "Save failed")),
                description: error instanceof Error ? error.message : t(lt("请稍后重试。", "Please try again later.")),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    };

    const handleReloadSystem = async () => {
        setReloading(true);
        try {
            const res = await fetch("/api/extensions/reload", { method: "POST" });
            if (!res.ok) throw new Error(t(lt("扩展刷新失败", "Extension reload failed")));
            await res.json();
            await loadData();
            toast({ title: t(lt("扩展已刷新", "Extensions refreshed")) });
        } catch {
            toast({ title: t(lt("刷新失败", "Refresh failed")), description: t(lt("请稍后重试。", "Please try again later.")), variant: "destructive" });
        } finally {
            setReloading(false);
        }
    };

    const handleCommandInstall = async () => {
        if (!commandInput.trim()) return;
        setInstallingCommand(true);
        setInstallResult(null);
        try {
            const res = await fetch("/api/skills/install/command", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ command: commandInput }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(String(data?.detail || data?.error || t(lt("Skills 安装失败", "Skill installation failed"))));
            setInstallResult(data);
            setCommandInput("");
            toast({ title: t(lt("Skills 安装完成", "Skills installed")), description: t(lt(`已安装 ${data.installed?.length ?? 0} 项。`, `${data.installed?.length ?? 0} item(s) installed.`)) });
            await loadData();
        } catch (error) {
            toast({
                title: t(lt("安装失败", "Install failed")),
                description: error instanceof Error ? error.message : t(lt("执行失败", "Execution failed")),
                variant: "destructive",
            });
        } finally {
            setInstallingCommand(false);
        }
    };

    const handleZipUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;
        setUploadingZip(true);
        setZipValidationError("");
        try {
            if (!String(file.name || "").toLowerCase().endsWith(".zip")) {
                throw new Error(t(lt("当前只接受 .zip 压缩包。", "Only .zip archives are accepted.")));
            }
            if (file.size <= 0) {
                throw new Error(t(lt("上传文件为空，请重新选择。", "The selected archive is empty.")));
            }
            setZipFileLabel(`${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`);
            const formData = new FormData();
            formData.append("file", file);
            const res = await fetch("/api/skills/install/zip", { method: "POST", body: formData });
            const data = await res.json();
            if (!res.ok) {
                const validation = extractValidationPayload(data);
                throw new Error(localizeSkillZipValidationPayload(validation, t) || String(data?.detail || data?.error || t(lt("Skills 压缩包安装失败", "Skill archive install failed"))));
            }
            setInstallResult(data);
            toast({ title: t(lt("技能已导入", "Skill archive imported")) });
            await loadData();
        } catch (error) {
            setZipValidationError(error instanceof Error ? error.message : t(lt("请检查压缩包结构后重试。", "Check the archive structure and try again.")));
            toast({
                title: t(lt("上传失败", "Upload failed")),
                description: error instanceof Error ? error.message : t(lt("请检查压缩包结构后重试。", "Check the archive structure and try again.")),
                variant: "destructive",
            });
        } finally {
            setUploadingZip(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const saveMcpConfig = async () => {
        if (!mcpConfigInput.trim()) return;
        setSavingMcp(true);
        setMcpValidationError("");
        try {
            const validation = validateMcpJsonInput(mcpConfigInput, t);
            setMcpValidationSummary(t(lt(`检测到 ${validation.serverCount} 个 MCP server，准备写入。`, `${validation.serverCount} MCP server(s) validated and ready to import.`)));
            const res = await fetch("/api/mcp/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: mcpConfigInput,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const validationError = extractValidationPayload(data);
                throw new Error(localizeMcpValidationPayload(validationError, t) || t(lt("MCP 配置保存失败", "Failed to save MCP configuration")));
            }
            setMcpDialogOpen(false);
            setMcpConfigInput("");
            setMcpValidationSummary("");
            toast({ title: t(lt("配置已合并", "Configuration merged")), description: t(lt("新的 MCP 配置已写入系统。", "The new MCP configuration has been written into the system.")) });
            await loadData();
        } catch (error) {
            setMcpValidationError(error instanceof Error ? error.message : t(lt("请检查 JSON 格式。", "Please verify the JSON format.")));
            toast({
                title: t(lt("导入失败", "Import failed")),
                description: error instanceof Error ? error.message : t(lt("请检查 JSON 格式。", "Please verify the JSON format.")),
                variant: "destructive",
            });
        } finally {
            setSavingMcp(false);
        }
    };

    if (loading || !catalog || !health || !configEnvelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    const rerankEnabled = Boolean(configEnvelope.data?.rerankPolicy?.enabled);
    const rerankerModel = String(configEnvelope.data?.modelBindings?.rerankerModel || "").trim();
    const fallbackRerankerModel = String(configEnvelope.data?.modelBindings?.fallbackRerankerModel || "").trim();
    const runtimeStartupState = String(health.runtime?.startupState || catalog.startupState || "cold").trim().toLowerCase();
    const snapshotFreshness = String(health.runtime?.snapshotFreshness || catalog.snapshotFreshness || "cold").trim().toLowerCase();
    const silkAvailable = Boolean(health.silk?.available ?? health.runtime?.silk?.available);
    const silkVersion = String(health.silk?.version || health.runtime?.silk?.version || "").trim();
    const silkRoot = String(health.silk?.toolRoot || health.runtime?.silk?.toolRoot || "").trim();
    const dependencyPolicy = catalog.skillDependencyPolicy || health.skillDependencyPolicy || {};

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("扩展生态", "Extensions")}
                description={lt("管理 Skills、MCP 服务和候选排序。", "Manage skills, MCP services, and candidate routing order.")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label={t(lt("候选重排", "Candidate reranking"))} />
                        <Button onClick={() => void handleSaveConfig()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t(lt("保存", "Save"))}
                        </Button>
                        <Button variant="outline" onClick={() => void loadData()} disabled={reloading || saving}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t(lt("重新读取", "Reload"))}
                        </Button>
                        <Button onClick={() => void handleReloadSystem()} disabled={reloading || saving}>
                            {reloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            {t(lt("刷新扩展", "Refresh extensions"))}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip items={summaryItems} />
            {runtimeStartupState === "refreshing" ? (
                <StatusNotice
                    title={lt("扩展运行时正在后台刷新", "Extensions runtime is refreshing in the background")}
                    description={t(lt(`当前先展示${snapshotFreshness === "live" ? "live" : snapshotFreshness === "cached" ? "缓存" : "冷启动"}快照；Skills 与 MCP 会在后台继续完成加载。`, `Showing a ${snapshotFreshness === "live" ? "live" : snapshotFreshness === "cached" ? "cached" : "cold-start"} snapshot for now. Skills and MCP continue loading in the background.`))}
                    tone="info"
                />
            ) : null}
            {runtimeStartupState === "error" ? (
                <StatusNotice
                    title={lt("扩展运行时后台刷新失败", "Background refresh failed for extensions runtime")}
                    description={health.lastRefreshError || catalog.lastRefreshError || t(lt("当前继续展示缓存快照，请稍后手动刷新。", "The cached snapshot is still shown. Please refresh again later."))}
                    tone="warning"
                />
            ) : null}
            <StatusNotice
                title={silkAvailable ? lt("Silk 工具链已就绪", "Silk toolchain is ready") : lt("Silk 工具链未就绪", "Silk toolchain is not ready")}
                description={
                    silkAvailable
                        ? t(lt(`当前会优先为 Weixin / QQ 生成 Silk 原生语音。${silkVersion ? ` 版本：${silkVersion}。` : ""}${silkRoot ? ` 根目录：${silkRoot}` : ""}`, `Weixin / QQ will prefer native Silk audio now.${silkVersion ? ` Version: ${silkVersion}.` : ""}${silkRoot ? ` Root: ${silkRoot}` : ""}`))
                        : t(lt(`Weixin / QQ 当前会显式降级为附件音频，不会误报原生语音成功。${silkRoot ? ` 期望安装根：${silkRoot}` : ""}`, `Weixin / QQ will explicitly fall back to attachment audio right now.${silkRoot ? ` Expected install root: ${silkRoot}` : ""}`))
                }
                tone={silkAvailable ? "success" : "warning"}
            />

            <ConfigCard
                title={lt("候选重排", "Candidate reranking")}
                description={lt("控制 Skills 与 MCP 候选排序。", "Control the ordering of Skills and MCP candidates.")}
            >
                <div className="grid gap-6 xl:grid-cols-[1.2fr,1fr]">
                    <div className="space-y-5">
                        <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("启用扩展候选重排", "Enable extensions candidate reranking"))}</div>
                                <div className="text-xs leading-5 text-slate-500">{t(lt("只对词面召回池做精排，rerank 出错时会自动退回 lexical。", "Only rerank the lexical candidate pool. If reranking fails, the system falls back to lexical ordering automatically."))}</div>
                            </div>
                            <Switch checked={rerankEnabled} onCheckedChange={(checked) => updateConfig({ rerankPolicy: { enabled: checked } })} />
                        </div>

                        <div className="space-y-2">
                            <Label>{t(lt("扩展候选重排模型", "Extensions reranker model"))}</Label>
                            <Select
                                value={rerankerModel || "__empty__"}
                                onValueChange={(value) => updateConfig({ modelBindings: { rerankerModel: value === "__empty__" ? "" : value } })}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder={t(lt("未指定，回退全局重排模型", "Unset, fall back to the global reranker"))} />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__empty__">{t(lt("未指定，回退全局重排模型", "Unset, fall back to the global reranker"))}</SelectItem>
                                    {rerankModels.map((model) => (
                                        <SelectItem key={modelValue(model)} value={modelValue(model)}>
                                            {modelLabel(model)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs leading-5 text-slate-500">
                                {t(lt("推荐绑定本地 vLLM Rerank 服务。当前全局回退模型：", "Binding a local vLLM rerank service is recommended. Current global fallback model:"))}{fallbackRerankerModel || t(lt("未指定", "Unset"))}。
                            </p>
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm text-slate-600">
                        <div className="flex items-center justify-between gap-3"><span>{t(lt("当前策略", "Current policy"))}</span><Badge variant={rerankEnabled ? "default" : "secondary"}>{rerankEnabled ? t(lt("已启用", "Enabled")) : t(lt("已关闭", "Disabled"))}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{t(lt("专用模型绑定", "Dedicated binding"))}</span><Badge variant="outline">{rerankerModel || t(lt("未指定", "Unset"))}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{t(lt("全局回退", "Global fallback"))}</span><Badge variant="outline">{fallbackRerankerModel || t(lt("未指定", "Unset"))}</Badge></div>
                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-xs leading-6 text-slate-500">
                            {t(lt("提示：这里只有“排序权”，不会把 rerank 结果写进聊天正文，也不会替代 Skills 安装或 MCP 配置本身。", "Note: this only controls ranking. It does not write rerank output into chat content, and it does not replace skill installation or MCP configuration itself."))}
                        </div>
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={configEnvelope.source} savePath={configEnvelope.savePath} reloadRequired={configEnvelope.reloadRequired} />

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title={lt("已安装的 Skills", "Installed skills")} description={lt("查看当前可读取的 Skills。", "Inspect the skills currently available for reading.")} variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            {t(lt("Skills 目录：", "Skills root:"))}<span className="font-medium break-all text-slate-900">{catalog.skills.root}</span>
                        </div>
                        {catalog.skills.items.length === 0 ? (
                            <EmptyState title={t(lt("还没有可用 Skills", "No skills available yet"))} description={t(lt("你可以通过命令行安装或上传压缩包添加新的 Skills。", "Install skills via command line or upload a zip archive to add new ones."))} />
                        ) : (
                            catalog.skills.items.map((skill) => (
                                <div key={skill.name} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex items-center gap-2">
                                                <PackageCheck className="h-4 w-4 text-emerald-600" />
                                                <div className="text-sm font-semibold text-slate-900">{skill.name}</div>
                                            </div>
                                            <div className="line-clamp-2 text-sm leading-6 text-slate-600">{skill.description}</div>
                                            <div className="break-all rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">{skill.path}</div>
                                        </div>
                                        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </ConfigCard>

                <ConfigCard title={lt("MCP 服务", "MCP services")} description={lt("查看服务状态和工具数量。", "Inspect service status and tool counts.")} variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        {catalog.mcp.servers.length === 0 ? (
                            <EmptyState title={t(lt("还没有 MCP 服务", "No MCP services yet"))} description={t(lt("你可以导入一份 MCP JSON 配置，把新的 MCP 服务接到系统里。", "Import an MCP JSON configuration to connect new MCP services."))} />
                        ) : (
                            catalog.mcp.servers.map((server) => (
                                <div key={server.name} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-semibold text-slate-900">{server.name}</div>
                                                <Badge variant={server.status === "connected" ? "default" : server.status === "disabled" ? "secondary" : "destructive"}>{statusLabel(server.status, t)}</Badge>
                                                <Badge variant="outline">{server.transport}</Badge>
                                            </div>
                                            <div className="break-all text-xs text-slate-500">{server.target || t(lt("未提供命令或地址", "No command or target provided"))}</div>
                                            <div className="text-xs text-slate-600">{t(lt("可用工具：", "Available tools:"))}{server.toolCount}</div>
                                            <div className="flex flex-wrap gap-2">
                                                {server.tools.slice(0, 6).map((tool) => (
                                                    <Badge key={tool.name} variant="secondary">{tool.name}</Badge>
                                                ))}
                                                {server.tools.length > 6 ? <Badge variant="secondary">+{server.tools.length - 6}</Badge> : null}
                                            </div>
                                        </div>
                                        <Server className="mt-0.5 h-5 w-5 shrink-0 text-sky-600" />
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </ConfigCard>
            </div>

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title={lt("添加 Skills", "Add skills")} description={lt("通过命令或压缩包添加 Skills。", "Add skills via command line or zip archive.")} variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>{t(lt("命令行安装 Skills", "Install skills by command"))}</Label>
                            <Input value={commandInput} onChange={(event) => setCommandInput(event.target.value)} placeholder="npx skills add https://github.com/vercel-labs/skills --skill find-skills" />
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                                {t(lt("支持命令格式：", "Supported command format:"))}<span className="font-mono text-slate-900">npx skills add &lt;source&gt; [--skill &lt;name&gt;] [--overwrite]</span>。{t(lt("安装器会把 Skill 放到 ", "The installer places skills under "))}<span className="font-mono text-slate-900">~/.agents/skills</span>。
                            </div>
                            <div className="rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-3 text-xs leading-6 text-amber-900">
                                <div className="font-medium">
                                    {t(lt("Skill 依赖策略：仅允许写入 engine venv", "Skill dependency policy: engine venv only"))}
                                </div>
                                <div className="mt-1">
                                    {t(lt("Skill 文件本身仍安装到 ~/.agents/skills；若后续需要额外 Python 依赖，只允许受控安装到 ", "Skill files are still installed under ~/.agents/skills. If extra Python dependencies are needed later, they may only be installed in a controlled way into "))}<span className="font-mono">{dependencyPolicy.pythonTarget || "apps/v8-agent-os-engine/.venv"}</span>{t(lt("，不支持自由写入系统 Python 或全局 Node 环境。", ". Free installs into the system Python or global Node environment are not supported."))}
                                </div>
                            </div>
                            <div className="text-xs leading-5 text-slate-500">
                                {t(lt("也可以前往", "You can also browse"))}{" "}
                                <a href="https://skills.sh/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-600 underline">
                                    skills.sh
                                    <ExternalLink className="h-3 w-3" />
                                </a>
                                {t(lt("查找灵感。", "for inspiration."))}
                            </div>
                        </div>
                        {installResult ? (
                            <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{t(lt("来源", "Source"))}</Badge><span className="break-all">{installResult.source}</span></div>
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{t(lt("目标目录", "Target root"))}</Badge><span className="break-all">{installResult.targetRoot}</span></div>
                                <div className="grid gap-3 md:grid-cols-3">
                                    <StatPill label={t(lt("已安装", "Installed"))} value={installResult.installed.length} />
                                    <StatPill label={t(lt("冲突", "Conflicts"))} value={installResult.conflicts.length} />
                                    <StatPill label={t(lt("警告", "Warnings"))} value={installResult.warnings.length} />
                                </div>
                            </div>
                        ) : null}
                        <div className="flex flex-wrap gap-3">
                            <Button onClick={() => void handleCommandInstall()} disabled={installingCommand || !commandInput.trim()}>
                                <Terminal className="mr-2 h-4 w-4" />
                                {installingCommand ? t(lt("安装中...", "Installing...")) : t(lt("运行安装命令", "Run install command"))}
                            </Button>
                            <div className="flex min-w-0 flex-1 items-center gap-3">
                                <Input ref={fileInputRef} type="file" accept=".zip" onChange={handleZipUpload} disabled={uploadingZip} className="hidden" />
                                <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadingZip}>
                                    <Upload className="mr-2 h-4 w-4" />
                                    {t(lt("选择 Skills ZIP", "Choose skill ZIP"))}
                                </Button>
                                <div className="min-w-0 flex-1 rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-500">
                                    {zipFileLabel || t(lt("ZIP 顶层必须只有一个目录，且目录内至少包含一个 SKILL.md。", "The ZIP must contain exactly one top-level directory, and that directory must include at least one SKILL.md."))}
                                </div>
                                {uploadingZip ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                            </div>
                        </div>
                        {zipValidationError ? (
                            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                                {zipValidationError}
                            </div>
                        ) : null}
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                            <div className="font-medium text-slate-900">{t(lt("ZIP 导入前请确认", "Before importing a ZIP"))}</div>
                            <ul className="mt-2 space-y-1">
                                <li>{t(lt("1. 顶层只能有一个目录。", "1. The archive must contain exactly one top-level folder."))}</li>
                                <li>{t(lt("2. 该目录里至少要有一个 SKILL.md。", "2. That folder must contain at least one SKILL.md."))}</li>
                                <li>{t(lt("3. 根目录散文件、空包和非法路径都会被拒绝。", "3. Loose root files, empty archives, and unsafe paths are rejected."))}</li>
                            </ul>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title={lt("MCP 配置", "MCP configuration")} description={lt("导入 MCP JSON 并刷新服务。", "Import MCP JSON and refresh services.")} variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-3">
                            <StatPill label={t(lt("已连接服务", "Connected services"))} value={health.mcp.statusBreakdown.connected || 0} />
                            <StatPill label={t(lt("已停用服务", "Disabled services"))} value={health.mcp.statusBreakdown.disabled || 0} />
                            <StatPill label={t(lt("异常服务", "Errored services"))} value={health.mcp.statusBreakdown.error || 0} />
                        </div>
                        <Dialog open={mcpDialogOpen} onOpenChange={setMcpDialogOpen}>
                            <DialogTrigger asChild>
                                <Button variant="outline">
                                    <Plus className="mr-2 h-4 w-4" />
                                    {t(lt("从 JSON 导入", "Import from JSON"))}
                                </Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-2xl">
                                <DialogHeader>
                                    <DialogTitle>{t(lt("导入 MCP 配置", "Import MCP configuration"))}</DialogTitle>
                                    <DialogDescription>{t(lt("请把 MCP 服务提供方给出的 JSON 配置粘贴到输入框中。系统会先做结构校验，只有合法配置才会被合并。", "Paste the JSON configuration provided by the MCP service into the input below. The system validates the structure first and only merges valid configurations."))}</DialogDescription>
                                </DialogHeader>
                                <div className="space-y-3 py-4">
                                    <Textarea
                                        className="h-[300px] bg-slate-50 font-mono text-sm"
                                        value={mcpConfigInput}
                                        onChange={(event) => {
                                            setMcpConfigInput(event.target.value);
                                            if (mcpValidationError) setMcpValidationError("");
                                            if (mcpValidationSummary) setMcpValidationSummary("");
                                        }}
                                        placeholder={'{\n  "mcpServers": {\n    "example": {\n      "command": "npx",\n      "args": ["-y", "@example/server"]\n    }\n  }\n}'}
                                    />
                                    {mcpValidationSummary ? (
                                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
                                            {mcpValidationSummary}
                                        </div>
                                    ) : null}
                                    {mcpValidationError ? (
                                        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                                            {mcpValidationError}
                                        </div>
                                    ) : null}
                                </div>
                                <DialogFooter>
                                    <Button variant="outline" onClick={() => setMcpDialogOpen(false)}>{t(lt("取消", "Cancel"))}</Button>
                                    <Button onClick={() => void saveMcpConfig()} disabled={savingMcp}>{savingMcp ? t(lt("导入中...", "Importing...")) : t(lt("确认导入", "Confirm import"))}</Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            <div className="flex items-start gap-2">
                                <Wrench className="mt-0.5 h-4 w-4 text-sky-600" />
                                <div>{t(lt("普通保护和均衡保护默认不会阻断 Skills 目录发现、Skills 读取、MCP 服务发现和 MCP 工具读取。", "Daily and balanced protection do not block skill discovery, skill reads, MCP service discovery, or MCP tool reads by default."))}</div>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                            <div className="font-medium text-slate-900">{t(lt("JSON 结构要求", "JSON structure requirements"))}</div>
                            <ul className="mt-2 space-y-1">
                                <li>{t(lt("1. 根必须是 `mcpServers` 对象，或直接是 server map。", "1. The root must be an `mcpServers` object or a direct server map."))}</li>
                                <li>{t(lt("2. 每个 server 至少提供 command 或 url。", "2. Each server must provide either a command or a url."))}</li>
                                <li>{t(lt("3. args 必须是数组，env / headers 必须是对象。", "3. args must be arrays, while env and headers must be objects."))}</li>
                            </ul>
                        </div>
                    </div>
                </ConfigCard>
            </div>

        </AdminPageShell>
    );
}
