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

type ProjectFormState = {
    name: string;
    workspacePath: string;
};

const EMPTY_FORM: ProjectFormState = {
    name: "",
    workspacePath: "",
};

function sortProjects(projects: ProjectDescriptor[]) {
    return [...projects].sort((left, right) => {
        const leftKey = `${left.name || ""}:${left.id || ""}`.toLowerCase();
        const rightKey = `${right.name || ""}:${right.id || ""}`.toLowerCase();
        if (leftKey === rightKey) return 0;
        return leftKey.localeCompare(rightKey);
    });
}

export function ProjectRegistryPanel() {
    const { toast } = useToast();
    const t = useT();
    const [projects, setProjects] = useState<ProjectDescriptor[]>([]);
    const [defaultProjectId, setDefaultProjectId] = useState<string | null>(null);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [form, setForm] = useState<ProjectFormState>(EMPTY_FORM);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const isEditing = useMemo(() => Boolean(selectedId), [selectedId]);
    const selectedProject = useMemo(
        () => projects.find((project) => project.id === selectedId) || null,
        [projects, selectedId],
    );
    const fieldClassName = "border-border/70 bg-card text-foreground placeholder:text-muted-foreground/70";

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
            name: project.name || "",
            workspacePath: project.workspacePath || "",
        });
        setDrawerOpen(true);
    }, []);

    const handleSave = useCallback(async () => {
        if (!form.name.trim() || !form.workspacePath.trim()) {
            toast({
                title: t("信息不完整"),
                description: t("项目名称和工作区路径不能为空。"),
                variant: "destructive",
            });
            return;
        }

        setSaving(true);
        try {
            const payload = {
                name: form.name.trim(),
                workspacePath: form.workspacePath.trim(),
            };

            const res = await fetch(isEditing ? `/api/projects/${selectedId}` : "/api/projects", {
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
    }, [form, isEditing, loadProjects, resetForm, selectedId, t, toast]);

    const handleDelete = useCallback(async (projectId: string) => {
        if (!projectId) {
            return;
        }
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
                        {t("这里管理项目级工作区记录。主表单只维护项目名称和工作区路径，内部标识会自动派生。")}
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
                        {t("还没有项目。先创建一个项目，方便项目级工作区和记忆绑定保持稳定。")}
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
                                        {project.workspacePath || t("尚未配置项目级工作区路径。")}
                                    </p>
                                </div>
                            </div>

                            <div className="mt-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                <span className="rounded-full border px-2 py-1">
                                    {t(lt(`工作区标识 ${project.workspaceId || project.id}`, `Workspace ID ${project.workspaceId || project.id}`))}
                                </span>
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
                                {t("这里只维护项目名称和工作区路径。项目 ID、工作区标识和默认范围由系统稳定派生。")}
                            </DialogDescription>
                        </DialogHeader>

                        <div className="flex-1 space-y-5 px-5 py-5">
                            {isEditing && selectedProject ? (
                                <div className="grid gap-3 sm:grid-cols-3">
                                    <div className="rounded-2xl border border-border/60 bg-muted/15 p-4">
                                        <p className="text-xs uppercase tracking-[0.22em] text-foreground/55">{t("项目 ID")}</p>
                                        <p className="mt-2 break-all font-mono text-sm text-foreground/85">{selectedProject.id}</p>
                                    </div>
                                    <div className="rounded-2xl border border-border/60 bg-muted/15 p-4">
                                        <p className="text-xs uppercase tracking-[0.22em] text-foreground/55">{t("工作区标识")}</p>
                                        <p className="mt-2 break-all font-mono text-sm text-foreground/85">{selectedProject.workspaceId || selectedProject.id}</p>
                                    </div>
                                    <div className="rounded-2xl border border-border/60 bg-muted/15 p-4">
                                        <p className="text-xs uppercase tracking-[0.22em] text-foreground/55">{t("默认范围")}</p>
                                        <p className="mt-2 break-all font-mono text-sm text-foreground/85">{selectedProject.defaultScope || `project:${selectedProject.id}`}</p>
                                    </div>
                                </div>
                            ) : null}

                            <div className="rounded-2xl border border-border/60 bg-muted/15 p-4 text-sm leading-6 text-foreground/75">
                                {t("项目级记忆和工作区绑定只依赖这两项输入。其余内部字段会在保存时自动生成或保持稳定。")}
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
                                <Label htmlFor="project-workspace-path">{t("工作区目录")}</Label>
                                <Input
                                    id="project-workspace-path"
                                    value={form.workspacePath}
                                    onChange={(event) => setForm((prev) => ({ ...prev, workspacePath: event.target.value }))}
                                    placeholder={t("例如 E:\\Projects\\V8-Agent-OS")}
                                    className={fieldClassName}
                                />
                            </div>
                        </div>

                        <DialogFooter className="border-t border-border/60 px-5 py-4">
                            {isEditing ? (
                                <Button
                                    variant="destructive"
                                    onClick={() => void handleDelete(selectedId || "")}
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
