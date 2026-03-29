"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, FolderOpen, Loader2, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
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
};

type WorkspaceData = {
    agent_workspace_path?: string;
    pathStatus?: WorkspacePathStatus;
};

function formatPathSummary(value?: string) {
    if (!value) return "未设置";
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
        if (!normalized) return "主工作区路径不能为空。";
        if (!isAbsolutePath(normalized)) return "主工作区必须使用绝对路径。";
        return "";
    }, [workspaceDraft]);

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
            setError(saveError instanceof Error ? saveError.message : "保存主工作区失败");
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
                title="项目与工作区"
                description="设置默认工作区和项目级覆盖关系。"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved && !workspaceHasChanges} label="主工作区" />
                        <Button onClick={() => void saveWorkspace()} disabled={saving || Boolean(localValidationError) || !workspaceHasChanges}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            保存主工作区
                        </Button>
                    </div>
                }
            />

            <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                <ConfigCard
                    title="主工作区"
                    description="设置系统默认执行目录。"
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-slate-900">默认执行目录</label>
                            <Input
                                value={workspaceDraft}
                                onChange={(event) => {
                                    setWorkspaceDraft(event.target.value);
                                    if (error) setError("");
                                }}
                                placeholder="例如：C:\\Users\\你的账户\\.v8-agent-os\\workspace"
                            />
                            <div className="text-xs leading-5 text-slate-500">
                                只接受绝对路径。这里不要求目录当前已存在，但建议父目录可写。
                            </div>
                        </div>

                        <div className="grid gap-3 sm:grid-cols-3">
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">目录存在</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.exists ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                    {pathStatus.exists ? "已存在" : "尚未创建"}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">绝对路径</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.isAbsolute ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-rose-600" />}
                                    {pathStatus.isAbsolute ? "符合要求" : "需要绝对路径"}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">写入权限</div>
                                <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    {pathStatus.writable ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                    {pathStatus.writable ? "可写" : "待确认"}
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
                            <div className="font-medium text-slate-900">状态说明</div>
                            <div className="mt-2 leading-6">{error || localValidationError || pathStatus.reason || "目录状态正常，可作为默认执行目录。"}</div>
                            {pathStatus.writableTarget ? (
                                <div className="mt-2 text-xs text-slate-500">写入检测目录：{pathStatus.writableTarget}</div>
                            ) : null}
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title="项目级工作区与 scope"
                    description="项目绑定后会覆盖主工作区。"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm leading-6 text-slate-600">
                            只有项目或会话显式绑定时，系统才会用项目级工作区。
                        </div>
                        <Link href="/admin/memory?tab=projects">
                            <Button className="w-full">
                                <FolderOpen className="mr-2 h-4 w-4" />
                                打开项目管理入口
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
