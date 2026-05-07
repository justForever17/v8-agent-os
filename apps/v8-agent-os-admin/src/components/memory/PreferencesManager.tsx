"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Save, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { tg } from "@/i18n/admin-legacy";
interface PreferenceRow {
  id: string;
  key: string;
  value: string;
  originalKey: string;
  isNew?: boolean;
  saving?: boolean;
}
interface QuarantinedPreferenceRecord {
  id: string;
  key: string;
  value: string;
  reason?: string;
  metadata?: Record<string, unknown>;
  quarantinedAt?: string;
}
type PreferencesByScope = Record<string, PreferenceRow[]>;
function isAllowedPreferenceScope(scope: string) {
  return scope === "global" || scope.startsWith("project:") || scope.startsWith("channel:");
}
function scopeLabel(scope: string, t: ReturnType<typeof useT>) {
  if (scope === "global")
  return t("components.memory.PreferencesManager.kb4d725ff");
  if (scope.startsWith("project:"))
  return t("components.memory.PreferencesManager.scope.project", {
    scope_name: scope.split(":")[1]
  });
  if (scope.startsWith("channel:"))
  return t("components.memory.PreferencesManager.scope.channel", {
    scope_name: scope.split(":").slice(1).join(":")
  });
  return scope;
}
function normalizePreferences(raw: Record<string, Record<string, string>>) {
  const next: PreferencesByScope = {};
  for (const [scope, entries] of Object.entries(raw || {})) {
    next[scope] = Object.entries(entries || {}).map(([key, value]) => ({
      id: `${scope}:${key}`,
      key,
      value,
      originalKey: key
    }));
  }
  return next;
}
function sortScopes(scopes: string[]) {
  return [...scopes].sort((left, right) => {
    if (left === "global")
    return -1;
    if (right === "global")
    return 1;
    return left.localeCompare(right);
  });
}
export function PreferencesManager() {
  const { toast } = useToast();
  const t = useT();
  const [preferencesByScope, setPreferencesByScope] = useState<PreferencesByScope>({});
  const [quarantinedGlobalPreferences, setQuarantinedGlobalPreferences] = useState<QuarantinedPreferenceRecord[]>([]);
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
      setQuarantinedGlobalPreferences(Array.isArray(data?.globalQuarantine) ? data.globalQuarantine : []);
    }
    catch (error) {
      console.error("Failed to load preferences:", error);
      toast({
        title: t("components.memory.PreferencesManager.k0a8fdabc"),
        description: t("components.memory.PreferencesManager.k5497dd43"),
        variant: "destructive"
      });
    } finally
    {
      setLoading(false);
    }
  }, [t, toast]);
  useEffect(() => {
    void loadPreferences();
  }, [loadPreferences]);
  const handleRestoreQuarantined = useCallback(async (record: QuarantinedPreferenceRecord) => {
    try {
      const res = await fetch("/api/memory/preferences/quarantine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recordId: record.id })
      });
      if (!res.ok) {
        throw new Error(`Restore failed: ${res.status}`);
      }
      await loadPreferences();
      toast({
        title: tg(t, "a9677acb")),
        description: tg(t, "4191e7e2"), { value1: record.key })
      });
    } catch (error) {
      console.error("Failed to restore quarantined preference:", error);
      toast({
        title: tg(t, "76842a03")),
        description: tg(t, "b01dce71")),
        variant: "destructive"
      });
    }
  }, [loadPreferences, toast]);
  const handleDeleteQuarantined = useCallback(async (record: QuarantinedPreferenceRecord) => {
    if (!window.confirm(tg(t, "0011ed75"), { value1: record.key }))) {
      return;
    }
    try {
      const res = await fetch("/api/memory/preferences/quarantine", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recordId: record.id })
      });
      if (!res.ok) {
        throw new Error(`Delete failed: ${res.status}`);
      }
      await loadPreferences();
      toast({
        title: tg(t, "6a51586b")),
        description: tg(t, "23a1462d"), { value1: record.key })
      });
    } catch (error) {
      console.error("Failed to delete quarantined preference:", error);
      toast({
        title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0915ccdf"),
        description: tg(t, "982f281a")),
        variant: "destructive"
      });
    }
  }, [loadPreferences, toast]);
  const mutateRow = useCallback((scope: string, rowId: string, patch: Partial<PreferenceRow>) => {
    setPreferencesByScope((prev) => ({
      ...prev,
      [scope]: (prev[scope] || []).map((row) => row.id === rowId ? { ...row, ...patch } : row)
    }));
  }, []);
  const addPreferenceRow = useCallback((scope: string) => {
    const nextId = `${scope}:new:${Date.now()}`;
    setPreferencesByScope((prev) => ({
      ...prev,
      [scope]: [
      ...(prev[scope] || []),
      { id: nextId, key: "", value: "", originalKey: "", isNew: true }]

    }));
  }, []);
  const createScopeCard = useCallback(() => {
    const scope = newScopeName.trim();
    if (!scope) {
      return;
    }
    if (!isAllowedPreferenceScope(scope)) {
      toast({
        title: t("components.memory.PreferencesManager.k51274e26"),
        description: t("components.memory.PreferencesManager.k75d79433"),
        variant: "destructive"
      });
      return;
    }
    setPreferencesByScope((prev) => {
      if (prev[scope]) {
        return prev;
      }
      return { ...prev, [scope]: [] };
    });
    setNewScopeName("");
  }, [newScopeName, t, toast]);
  const saveRow = useCallback(async (scope: string, row: PreferenceRow) => {
    const key = row.key.trim();
    const value = row.value.trim();
    if (!key || !value) {
      toast({
        title: t("components.memory.PreferencesManager.k875460ab"),
        description: t("components.memory.PreferencesManager.k51e85cfa"),
        variant: "destructive"
      });
      return;
    }
    mutateRow(scope, row.id, { saving: true });
    try {
      if (!row.isNew && row.originalKey && row.originalKey !== key) {
        await fetch("/api/memory/preferences", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope, key: row.originalKey })
        });
      }
      const res = await fetch("/api/memory/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, key, value })
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
        id: `${scope}:${key}`
      });
      toast({
        title: t("components.memory.PreferencesManager.k9fb6b6d9"),
        description: t("components.memory.PreferencesManager.k15eed48c", {
          scope: scope,
          key: key
        })
      });
    }
    catch (error) {
      console.error("Failed to save preference:", error);
      mutateRow(scope, row.id, { saving: false });
      toast({
        title: t("components.memory.PreferencesManager.k12769ce1"),
        description: t("components.memory.PreferencesManager.k98a2d3fe"),
        variant: "destructive"
      });
    }
  }, [mutateRow, t, toast]);
  const deleteRow = useCallback(async (scope: string, row: PreferenceRow) => {
    if (row.isNew && !row.originalKey) {
      setPreferencesByScope((prev) => ({
        ...prev,
        [scope]: (prev[scope] || []).filter((item) => item.id !== row.id)
      }));
      return;
    }
    if (!window.confirm(t("components.memory.PreferencesManager.k2e757f28", {
      scope: scope,
      row_key_row_originalKey: row.key || row.originalKey
    }))) {
      return;
    }
    try {
      const res = await fetch("/api/memory/preferences", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, key: row.originalKey || row.key })
      });
      if (!res.ok) {
        throw new Error(`Delete failed: ${res.status}`);
      }
      setPreferencesByScope((prev) => ({
        ...prev,
        [scope]: (prev[scope] || []).filter((item) => item.id !== row.id)
      }));
      toast({
        title: t("components.memory.PreferencesManager.k121bffe2"),
        description: t("components.memory.PreferencesManager.kd0cb03e4", {
          scope: scope,
          row_key_row_originalKey: row.key || row.originalKey
        })
      });
    }
    catch (error) {
      console.error("Failed to delete preference:", error);
      toast({
        title: t("components.memory.PreferencesManager.k0915ccdf"),
        description: t("components.memory.PreferencesManager.kdb5f3c64"),
        variant: "destructive"
      });
    }
  }, [t, toast]);
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border/60 bg-card/40 px-5 py-4">
                <div>
                    <h2 className="text-lg font-semibold">{t("components.memory.PreferencesManager.k79d67bc6")}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {t("components.memory.PreferencesManager.k5bf71d02")}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Input value={newScopeName} onChange={(event) => setNewScopeName(event.target.value)} placeholder={t("components.memory.PreferencesManager.k2c8cf5b7")} className="w-64" />
                    <Button variant="outline" onClick={createScopeCard}>
                        <Plus className="mr-2 h-4 w-4" />
                        {t("components.memory.PreferencesManager.k3b610bbd")}
                    </Button>
                </div>
            </div>

            {quarantinedGlobalPreferences.length > 0 ?
    <Card className="border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/10">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "b9aeaf18"))}</CardTitle>
                        <CardDescription>
                            {tg(t, "5efc831b"))}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {quarantinedGlobalPreferences.map((record) =>
        <div key={record.id} className="grid gap-3 rounded-2xl border border-amber-500/20 bg-background/80 px-4 py-4 xl:grid-cols-[200px_minmax(0,1fr)_auto]">
                                <div className="min-w-0">
                                    <div className="font-mono text-sm font-medium">{record.key}</div>
                                    <div className="mt-1 text-xs text-muted-foreground">{record.quarantinedAt || t("components.memory.ArtifactExplorerPanel.k2be56351")}</div>
                                </div>
                                <div className="min-w-0 space-y-1">
                                    <div className="text-sm">{record.value}</div>
                                    <div className="text-xs text-muted-foreground">
                                        {tg(t, "0f93c2bb"))}{record.reason || "unspecified"}
                                    </div>
                                </div>
                                <div className="flex items-center justify-end gap-2">
                                    <Button variant="outline" size="sm" onClick={() => void handleRestoreQuarantined(record)}>
                                        <Save className="mr-2 h-4 w-4" />
                                        {tg(t, "79748ca1"))}
                                    </Button>
                                    <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-destructive" onClick={() => void handleDeleteQuarantined(record)}>
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
        )}
                    </CardContent>
                </Card> :
    null}

            {loading ? <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                    {t("components.memory.PreferencesManager.k1f44c77b")}
                </div> : scopes.length === 0 ? <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
                    {t("components.memory.PreferencesManager.k96833d51")}
                </div> : scopes.map((scope) => <Card key={scope} className="border-border/60">
                        <CardHeader className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <CardTitle className="text-lg">
                                        <span className="rounded-full bg-primary/10 px-3 py-1 font-mono text-sm text-primary">
                                            [{scope}]
                                        </span>
                                    </CardTitle>
                                    <CardDescription className="mt-3">{scopeLabel(scope, t)}</CardDescription>
                                </div>
                                <Button variant="outline" size="sm" onClick={() => addPreferenceRow(scope)}>
                                    <Plus className="mr-2 h-4 w-4" />
                                    {t("components.memory.PreferencesManager.ke7a412a5")}
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {(preferencesByScope[scope] || []).length === 0 ? <div className="rounded-xl border border-dashed px-4 py-5 text-sm text-muted-foreground">
                                    {t("components.memory.PreferencesManager.kfbe7f14a")}
                                </div> : (preferencesByScope[scope] || []).map((row) => <div key={row.id} className="grid gap-3 rounded-2xl border border-border/50 bg-muted/15 px-4 py-4 xl:grid-cols-[220px_minmax(0,1fr)_auto]">
                                        <Input value={row.key} onChange={(event) => mutateRow(scope, row.id, { key: event.target.value })} placeholder="preference_key" className="font-mono" />
                                        <Input value={row.value} onChange={(event) => mutateRow(scope, row.id, { value: event.target.value })} placeholder={t("components.memory.PreferencesManager.k226ef005")} />
                                        <div className="flex items-center justify-end gap-2">
                                            <Button variant="outline" size="sm" onClick={() => void saveRow(scope, row)} disabled={row.saving}>
                                                <Save className="mr-2 h-4 w-4" />
                                                {t("components.memory.PreferencesManager.k6010e1ed")}
                                            </Button>
                                            <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-destructive" onClick={() => void deleteRow(scope, row)}>
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>)}
                        </CardContent>
                    </Card>)}
        </div>;
}
