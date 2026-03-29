"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Save, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface PreferenceRow {
    id: string;
    key: string;
    value: string;
    originalKey: string;
    isNew?: boolean;
    saving?: boolean;
}

type PreferencesByScope = Record<string, PreferenceRow[]>;

function scopeLabel(scope: string) {
    if (scope === "global") return lt("全局偏好", "Global preferences");
    if (scope.startsWith("app:")) return lt(`${scope.split(":")[1]} 场景偏好`, `${scope.split(":")[1]} app preferences`);
    if (scope.startsWith("project:")) return lt(`项目 ${scope.split(":")[1]} 偏好`, `Project ${scope.split(":")[1]} preferences`);
    if (scope.startsWith("workspace:")) return lt(`工作区 ${scope.split(":")[1]} 偏好`, `Workspace ${scope.split(":")[1]} preferences`);
    return scope;
}

function normalizePreferences(raw: Record<string, Record<string, string>>) {
    const next: PreferencesByScope = {};
    for (const [scope, entries] of Object.entries(raw || {})) {
        next[scope] = Object.entries(entries || {}).map(([key, value]) => ({
            id: `${scope}:${key}`,
            key,
            value,
            originalKey: key,
        }));
    }
    return next;
}

function sortScopes(scopes: string[]) {
    return [...scopes].sort((left, right) => {
        if (left === "global") return -1;
        if (right === "global") return 1;
        return left.localeCompare(right);
    });
}

export function PreferencesManager() {
    const { toast } = useToast();
    const t = useT();
    const [preferencesByScope, setPreferencesByScope] = useState<PreferencesByScope>({});
    const [loading, setLoading] = useState(true);
    const [newScopeName, setNewScopeName] = useState("");

    const scopes = useMemo(() => sortScopes(Object.keys(preferencesByScope)), [preferencesByScope]);

    const loadPreferences = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/memory/preferences", { cache: "no-store" });
            if (!res.ok) {
                throw new Error(`Load failed: ${res.status}`);
            }
            const data = await res.json();
            setPreferencesByScope(normalizePreferences(data?.preferences || {}));
        } catch (error) {
            console.error("Failed to load preferences:", error);
            toast({
                title: t("偏好加载失败"),
                description: t("未能读取 MEMORY.md 中的偏好数据。"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [t, toast]);

    useEffect(() => {
        void loadPreferences();
    }, [loadPreferences]);

    const mutateRow = useCallback((scope: string, rowId: string, patch: Partial<PreferenceRow>) => {
        setPreferencesByScope((prev) => ({
            ...prev,
            [scope]: (prev[scope] || []).map((row) => row.id === rowId ? { ...row, ...patch } : row),
        }));
    }, []);

    const addPreferenceRow = useCallback((scope: string) => {
        const nextId = `${scope}:new:${Date.now()}`;
        setPreferencesByScope((prev) => ({
            ...prev,
            [scope]: [
                ...(prev[scope] || []),
                { id: nextId, key: "", value: "", originalKey: "", isNew: true },
            ],
        }));
    }, []);

    const createScopeCard = useCallback(() => {
        const scope = newScopeName.trim();
        if (!scope) {
            return;
        }
        setPreferencesByScope((prev) => {
            if (prev[scope]) {
                return prev;
            }
            return { ...prev, [scope]: [] };
        });
        setNewScopeName("");
    }, [newScopeName]);

    const saveRow = useCallback(async (scope: string, row: PreferenceRow) => {
        const key = row.key.trim();
        const value = row.value.trim();
        if (!key || !value) {
            toast({
                title: t("无法保存"),
                description: t("偏好的 key 和 value 都不能为空。"),
                variant: "destructive",
            });
            return;
        }

        mutateRow(scope, row.id, { saving: true });
        try {
            if (!row.isNew && row.originalKey && row.originalKey !== key) {
                await fetch("/api/memory/preferences", {
                    method: "DELETE",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ scope, key: row.originalKey }),
                });
            }

            const res = await fetch("/api/memory/preferences", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ scope, key, value }),
            });

            if (!res.ok) {
                throw new Error(`Save failed: ${res.status}`);
            }

            mutateRow(scope, row.id, {
                key,
                value,
                originalKey: key,
                isNew: false,
                saving: false,
                id: `${scope}:${key}`,
            });
            toast({
                title: t("偏好已保存"),
                description: t(lt(`[${scope}] ${key} 已更新。`, `[${scope}] ${key} updated.`)),
            });
        } catch (error) {
            console.error("Failed to save preference:", error);
            mutateRow(scope, row.id, { saving: false });
            toast({
                title: t("保存失败"),
                description: t("偏好写入失败，请稍后重试。"),
                variant: "destructive",
            });
        }
    }, [mutateRow, t, toast]);

    const deleteRow = useCallback(async (scope: string, row: PreferenceRow) => {
        if (row.isNew && !row.originalKey) {
            setPreferencesByScope((prev) => ({
                ...prev,
                [scope]: (prev[scope] || []).filter((item) => item.id !== row.id),
            }));
            return;
        }

        if (!window.confirm(t(lt(`确定删除 [${scope}] ${row.key || row.originalKey} 吗？`, `Delete [${scope}] ${row.key || row.originalKey}?`)))) {
            return;
        }

        try {
            const res = await fetch("/api/memory/preferences", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ scope, key: row.originalKey || row.key }),
            });
            if (!res.ok) {
                throw new Error(`Delete failed: ${res.status}`);
            }

            setPreferencesByScope((prev) => ({
                ...prev,
                [scope]: (prev[scope] || []).filter((item) => item.id !== row.id),
            }));
            toast({
                title: t("偏好已删除"),
                description: t(lt(`[${scope}] ${row.key || row.originalKey} 已移除。`, `[${scope}] ${row.key || row.originalKey} removed.`)),
            });
        } catch (error) {
            console.error("Failed to delete preference:", error);
            toast({
                title: t("删除失败"),
                description: t("偏好删除失败，请稍后重试。"),
                variant: "destructive",
            });
        }
    }, [t, toast]);

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border/60 bg-card/40 px-5 py-4">
                <div>
                    <h2 className="text-lg font-semibold">{t("偏好管理")}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {t("当前偏好直接回写到 `MEMORY.md`，现在这里可以查看、编辑、增加和删除单条偏好，不再只是只读面板。")}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Input
                        value={newScopeName}
                        onChange={(event) => setNewScopeName(event.target.value)}
                        placeholder={t("新 scope，例如 project:v8-agent-os")}
                        className="w-64"
                    />
                    <Button variant="outline" onClick={createScopeCard}>
                        <Plus className="mr-2 h-4 w-4" />
                        {t("新增 scope")}
                    </Button>
                </div>
            </div>

            {loading ? (
                <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                    {t("偏好加载中...")}
                </div>
            ) : scopes.length === 0 ? (
                <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                    {t("还没有可编辑的偏好项，可以先新增一个 scope，再添加条目。")}
                </div>
            ) : (
                scopes.map((scope) => (
                    <Card key={scope} className="border-border/60">
                        <CardHeader className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <CardTitle className="text-lg">
                                        <span className="rounded-full bg-primary/10 px-3 py-1 font-mono text-sm text-primary">
                                            [{scope}]
                                        </span>
                                    </CardTitle>
                                    <CardDescription className="mt-3">{t(scopeLabel(scope))}</CardDescription>
                                </div>
                                <Button variant="outline" size="sm" onClick={() => addPreferenceRow(scope)}>
                                    <Plus className="mr-2 h-4 w-4" />
                                    {t("添加偏好")}
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {(preferencesByScope[scope] || []).length === 0 ? (
                                <div className="rounded-xl border border-dashed px-4 py-5 text-sm text-muted-foreground">
                                    {t("这个 scope 还没有偏好，点击上方按钮添加一条即可。")}
                                </div>
                            ) : (
                                (preferencesByScope[scope] || []).map((row) => (
                                    <div
                                        key={row.id}
                                        className="grid gap-3 rounded-2xl border border-border/50 bg-muted/15 px-4 py-4 xl:grid-cols-[220px_minmax(0,1fr)_auto]"
                                    >
                                        <Input
                                            value={row.key}
                                            onChange={(event) => mutateRow(scope, row.id, { key: event.target.value })}
                                            placeholder="preference_key"
                                            className="font-mono"
                                        />
                                        <Input
                                            value={row.value}
                                            onChange={(event) => mutateRow(scope, row.id, { value: event.target.value })}
                                            placeholder={t("偏好内容")}
                                        />
                                        <div className="flex items-center justify-end gap-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => void saveRow(scope, row)}
                                                disabled={row.saving}
                                            >
                                                <Save className="mr-2 h-4 w-4" />
                                                {t("保存")}
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="text-muted-foreground hover:text-destructive"
                                                onClick={() => void deleteRow(scope, row)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                ))
                            )}
                        </CardContent>
                    </Card>
                ))
            )}
        </div>
    );
}
