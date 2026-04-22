"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, FolderOpen, Loader2, Plus, Save, Trash2 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";

type ProjectsData = {
    defaultProjectId?: string | null;
    projects?: ProjectRecord[];
};

type ProjectRecord = {
    id: string;
    name?: string;
    workspaceId?: string;
    workspacePath?: string;
    defaultScope?: string;
    active?: boolean;
};

type WorkspacePathStatus = {
    exists?: boolean;
    isAbsolute?: boolean;
    writable?: boolean;
    reason?: string;
    isLegacyResidue?: boolean;
    legacyReason?: string;
    recommendedPath?: string;
};

type WorkspaceData = {
    agent_workspace_path?: string;
    pathStatus?: WorkspacePathStatus;
};

type WorkspaceRulesPayload = {
    workspacePath: string;
    path: string;
    exists: boolean;
    content: string;
    suggestedContent?: string;
    workspaceStatus?: WorkspacePathStatus;
    budgetDiagnostics?: {
        estimatedTokens?: number;
        budgetTokens?: number;
        truncated?: boolean;
        saveRejected?: boolean;
        omittedReason?: string;
    } | null;
};

type ProjectEditorState = {
    workspacePathDraft: string;
    agentsContent: string;
    agentsDirty: boolean;
    savingProject: boolean;
    deletingProject: boolean;
    rulesLoading: boolean;
    rulesSaving: boolean;
    rules: WorkspaceRulesPayload | null;
    loadedWorkspacePath: string;
};

const WORKSPACE_RULES_BUDGET_TOKENS = 10_000;
const DEFAULT_AGENTS_TEMPLATE = [
    "# Workspace Rules",
    "",
    "Add concise runtime instructions for this workspace here.",
    "Keep this file under 10000 estimated tokens.",
    "",
].join("\n");

function isAbsolutePath(value: string) {
    const normalized = String(value || "").trim();
    return /^[a-zA-Z]:[\\/]/.test(normalized) || normalized.startsWith("/") || normalized.startsWith("\\\\");
}

function deriveFolderName(value: string) {
    const normalized = String(value || "").trim().replace(/[\\/]+$/, "");
    if (!normalized) {
        return "";
    }
    const parts = normalized.split(/[\\/]+/).filter(Boolean);
    return parts.at(-1) || "";
}

function estimatePromptTokens(text: string) {
    const raw = String(text || "");
    if (!raw) {
        return 0;
    }
    let cjkCount = 0;
    let nonCjkVisible = 0;
    for (const char of raw) {
        const codepoint = char.codePointAt(0) || 0;
        if (
            (codepoint >= 0x4e00 && codepoint <= 0x9fff)
            || (codepoint >= 0x3400 && codepoint <= 0x4dbf)
            || (codepoint >= 0x3040 && codepoint <= 0x30ff)
            || (codepoint >= 0xac00 && codepoint <= 0xd7af)
        ) {
            cjkCount += 1;
        } else if (!/\s/.test(char)) {
            nonCjkVisible += 1;
        }
    }
    return cjkCount + Math.ceil(nonCjkVisible / 4);
}

function getWorkspaceRulesInitialContent(payload: WorkspaceRulesPayload | null) {
    if (!payload) {
        return DEFAULT_AGENTS_TEMPLATE;
    }
    return payload.content || payload.suggestedContent || DEFAULT_AGENTS_TEMPLATE;
}

function buildProjectEditors(projects: ProjectRecord[], previous: Record<string, ProjectEditorState>) {
    const next: Record<string, ProjectEditorState> = {};
    projects.forEach((project) => {
        const current = previous[project.id];
        next[project.id] = {
            workspacePathDraft: current?.workspacePathDraft ?? String(project.workspacePath || ""),
            agentsContent: current?.agentsContent ?? DEFAULT_AGENTS_TEMPLATE,
            agentsDirty: current?.agentsDirty ?? false,
            savingProject: current?.savingProject ?? false,
            deletingProject: current?.deletingProject ?? false,
            rulesLoading: current?.rulesLoading ?? false,
            rulesSaving: current?.rulesSaving ?? false,
            rules: current?.rules ?? null,
            loadedWorkspacePath: current?.loadedWorkspacePath ?? "",
        };
    });
    return next;
}

function sortProjects(projects: ProjectRecord[]) {
    return [...projects].sort((left, right) => {
        const leftKey = `${left.name || deriveFolderName(left.workspacePath || "")}:${left.id || ""}`.toLowerCase();
        const rightKey = `${right.name || deriveFolderName(right.workspacePath || "")}:${right.id || ""}`.toLowerCase();
        return leftKey.localeCompare(rightKey);
    });
}

export default function ProjectsWorkspacesPage() {
    const t = useT();
    const { toast } = useToast();

    const [projectsEnvelope, setProjectsEnvelope] = useState<ConfigRegistryEnvelope<ProjectsData> | null>(null);
    const [workspaceEnvelope, setWorkspaceEnvelope] = useState<ConfigRegistryEnvelope<WorkspaceData> | null>(null);
    const [loading, setLoading] = useState(true);

    const [workspaceDraft, setWorkspaceDraft] = useState("");
    const [workspaceSaving, setWorkspaceSaving] = useState(false);
    const [workspaceSaved, setWorkspaceSaved] = useState(false);
    const [defaultRules, setDefaultRules] = useState<WorkspaceRulesPayload | null>(null);
    const [defaultRulesDraft, setDefaultRulesDraft] = useState(DEFAULT_AGENTS_TEMPLATE);
    const [defaultRulesDirty, setDefaultRulesDirty] = useState(false);
    const [defaultRulesLoading, setDefaultRulesLoading] = useState(false);
    const [defaultRulesSaving, setDefaultRulesSaving] = useState(false);
    const defaultRulesPathRef = useRef("");
    const defaultRulesDirtyRef = useRef(false);

    const [newProjectPath, setNewProjectPath] = useState("");
    const [creatingProject, setCreatingProject] = useState(false);
    const [expandedProjectId, setExpandedProjectId] = useState<string | null>(null);
    const [projectEditors, setProjectEditors] = useState<Record<string, ProjectEditorState>>({});

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [projects, workspace] = await Promise.all([
                fetchConfigDomain<ProjectsData>("projects"),
                fetchConfigDomain<WorkspaceData>("workspace"),
            ]);
            const sortedProjects = sortProjects(Array.isArray(projects.data.projects) ? projects.data.projects : []);
            setProjectsEnvelope({
                ...projects,
                data: {
                    ...projects.data,
                    projects: sortedProjects,
                },
            });
            setWorkspaceEnvelope(workspace);
            setWorkspaceDraft(String(workspace.data.agent_workspace_path || ""));
            setProjectEditors((previous) => buildProjectEditors(sortedProjects, previous));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    useEffect(() => {
        defaultRulesPathRef.current = defaultRules?.workspacePath || "";
    }, [defaultRules]);

    useEffect(() => {
        defaultRulesDirtyRef.current = defaultRulesDirty;
    }, [defaultRulesDirty]);

    const loadWorkspaceRules = useCallback(async (workspacePath: string, target: "default" | { projectId: string }) => {
        if (!workspacePath.trim() || !isAbsolutePath(workspacePath)) {
            return;
        }
        if (target === "default") {
            setDefaultRulesLoading(true);
        } else {
            setProjectEditors((previous) => ({
                ...previous,
                [target.projectId]: {
                    ...previous[target.projectId],
                    rulesLoading: true,
                },
            }));
        }
        try {
            const response = await fetch(`/api/workspace/agents-rules?workspacePath=${encodeURIComponent(workspacePath)}`, { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.rulesLoadFailed"));
            }
            if (target === "default") {
                const previousWorkspacePath = defaultRulesPathRef.current;
                const nextWorkspacePath = String(payload?.workspacePath || "");
                const shouldResetContent = !defaultRulesDirtyRef.current || previousWorkspacePath !== nextWorkspacePath;
                setDefaultRules(payload);
                if (shouldResetContent) {
                    setDefaultRulesDraft(getWorkspaceRulesInitialContent(payload));
                }
                if (previousWorkspacePath !== nextWorkspacePath) {
                    setDefaultRulesDirty(false);
                }
            } else {
                setProjectEditors((previous) => {
                    const current = previous[target.projectId];
                    const nextContent = getWorkspaceRulesInitialContent(payload);
                    const shouldResetContent = !current?.agentsDirty || current?.loadedWorkspacePath !== payload.workspacePath;
                    return {
                        ...previous,
                        [target.projectId]: {
                            ...current,
                            rules: payload,
                            rulesLoading: false,
                            loadedWorkspacePath: payload.workspacePath,
                            agentsDirty: shouldResetContent ? false : current?.agentsDirty ?? false,
                            agentsContent: shouldResetContent ? nextContent : current?.agentsContent ?? nextContent,
                        },
                    };
                });
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.rulesLoadFailed");
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesLoadTitle"),
                description: message,
                variant: "destructive",
            });
            if (target !== "default") {
                setProjectEditors((previous) => ({
                    ...previous,
                    [target.projectId]: {
                        ...previous[target.projectId],
                        rulesLoading: false,
                    },
                }));
            }
        } finally {
            if (target === "default") {
                setDefaultRulesLoading(false);
            }
        }
    }, [t, toast]);

    const normalizedDefaultWorkspacePath = workspaceDraft.trim();
    useEffect(() => {
        if (!normalizedDefaultWorkspacePath || !isAbsolutePath(normalizedDefaultWorkspacePath)) {
            return;
        }
        if (!defaultRulesLoading && String(defaultRules?.workspacePath || "").trim() === normalizedDefaultWorkspacePath) {
            return;
        }
        const handle = window.setTimeout(() => {
            void loadWorkspaceRules(normalizedDefaultWorkspacePath, "default");
        }, 180);
        return () => window.clearTimeout(handle);
    }, [defaultRules?.workspacePath, defaultRulesLoading, loadWorkspaceRules, normalizedDefaultWorkspacePath]);

    const expandedProjectEditor = expandedProjectId ? projectEditors[expandedProjectId] : null;
    const expandedProjectPath = String(expandedProjectEditor?.workspacePathDraft || "").trim();
    const expandedProjectLoadedPath = String(expandedProjectEditor?.loadedWorkspacePath || "").trim();
    const expandedProjectRulesLoading = Boolean(expandedProjectEditor?.rulesLoading);
    const expandedProjectHasRules = Boolean(expandedProjectEditor?.rules);
    useEffect(() => {
        if (!expandedProjectId || !expandedProjectPath.trim() || !isAbsolutePath(expandedProjectPath)) {
            return;
        }
        if (expandedProjectRulesLoading || (expandedProjectHasRules && expandedProjectLoadedPath === expandedProjectPath)) {
            return;
        }
        const handle = window.setTimeout(() => {
            void loadWorkspaceRules(expandedProjectPath, { projectId: expandedProjectId });
        }, 180);
        return () => window.clearTimeout(handle);
    }, [expandedProjectHasRules, expandedProjectId, expandedProjectLoadedPath, expandedProjectPath, expandedProjectRulesLoading, loadWorkspaceRules]);

    const pickFolder = useCallback(async (initialPath: string) => {
        const response = await fetch("/api/workspace/folder-picker", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                initialPath,
                title: t("app.admin.dashboard.projects.workspaces.page.folderPicker.title"),
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload?.supported === false) {
            throw new Error(payload?.error || t("app.admin.dashboard.projects.workspaces.page.folderPicker.unavailable"));
        }
        if (payload?.cancelled) {
            return "";
        }
        return String(payload?.path || "").trim();
    }, [t]);

    const workspaceHasChanges = workspaceDraft.trim() !== String(workspaceEnvelope?.data.agent_workspace_path || "").trim();
    const workspaceValidationError = useMemo(() => {
        const normalized = workspaceDraft.trim();
        if (!normalized) return t("app.admin.dashboard.projects.workspaces.page.validation.required");
        if (!isAbsolutePath(normalized)) return t("app.admin.dashboard.projects.workspaces.page.validation.absolute");
        return "";
    }, [t, workspaceDraft]);

    const defaultRulesEstimatedTokens = useMemo(() => estimatePromptTokens(defaultRulesDraft), [defaultRulesDraft]);
    const defaultRulesOverBudget = defaultRulesEstimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS;

    const saveDefaultWorkspace = useCallback(async () => {
        if (!workspaceEnvelope) return;
        if (workspaceValidationError) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.saveTitle"),
                description: workspaceValidationError,
                variant: "destructive",
            });
            return;
        }
        setWorkspaceSaving(true);
        try {
            const next = await saveConfigDomain<WorkspaceData>("workspace", {
                data: {
                    agent_workspace_path: workspaceDraft.trim(),
                },
            });
            setWorkspaceEnvelope(next);
            setWorkspaceDraft(String(next.data.agent_workspace_path || ""));
            setWorkspaceSaved(true);
            window.setTimeout(() => setWorkspaceSaved(false), 1800);
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.defaultWorkspaceSaved"),
                description: workspaceDraft.trim(),
            });
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.saveTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.saveFailed"),
                variant: "destructive",
            });
        } finally {
            setWorkspaceSaving(false);
        }
    }, [workspaceDraft, workspaceEnvelope, workspaceValidationError, t, toast]);

    const saveDefaultRules = useCallback(async () => {
        if (!workspaceDraft.trim() || !isAbsolutePath(workspaceDraft)) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.validation.absolute"),
                variant: "destructive",
            });
            return;
        }
        if (defaultRulesOverBudget) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.error.rulesOverBudget", {
                    estimated: defaultRulesEstimatedTokens,
                    budget: WORKSPACE_RULES_BUDGET_TOKENS,
                }),
                variant: "destructive",
            });
            return;
        }
        setDefaultRulesSaving(true);
        try {
            const response = await fetch("/api/workspace/agents-rules", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    workspacePath: workspaceDraft.trim(),
                    content: defaultRulesDraft,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveFailed"));
            }
            setDefaultRules(payload);
            setDefaultRulesDraft(getWorkspaceRulesInitialContent(payload));
            setDefaultRulesDirty(false);
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.rulesSaved"),
                description: payload?.path || t("app.admin.dashboard.projects.workspaces.page.value.notSet"),
            });
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveFailed"),
                variant: "destructive",
            });
        } finally {
            setDefaultRulesSaving(false);
        }
    }, [defaultRulesDraft, defaultRulesEstimatedTokens, defaultRulesOverBudget, t, toast, workspaceDraft]);

    const handleCreateProject = useCallback(async () => {
        const normalized = newProjectPath.trim();
        if (!normalized || !isAbsolutePath(normalized)) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.projectCreateTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.validation.absolute"),
                variant: "destructive",
            });
            return;
        }
        setCreatingProject(true);
        try {
            const response = await fetch("/api/projects", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    workspacePath: normalized,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.projectCreateFailed"));
            }
            await load();
            setExpandedProjectId(String(payload?.id || ""));
            setNewProjectPath("");
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.projectCreated"),
                description: String(payload?.name || deriveFolderName(normalized) || payload?.id || ""),
            });
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.projectCreateTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.projectCreateFailed"),
                variant: "destructive",
            });
        } finally {
            setCreatingProject(false);
        }
    }, [load, newProjectPath, t, toast]);

    const patchProjectEditor = useCallback((projectId: string, updates: Partial<ProjectEditorState>) => {
        setProjectEditors((previous) => ({
            ...previous,
            [projectId]: {
                ...previous[projectId],
                ...updates,
            },
        }));
    }, []);

    const handleSaveProject = useCallback(async (project: ProjectRecord) => {
        const editor = projectEditors[project.id];
        const normalized = String(editor?.workspacePathDraft || "").trim();
        if (!normalized || !isAbsolutePath(normalized)) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.projectSaveTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.validation.absolute"),
                variant: "destructive",
            });
            return;
        }
        patchProjectEditor(project.id, { savingProject: true });
        try {
            const response = await fetch(`/api/projects/${project.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    workspacePath: normalized,
                    name: deriveFolderName(normalized) || project.name || project.id,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.projectSaveFailed"));
            }
            await load();
            setExpandedProjectId(project.id);
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.projectSaved"),
                description: String(payload?.name || project.name || project.id),
            });
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.projectSaveTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.projectSaveFailed"),
                variant: "destructive",
            });
        } finally {
            patchProjectEditor(project.id, { savingProject: false });
        }
    }, [load, patchProjectEditor, projectEditors, t, toast]);

    const handleDeleteProject = useCallback(async (project: ProjectRecord) => {
        if (!window.confirm(t("app.admin.dashboard.projects.workspaces.page.project.deleteConfirm", { id: project.id }))) {
            return;
        }
        patchProjectEditor(project.id, { deletingProject: true });
        try {
            const response = await fetch(`/api/projects/${project.id}`, {
                method: "DELETE",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.projectDeleteFailed"));
            }
            await load();
            if (expandedProjectId === project.id) {
                setExpandedProjectId(null);
            }
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.projectDeleted"),
                description: project.id,
            });
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.projectDeleteTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.projectDeleteFailed"),
                variant: "destructive",
            });
        } finally {
            patchProjectEditor(project.id, { deletingProject: false });
        }
    }, [expandedProjectId, load, patchProjectEditor, t, toast]);

    const handleSaveProjectRules = useCallback(async (project: ProjectRecord) => {
        const editor = projectEditors[project.id];
        const normalized = String(editor?.workspacePathDraft || "").trim();
        if (!normalized || !isAbsolutePath(normalized)) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.validation.absolute"),
                variant: "destructive",
            });
            return;
        }
        const estimatedTokens = estimatePromptTokens(editor?.agentsContent || "");
        if (estimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS) {
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: t("app.admin.dashboard.projects.workspaces.page.error.rulesOverBudget", {
                    estimated: estimatedTokens,
                    budget: WORKSPACE_RULES_BUDGET_TOKENS,
                }),
                variant: "destructive",
            });
            return;
        }
        patchProjectEditor(project.id, { rulesSaving: true });
        try {
            const response = await fetch("/api/workspace/agents-rules", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    workspacePath: normalized,
                    content: editor?.agentsContent || DEFAULT_AGENTS_TEMPLATE,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveFailed"));
            }
            patchProjectEditor(project.id, {
                rulesSaving: false,
                agentsDirty: false,
                rules: payload,
                loadedWorkspacePath: payload.workspacePath,
                agentsContent: getWorkspaceRulesInitialContent(payload),
            });
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.toast.rulesSaved"),
                description: payload?.path || t("app.admin.dashboard.projects.workspaces.page.value.notSet"),
            });
        } catch (error) {
            patchProjectEditor(project.id, { rulesSaving: false });
            toast({
                title: t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.error.rulesSaveFailed"),
                variant: "destructive",
            });
        }
    }, [patchProjectEditor, projectEditors, t, toast]);

    const projects = projectsEnvelope?.data.projects || [];
    const defaultWorkspaceStatus = defaultRules?.workspaceStatus || workspaceEnvelope?.data.pathStatus || {};

    if (loading || !projectsEnvelope || !workspaceEnvelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.projects.workspaces.page.k6dc301c9"
                description="app.admin.dashboard.projects.workspaces.page.description"
            />

            <div className="grid items-stretch gap-4 xl:grid-cols-2">
                <ConfigCard
                    title="app.admin.dashboard.projects.workspaces.page.defaultCard.title"
                    description="app.admin.dashboard.projects.workspaces.page.defaultCard.description"
                    bodyHeight="clamp"
                    bodyScroll="none"
                    className="h-full"
                    contentClassName="flex h-full flex-col gap-4"
                >
                    <div className="space-y-2">
                        <label className="text-sm font-medium text-slate-900">
                            {t("app.admin.dashboard.projects.workspaces.page.field.defaultWorkspace")}
                        </label>
                        <div className="flex flex-col gap-2 xl:flex-row">
                            <Input
                                value={workspaceDraft}
                                onChange={(event) => setWorkspaceDraft(event.target.value)}
                                placeholder={t("app.admin.dashboard.projects.workspaces.page.k63f45c6d")}
                                className="flex-1"
                            />
                            <Button
                                type="button"
                                variant="outline"
                                onClick={async () => {
                                    try {
                                        const selected = await pickFolder(workspaceDraft);
                                        if (selected) {
                                            setWorkspaceDraft(selected);
                                        }
                                    } catch (error) {
                                        toast({
                                            title: t("app.admin.dashboard.projects.workspaces.page.folderPicker.errorTitle"),
                                            description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.folderPicker.unavailable"),
                                            variant: "destructive",
                                        });
                                    }
                                }}
                            >
                                <FolderOpen className="mr-2 h-4 w-4" />
                                {t("app.admin.dashboard.projects.workspaces.page.folderPicker.choose")}
                            </Button>
                            <Button type="button" onClick={() => void saveDefaultWorkspace()} disabled={workspaceSaving || Boolean(workspaceValidationError) || !workspaceHasChanges}>
                                {workspaceSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {t("app.admin.dashboard.projects.workspaces.page.action.save")}
                            </Button>
                        </div>
                        <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
                            <span>{t("app.admin.dashboard.projects.workspaces.page.field.defaultWorkspaceHint")}</span>
                            <InlineSaveState saving={workspaceSaving} saved={workspaceSaved && !workspaceHasChanges} label="app.admin.dashboard.projects.workspaces.page.defaultSavedLabel" />
                        </div>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-3">
                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.existsLabel")} ok={Boolean(defaultWorkspaceStatus.exists)} okText={t("app.admin.dashboard.projects.workspaces.page.status.exists")} badText={t("app.admin.dashboard.projects.workspaces.page.status.missing")} />
                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.absoluteLabel")} ok={Boolean(defaultWorkspaceStatus.isAbsolute)} okText={t("app.admin.dashboard.projects.workspaces.page.status.absoluteOk")} badText={t("app.admin.dashboard.projects.workspaces.page.status.absoluteRequired")} />
                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.writableLabel")} ok={Boolean(defaultWorkspaceStatus.writable)} okText={t("app.admin.dashboard.projects.workspaces.page.status.writable")} badText={t("app.admin.dashboard.projects.workspaces.page.status.pending")} />
                    </div>

                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm text-slate-600">
                        <div className="font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.status.summaryTitle")}</div>
                        <div className="mt-2 leading-6">
                            {workspaceValidationError
                                || defaultWorkspaceStatus.reason
                                || t("app.admin.dashboard.projects.workspaces.page.status.singleChoiceHint")}
                        </div>
                    </div>

                    <div className="flex min-h-0 flex-1 flex-col rounded-2xl border border-slate-200 bg-white">
                        <div className="border-b border-slate-200 px-4 py-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.agentsRules.inlineTitle")}</div>
                                    <div className="mt-1 text-xs leading-5 text-slate-500">
                                        {t("app.admin.dashboard.projects.workspaces.page.agentsRules.singleChoiceHint")}
                                    </div>
                                </div>
                                <Button type="button" onClick={() => void saveDefaultRules()} disabled={defaultRulesSaving || defaultRulesLoading || defaultRulesOverBudget}>
                                    {defaultRulesSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                    {t("app.admin.dashboard.projects.workspaces.page.agentsRules.save")}
                                </Button>
                            </div>
                            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                <span className={cn("rounded-full border px-2 py-1", defaultRulesOverBudget ? "border-rose-200 bg-rose-50 text-rose-700" : "border-slate-200 bg-slate-50 text-slate-600")}>
                                    {t("app.admin.dashboard.projects.workspaces.page.agentsRules.budget", {
                                        estimated: defaultRulesEstimatedTokens,
                                        budget: WORKSPACE_RULES_BUDGET_TOKENS,
                                    })}
                                </span>
                                {defaultRules?.path ? <span className="truncate rounded-full border border-slate-200 bg-slate-50 px-2 py-1">{defaultRules.path}</span> : null}
                            </div>
                        </div>
                        <div className="min-h-0 flex-1 overflow-y-auto p-4">
                            {defaultRulesLoading ? (
                                <div className="flex h-40 items-center justify-center text-sm text-slate-500">
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    {t("app.admin.dashboard.projects.workspaces.page.agentsRules.loading")}
                                </div>
                            ) : (
                                <Textarea
                                    rows={18}
                                    value={defaultRulesDraft}
                                    onChange={(event) => {
                                        setDefaultRulesDraft(event.target.value);
                                        setDefaultRulesDirty(true);
                                    }}
                                    className="min-h-[320px] resize-none font-mono text-xs leading-6"
                                />
                            )}
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title="app.admin.dashboard.projects.workspaces.page.projectsCard.title"
                    description="app.admin.dashboard.projects.workspaces.page.projectsCard.description"
                    bodyHeight="clamp"
                    bodyScroll="none"
                    className="h-full"
                    contentClassName="flex h-full flex-col gap-4"
                >
                    <div className="space-y-2 rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                        <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.projectsCard.createTitle")}</div>
                        <div className="flex flex-col gap-2 xl:flex-row">
                            <Input
                                value={newProjectPath}
                                onChange={(event) => setNewProjectPath(event.target.value)}
                                placeholder={t("app.admin.dashboard.projects.workspaces.page.projectsCard.pathPlaceholder")}
                                className="flex-1"
                            />
                            <Button
                                type="button"
                                variant="outline"
                                onClick={async () => {
                                    try {
                                        const selected = await pickFolder(newProjectPath);
                                        if (selected) {
                                            setNewProjectPath(selected);
                                        }
                                    } catch (error) {
                                        toast({
                                            title: t("app.admin.dashboard.projects.workspaces.page.folderPicker.errorTitle"),
                                            description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.folderPicker.unavailable"),
                                            variant: "destructive",
                                        });
                                    }
                                }}
                            >
                                <FolderOpen className="mr-2 h-4 w-4" />
                                {t("app.admin.dashboard.projects.workspaces.page.folderPicker.choose")}
                            </Button>
                            <Button type="button" onClick={() => void handleCreateProject()} disabled={creatingProject || !newProjectPath.trim() || !isAbsolutePath(newProjectPath)}>
                                {creatingProject ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                                {t("app.admin.dashboard.projects.workspaces.page.projectsCard.create")}
                            </Button>
                        </div>
                        <div className="text-xs leading-5 text-slate-500">
                            {t("app.admin.dashboard.projects.workspaces.page.projectsCard.derivedName", {
                                name: deriveFolderName(newProjectPath) || t("app.admin.dashboard.projects.workspaces.page.value.notSet"),
                            })}
                        </div>
                    </div>

                    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
                        {projects.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-slate-300 p-6 text-sm text-slate-500">
                                {t("app.admin.dashboard.projects.workspaces.page.projectsCard.empty")}
                            </div>
                        ) : (
                            projects.map((project) => {
                                const expanded = expandedProjectId === project.id;
                                const editor = projectEditors[project.id];
                                const projectRulesEstimatedTokens = estimatePromptTokens(editor?.agentsContent || "");
                                const projectRulesOverBudget = projectRulesEstimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS;
                                const projectStatus = editor?.rules?.workspaceStatus || {};
                                return (
                                    <div key={project.id} className="rounded-2xl border border-slate-200 bg-white">
                                        <button
                                            type="button"
                                            onClick={() => setExpandedProjectId(expanded ? null : project.id)}
                                            className="flex w-full items-center justify-between gap-3 px-4 py-4 text-left"
                                        >
                                            <div className="min-w-0">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="font-medium text-slate-900">{project.name || deriveFolderName(project.workspacePath || "") || project.id}</span>
                                                    <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-600">{project.id}</span>
                                                </div>
                                                <div className="mt-1 text-xs text-slate-500">{project.workspacePath || t("app.admin.dashboard.projects.workspaces.page.value.notSet")}</div>
                                            </div>
                                            <ChevronDown className={cn("h-4 w-4 text-slate-500 transition-transform", expanded ? "rotate-180" : "")} />
                                        </button>

                                        {expanded ? (
                                            <div className="border-t border-slate-200 px-4 pb-4 pt-3">
                                                <div className="space-y-4">
                                                    <div className="flex flex-col gap-2 xl:flex-row">
                                                        <Input
                                                            value={editor?.workspacePathDraft || ""}
                                                            onChange={(event) => patchProjectEditor(project.id, {
                                                                workspacePathDraft: event.target.value,
                                                            })}
                                                            placeholder={t("app.admin.dashboard.projects.workspaces.page.projectsCard.pathPlaceholder")}
                                                            className="flex-1"
                                                        />
                                                        <Button
                                                            type="button"
                                                            variant="outline"
                                                            onClick={async () => {
                                                                try {
                                                                    const selected = await pickFolder(editor?.workspacePathDraft || "");
                                                                    if (selected) {
                                                                        patchProjectEditor(project.id, { workspacePathDraft: selected });
                                                                    }
                                                                } catch (error) {
                                                                    toast({
                                                                        title: t("app.admin.dashboard.projects.workspaces.page.folderPicker.errorTitle"),
                                                                        description: error instanceof Error ? error.message : t("app.admin.dashboard.projects.workspaces.page.folderPicker.unavailable"),
                                                                        variant: "destructive",
                                                                    });
                                                                }
                                                            }}
                                                        >
                                                            <FolderOpen className="mr-2 h-4 w-4" />
                                                            {t("app.admin.dashboard.projects.workspaces.page.folderPicker.choose")}
                                                        </Button>
                                                        <Button type="button" onClick={() => void handleSaveProject(project)} disabled={editor?.savingProject}>
                                                            {editor?.savingProject ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                                            {t("app.admin.dashboard.projects.workspaces.page.projectsCard.saveProject")}
                                                        </Button>
                                                        <Button type="button" variant="destructive" onClick={() => void handleDeleteProject(project)} disabled={editor?.deletingProject}>
                                                            {editor?.deletingProject ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                                                            {t("app.admin.dashboard.projects.workspaces.page.projectsCard.deleteProject")}
                                                        </Button>
                                                    </div>

                                                    <div className="grid gap-3 sm:grid-cols-3">
                                                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.existsLabel")} ok={Boolean(projectStatus.exists)} okText={t("app.admin.dashboard.projects.workspaces.page.status.exists")} badText={t("app.admin.dashboard.projects.workspaces.page.status.missing")} />
                                                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.absoluteLabel")} ok={Boolean(projectStatus.isAbsolute)} okText={t("app.admin.dashboard.projects.workspaces.page.status.absoluteOk")} badText={t("app.admin.dashboard.projects.workspaces.page.status.absoluteRequired")} />
                                                        <StatusChip label={t("app.admin.dashboard.projects.workspaces.page.status.writableLabel")} ok={Boolean(projectStatus.writable)} okText={t("app.admin.dashboard.projects.workspaces.page.status.writable")} badText={t("app.admin.dashboard.projects.workspaces.page.status.pending")} />
                                                    </div>

                                                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-5 text-slate-500">
                                                        {t("app.admin.dashboard.projects.workspaces.page.projectsCard.singleChoiceHint")}
                                                    </div>

                                                    <div className="rounded-2xl border border-slate-200">
                                                        <div className="border-b border-slate-200 px-4 py-3">
                                                            <div className="flex items-center justify-between gap-3">
                                                                <div>
                                                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.agentsRules.projectTitle")}</div>
                                                                    <div className="mt-1 text-xs text-slate-500">{editor?.rules?.path || t("app.admin.dashboard.projects.workspaces.page.value.notSet")}</div>
                                                                </div>
                                                                <Button type="button" onClick={() => void handleSaveProjectRules(project)} disabled={editor?.rulesSaving || editor?.rulesLoading || projectRulesOverBudget}>
                                                                    {editor?.rulesSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                                                    {t("app.admin.dashboard.projects.workspaces.page.agentsRules.save")}
                                                                </Button>
                                                            </div>
                                                            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                                                <span className={cn("rounded-full border px-2 py-1", projectRulesOverBudget ? "border-rose-200 bg-rose-50 text-rose-700" : "border-slate-200 bg-slate-50 text-slate-600")}>
                                                                    {t("app.admin.dashboard.projects.workspaces.page.agentsRules.budget", {
                                                                        estimated: projectRulesEstimatedTokens,
                                                                        budget: WORKSPACE_RULES_BUDGET_TOKENS,
                                                                    })}
                                                                </span>
                                                                {editor?.rulesLoading ? <span>{t("app.admin.dashboard.projects.workspaces.page.agentsRules.loading")}</span> : null}
                                                            </div>
                                                        </div>
                                                        <div className="p-4">
                                                            <Textarea
                                                                rows={12}
                                                                value={editor?.agentsContent || DEFAULT_AGENTS_TEMPLATE}
                                                                onChange={(event) => patchProjectEditor(project.id, {
                                                                    agentsContent: event.target.value,
                                                                    agentsDirty: true,
                                                                })}
                                                                className="min-h-[260px] resize-none font-mono text-xs leading-6"
                                                            />
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        ) : null}
                                    </div>
                                );
                            })
                        )}
                    </div>
                </ConfigCard>
            </div>

            <SourceMetaRow source={workspaceEnvelope.source} savePath={workspaceEnvelope.savePath} reloadRequired={workspaceEnvelope.reloadRequired} />
            <SourceMetaRow source={projectsEnvelope.source} savePath={projectsEnvelope.savePath} reloadRequired={projectsEnvelope.reloadRequired} />
        </AdminPageShell>
    );
}

function StatusChip({
    label,
    ok,
    okText,
    badText,
}: {
    label: string;
    ok: boolean;
    okText: string;
    badText: string;
}) {
    return (
        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
            <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{label}</div>
            <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                {ok ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                {ok ? okText : badText}
            </div>
        </div>
    );
}
