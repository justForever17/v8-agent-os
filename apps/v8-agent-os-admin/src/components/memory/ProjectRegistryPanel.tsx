"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderTree, Plus, RefreshCw, Save, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export interface ProjectDescriptor {
    id: string;
    name: string;
    description?: string;
    workspaceId?: string;
    workspacePath?: string;
    defaultScope?: string;
    tags?: string[];
    active?: boolean;
}

const EMPTY_FORM: ProjectDescriptor = {
    id: "",
    name: "",
    description: "",
    workspaceId: "",
    workspacePath: "",
    defaultScope: "",
    tags: [],
    active: true,
};

function sortProjects(projects: ProjectDescriptor[]) {
    return [...projects].sort((left, right) => {
        if (left.id === right.id) return 0;
        return left.id.localeCompare(right.id);
    });
}

export function ProjectRegistryPanel() {
    const { toast } = useToast();
    const t = useT();
    const [projects, setProjects] = useState<ProjectDescriptor[]>([]);
    const [defaultProjectId, setDefaultProjectId] = useState<string | null>(null);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [form, setForm] = useState<ProjectDescriptor>(EMPTY_FORM);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const isEditing = useMemo(() => Boolean(selectedId), [selectedId]);
    const fieldClassName = "border-border/70 bg-card text-foreground placeholder:text-muted-foreground/70";
    const readonlyFieldClassName = `${fieldClassName} bg-muted/35 text-foreground`;

    const loadProjects = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/projects", { cache: "no-store" });
            if (!res.ok) {
                throw new Error(`Load failed: ${res.status}`);
            }
            const data = await res.json();
            setProjects(sortProjects(Array.isArray(data?.projects) ? data.projects : []));
            setDefaultProjectId(typeof data?.defaultProjectId === "string" ? data.defaultProjectId : null);
        } catch (error) {
            console.error("Failed to load projects:", error);
            toast({
                title: t("项目加载失败"),
                description: t("未能读取项目注册表。"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [t, toast]);

    useEffect(() => {
        void loadProjects();
    }, [loadProjects]);

    const resetForm = useCallback(() => {
        setSelectedId(null);
        setForm(EMPTY_FORM);
    }, []);

    const openCreateDrawer = useCallback(() => {
        resetForm();
        setDrawerOpen(true);
    }, [resetForm]);

    const openProjectDrawer = useCallback((project: ProjectDescriptor) => {
        setSelectedId(project.id);
        setForm({
            ...project,
            tags: Array.isArray(project.tags) ? project.tags : [],
        });
        setDrawerOpen(true);
    }, []);

    const handleSave = useCallback(async () => {
        if (!form.id.trim() || !form.name.trim()) {
            toast({
                title: t("信息不完整"),
                description: t("项目 ID 和名称不能为空。"),
                variant: "destructive",
            });
            return;
        }

        setSaving(true);
        try {
            const payload = {
                id: form.id.trim(),
                name: form.name.trim(),
                description: form.description?.trim() || undefined,
                workspaceId: form.workspaceId?.trim() || undefined,
                workspacePath: form.workspacePath?.trim() || undefined,
                defaultScope: form.defaultScope?.trim() || undefined,
                tags: Array.isArray(form.tags) ? form.tags.filter(Boolean) : [],
                active: form.active !== false,
            };

            const res = await fetch(isEditing ? `/api/projects/${form.id}` : "/api/projects", {
                method: isEditing ? "PATCH" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                throw new Error(`Save failed: ${res.status}`);
            }

            await loadProjects();
            setDrawerOpen(false);
            resetForm();
            toast({
                title: isEditing ? t("项目已更新") : t("项目已创建"),
                description: t(lt(`${payload.name} 已写入项目注册表。`, `${payload.name} was saved to the project registry.`)),
            });
        } catch (error) {
            console.error("Failed to save project:", error);
            toast({
                title: t("保存失败"),
                description: t("项目注册表写入失败，请稍后重试。"),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    }, [form, isEditing, loadProjects, resetForm, t, toast]);

    const handleDelete = useCallback(async (projectId: string) => {
        if (!window.confirm(t(lt(`确定删除项目 ${projectId} 吗？`, `Delete project ${projectId}?`)))) {
            return;
        }

        try {
            const res = await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
            if (!res.ok) {
                throw new Error(`Delete failed: ${res.status}`);
            }
            await loadProjects();
            if (selectedId === projectId) {
                setDrawerOpen(false);
                resetForm();
            }
            toast({
                title: t("项目已删除"),
                description: t(lt(`${projectId} 已从项目注册表移除。`, `${projectId} was removed from the registry.`)),
            });
        } catch (error) {
            console.error("Failed to delete project:", error);
            toast({
                title: t("删除失败"),
                description: t("项目删除失败，请稍后重试。"),
                variant: "destructive",
            });
        }
    }, [loadProjects, resetForm, selectedId, t, toast]);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4 rounded-2xl border border-border/60 bg-card/40 px-5 py-4">
                <div>
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        <FolderTree className="h-5 w-5 text-primary" />
                        {t("项目注册表")}
                    </h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {t("这里可以查看和编辑项目的说明、工作区和默认范围。")}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={() => void loadProjects()}>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        {t("刷新")}
                    </Button>
                    <Button onClick={openCreateDrawer}>
                        <Plus className="mr-2 h-4 w-4" />
                        {t("新建项目")}
                    </Button>
                </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {loading ? (
                    <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                        {t("项目注册表加载中...")}
                    </div>
                ) : projects.length === 0 ? (
                    <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                        {t("还没有项目。先创建一个项目，方便会话和自动任务复用同一套项目范围。")}
                    </div>
                ) : (
                    projects.map((project) => (
                        <button
                            key={project.id}
                            type="button"
                            onClick={() => openProjectDrawer(project)}
                            className="group rounded-2xl border border-border/60 bg-gradient-to-br from-card via-card to-muted/20 p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lg hover:shadow-primary/5"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <p className="truncate font-semibold">{project.name}</p>
                                        <Badge variant="secondary" className="font-mono text-[11px]">
                                            {project.id}
                                        </Badge>
                                        {defaultProjectId === project.id ? <Badge>{t("默认")}</Badge> : null}
                                    </div>
                                    <p className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">
                                        {project.description || t("暂无描述，点击后可查看全文并编辑。")}
                                    </p>
                                </div>
                            </div>

                            <div className="mt-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                {project.defaultScope ? (
                                    <span className="rounded-full border border-primary/15 bg-primary/5 px-2 py-1 font-mono text-primary/80">
                                        {project.defaultScope}
                                    </span>
                                ) : null}
                                {project.workspaceId ? (
                                    <span className="rounded-full border px-2 py-1">workspace: {project.workspaceId}</span>
                                ) : null}
                                {Array.isArray(project.tags) && project.tags.length > 0
                                    ? project.tags.slice(0, 3).map((tag) => (
                                        <span key={tag} className="rounded-full border px-2 py-1">
                                            #{tag}
                                        </span>
                                    ))
                                    : null}
                            </div>
                        </button>
                    ))
                )}
            </div>

            <Dialog
                open={drawerOpen}
                onOpenChange={(open) => {
                    setDrawerOpen(open);
                    if (!open) {
                        resetForm();
                    }
                }}
            >
                <DialogContent className="left-auto right-0 top-0 h-screen max-w-md translate-x-0 translate-y-0 overflow-y-auto border-l border-border/70 bg-background p-0 shadow-2xl shadow-black/20 sm:max-w-md sm:rounded-none">
                    <div className="flex h-full flex-col">
                        <DialogHeader className="border-b border-border/60 px-5 py-4">
                            <DialogTitle>{isEditing ? t("查看 / 编辑项目") : t("创建项目")}</DialogTitle>
                            <DialogDescription className="text-foreground/70">
                                {t("这里可以集中查看和修改项目说明、工作区目录和默认范围。")}
                            </DialogDescription>
                        </DialogHeader>

                        <div className="flex-1 space-y-5 px-5 py-5">
                            <div className="rounded-2xl border border-border/60 bg-muted/15 p-4">
                                <p className="text-xs uppercase tracking-[0.22em] text-foreground/55">{t("原始记录")}</p>
                                <pre className="mt-3 whitespace-pre-wrap break-all text-xs leading-6 text-foreground/78">
                                    {JSON.stringify(
                                        {
                                            id: form.id || undefined,
                                            name: form.name || undefined,
                                            description: form.description || undefined,
                                            workspaceId: form.workspaceId || undefined,
                                            workspacePath: form.workspacePath || undefined,
                                            defaultScope: form.defaultScope || undefined,
                                            tags: form.tags || [],
                                            active: form.active !== false,
                                        },
                                        null,
                                        2,
                                    )}
                                </pre>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-id">{t("项目 ID")}</Label>
                                <Input
                                    id="project-id"
                                    value={form.id}
                                    readOnly={isEditing}
                                    aria-readonly={isEditing}
                                    onChange={(event) => setForm((prev) => ({ ...prev, id: event.target.value }))}
                                    placeholder={t("例如 v8-agent-os")}
                                    className={isEditing ? readonlyFieldClassName : fieldClassName}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-name">{t("项目名称")}</Label>
                                <Input
                                    id="project-name"
                                    value={form.name}
                                    onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                                    placeholder={t("例如 V8 Agent OS 主工程")}
                                    className={fieldClassName}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-description">{t("描述全文")}</Label>
                                <Textarea
                                    id="project-description"
                                    value={form.description || ""}
                                    onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                                    rows={8}
                                    className={`resize-none leading-6 ${fieldClassName}`}
                                    placeholder={t("写清楚这个项目是做什么的，以及需要长期记住的约束。")}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-workspace-id">{t("工作区标识")}</Label>
                                <Input
                                    id="project-workspace-id"
                                    value={form.workspaceId || ""}
                                    onChange={(event) => setForm((prev) => ({ ...prev, workspaceId: event.target.value }))}
                                    placeholder={t("例如 main-workspace")}
                                    className={fieldClassName}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-workspace-path">{t("工作区目录")}</Label>
                                <Input
                                    id="project-workspace-path"
                                    value={form.workspacePath || ""}
                                    onChange={(event) => setForm((prev) => ({ ...prev, workspacePath: event.target.value }))}
                                    placeholder={t("例如 E:\\Projects\\V8-Agent-OS")}
                                    className={fieldClassName}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-default-scope">{t("默认范围")}</Label>
                                <Input
                                    id="project-default-scope"
                                    value={form.defaultScope || ""}
                                    onChange={(event) => setForm((prev) => ({ ...prev, defaultScope: event.target.value }))}
                                    placeholder={t("例如 project:v8-agent-os")}
                                    className={`${fieldClassName} font-mono`}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="project-tags">{t("Tags")}</Label>
                                <Input
                                    id="project-tags"
                                    value={(form.tags || []).join(", ")}
                                    onChange={(event) => setForm((prev) => ({
                                        ...prev,
                                        tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean),
                                    }))}
                                    placeholder={t("例如 memory, rag, realtime")}
                                    className={fieldClassName}
                                />
                            </div>
                        </div>

                        <DialogFooter className="border-t border-border/60 px-5 py-4">
                            {isEditing ? (
                                <Button
                                    variant="destructive"
                                    onClick={() => void handleDelete(form.id)}
                                    disabled={saving}
                                    className="mr-auto"
                                >
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    {t("删除")}
                                </Button>
                            ) : null}
                            <Button variant="outline" onClick={() => setDrawerOpen(false)} disabled={saving}>
                                {t("取消")}
                            </Button>
                            <Button onClick={() => void handleSave()} disabled={saving}>
                                <Save className="mr-2 h-4 w-4" />
                                {isEditing ? t("保存修改") : t("创建项目")}
                            </Button>
                        </DialogFooter>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
