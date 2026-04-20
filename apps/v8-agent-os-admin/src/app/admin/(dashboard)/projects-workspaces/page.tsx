"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, FolderOpen, Loader2, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type ProjectsData = {
    defaultProjectId?: string | null;
    projects?: Array<{ id?: string; name?: string }>;
};

type WorkspacePathStatus = {
    exists?: boolean;
    isAbsolute?: boolean;
    writable?: boolean;
    writableTarget?: string;
    reason?: string;
    isLegacyResidue?: boolean;
    legacyReason?: string;
    recommendedPath?: string;
    autoCreateAllowed?: boolean;
};

type WorkspaceData = {
    agent_workspace_path?: string;
    pathStatus?: WorkspacePathStatus;
};

function formatPathSummary(value?: string) {
    if (!value) return "app.admin.dashboard.projects.workspaces.page.value.notSet";
    if (value.length <= 44) return value;
    const parts = value.split(/[\\/]+/).filter(Boolean);
    if (parts.length <= 3) return value;
    return `...\\${parts.slice(-3).join("\\")}`;
}

function isAbsolutePath(value: string) {
    const normalized = String(value || "").trim();
    return /^[a-zA-Z]:[\\/]/.test(normalized) || normalized.startsWith("/") || normalized.startsWith("\\\\");
}

export default function ProjectsWorkspacesPage() {
    const t = useT();
    const [projectsEnvelope, setProjectsEnvelope] = useState<ConfigRegistryEnvelope<ProjectsData> | null>(null);
    const [workspaceEnvelope, setWorkspaceEnvelope] = useState<ConfigRegistryEnvelope<WorkspaceData> | null>(null);
    const [workspaceDraft, setWorkspaceDraft] = useState("");
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const [projects, workspace] = await Promise.all([
                fetchConfigDomain<ProjectsData>("projects"),
                fetchConfigDomain<WorkspaceData>("workspace"),
            ]);
            setProjectsEnvelope(projects);
            setWorkspaceEnvelope(workspace);
            setWorkspaceDraft(String(workspace.data.agent_workspace_path || ""));
            setError("");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
    }, []);

    const localValidationError = useMemo(() => {
        const normalized = workspaceDraft.trim();
        if (!normalized) return t("app.admin.dashboard.projects.workspaces.page.validation.required");
        if (!isAbsolutePath(normalized)) return t("app.admin.dashboard.projects.workspaces.page.validation.absolute");
        return "";
    }, [t, workspaceDraft]);

    const saveWorkspace = async () => {
        if (!workspaceEnvelope) return;
        if (localValidationError) {
            setError(localValidationError);
            return;
        }
        setSaving(true);
        setError("");
        try {
            const next = await saveConfigDomain<WorkspaceData>("workspace", {
                data: {
                    agent_workspace_path: workspaceDraft.trim(),
                },
            });
            setWorkspaceEnvelope(next);
            setWorkspaceDraft(String(next.data.agent_workspace_path || ""));
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } catch (saveError) {
            setError(saveError instanceof Error ? saveError.message : t("app.admin.dashboard.projects.workspaces.page.error.saveFailed"));
        } finally {
            setSaving(false);
        }
    };

    if (loading || !projectsEnvelope || !workspaceEnvelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    const pathStatus = workspaceEnvelope.data.pathStatus || {};
    const workspaceHasChanges = workspaceDraft.trim() !== String(workspaceEnvelope.data.agent_workspace_path || "").trim();

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.projects.workspaces.page.k6dc301c9"
                description="app.admin.dashboard.projects.workspaces.page.k385d9359"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved && !workspaceHasChanges} label="app.admin.dashboard.projects.workspaces.page.kbaeeefa6" />
                        <Button onClick={() => void saveWorkspace()} disabled={saving || Boolean(localValidationError) || !workspaceHasChanges}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.projects.workspaces.page.action.save")}
                        </Button>
                    </div>
                }
            />

            <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                <ConfigCard
                    title="app.admin.dashboard.projects.workspaces.page.kbaeeefa6"
                    description="app.admin.dashboard.projects.workspaces.page.k2d761c72"
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.field.defaultWorkspace")}</label>
                            <Input
                                value={workspaceDraft}
                                onChange={(event) => {
                                    setWorkspaceDraft(event.target.value);
                                    if (error) setError("");
                                }}
                                placeholder="app.admin.dashboard.projects.workspaces.page.k63f45c6d"
                            />
                            <div className="text-xs leading-5 text-slate-500">
                                {t("app.admin.dashboard.projects.workspaces.page.field.defaultWorkspaceHint")}
                            </div>
                        </div>

                        <div className="grid gap-3 sm:grid-cols-3">
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("app.admin.dashboard.projects.workspaces.page.status.existsLabel")}</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.exists ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                    {pathStatus.exists ? t("app.admin.dashboard.projects.workspaces.page.status.exists") : t("app.admin.dashboard.projects.workspaces.page.status.missing")}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("app.admin.dashboard.projects.workspaces.page.status.absoluteLabel")}</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.isAbsolute ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-rose-600" />}
                                    {pathStatus.isAbsolute ? t("app.admin.dashboard.projects.workspaces.page.status.absoluteOk") : t("app.admin.dashboard.projects.workspaces.page.status.absoluteRequired")}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("app.admin.dashboard.projects.workspaces.page.status.writableLabel")}</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.writable ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                    {pathStatus.writable ? t("app.admin.dashboard.projects.workspaces.page.status.writable") : t("app.admin.dashboard.projects.workspaces.page.status.pending")}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 sm:col-span-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("app.admin.dashboard.projects.workspaces.page.status.pathTypeLabel")}</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.isLegacyResidue ? (
                                        <AlertTriangle className="h-4 w-4 text-rose-600" />
                                    ) : (
                                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                    )}
                                    {pathStatus.isLegacyResidue ? t("app.admin.dashboard.projects.workspaces.page.status.legacyResidue") : t("app.admin.dashboard.projects.workspaces.page.status.canonicalWorkspace")}
                                </div>
                                <div className="mt-2 text-xs leading-5 text-slate-500">
                                    {pathStatus.isLegacyResidue
                                        ? t("app.admin.dashboard.projects.workspaces.page.status.legacyDescription")
                                        : t("app.admin.dashboard.projects.workspaces.page.status.canonicalDescription")}
                                </div>
                                {pathStatus.isLegacyResidue && pathStatus.recommendedPath ? (
                                    <div className="mt-2 text-xs leading-5 text-rose-600">
                                        {t("app.admin.dashboard.projects.workspaces.page.status.recommendedPath")} {t(formatPathSummary(pathStatus.recommendedPath))}
                                    </div>
                                ) : null}
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
                            <div className="font-medium text-slate-900">{t("app.admin.dashboard.projects.workspaces.page.status.summaryTitle")}</div>
                            <div className="mt-2 leading-6">
                                {error
                                    || localValidationError
                                    || pathStatus.reason
                                    || (pathStatus.isLegacyResidue
                                        ? t("app.admin.dashboard.projects.workspaces.page.status.legacyRejected")
                                        : t("app.admin.dashboard.projects.workspaces.page.status.normal"))}
                            </div>
                            {pathStatus.writableTarget ? (
                                <div className="mt-2 text-xs text-slate-500">{t("app.admin.dashboard.projects.workspaces.page.status.writeProbe")} {pathStatus.writableTarget}</div>
                            ) : null}
                            {pathStatus.isLegacyResidue && pathStatus.legacyReason ? (
                                <div className="mt-2 text-xs leading-5 text-rose-600">
                                    {t("app.admin.dashboard.projects.workspaces.page.status.legacyRulePrefix")} {pathStatus.legacyReason}。{t("app.admin.dashboard.projects.workspaces.page.status.legacyRuleSuffix")}
                                </div>
                            ) : null}
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title="app.admin.dashboard.projects.workspaces.page.k8b859122"
                    description="app.admin.dashboard.projects.workspaces.page.kb741bb0c"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm leading-6 text-slate-600">
                            {t("app.admin.dashboard.projects.workspaces.page.projectCard.hint")}
                        </div>
                        <Link href="/admin/memory?tab=projects">
                            <Button className="w-full">
                                <FolderOpen className="mr-2 h-4 w-4" />
                                {t("app.admin.dashboard.projects.workspaces.page.projectCard.open")}
                            </Button>
                        </Link>
                    </div>
                </ConfigCard>
            </div>

            <SourceMetaRow source={workspaceEnvelope.source} savePath={workspaceEnvelope.savePath} reloadRequired={workspaceEnvelope.reloadRequired} />
            <SourceMetaRow source={projectsEnvelope.source} savePath={projectsEnvelope.savePath} reloadRequired={projectsEnvelope.reloadRequired} />
        </AdminPageShell>
    );
}
