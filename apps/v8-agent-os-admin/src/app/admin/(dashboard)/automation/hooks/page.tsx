"use client";
import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardFooter, } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, } from "@/components/ui/dialog";
import { RefreshCw, Code, Terminal, Zap, Plus, Pencil, Trash2, BookOpen, Workflow } from "lucide-react";
import { DocumentationGuideDialog } from "@/components/admin-shell/DocumentationGuideDialog";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache, primeAdminJsonCache } from "@/lib/admin-client-cache";
import { createTranslator } from "@/lib/locale";
interface HookConfig {
    name: string;
    type: "command" | "python" | "agent" | "rpa";
    target: string;
    events: string[];
    enabled: boolean;
    payload?: Record<string, unknown>;
    async?: boolean;
    triggerKind?: "nudge" | "wake" | "recovery_wake";
    targetBinding?: Record<string, unknown>;
    recoveryAnchor?: Record<string, unknown>;
    attachPolicy?: "new_session" | "attach_session" | "attach_run" | "resume_run";
    wakeReason?: string;
    message?: string;
    sourceMetadata?: Record<string, unknown>;
}
interface HooksConfigResponse {
    hooks: HookConfig[];
}
function getHookTypeLabel(type: HookConfig["type"]) {
    switch (type) {
        case "command":
            return "app.admin.dashboard.automation.hooks.page.kd64c8b12";
        case "python":
            return "app.admin.dashboard.automation.hooks.page.ke83fbd7a";
        case "agent":
            return "app.admin.dashboard.automation.hooks.page.k7c6312bb";
        case "rpa":
            return "app.admin.dashboard.automation.hooks.page.actionType.rpa";
        default:
            return type;
    }
}
function formatJsonField(value: Record<string, unknown> | undefined) {
    return value && Object.keys(value).length ? JSON.stringify(value, null, 2) : "{}";
}
function parseJsonField(label: string, value: string, locale: "zh-CN" | "en") {
    const raw = String(value || "").trim();
    if (!raw)
        return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(createTranslator(locale)("app.admin.dashboard.automation.cron.page.jsonObjectError", { label }));
    }
    return parsed as Record<string, unknown>;
}
const TRIGGER_KIND_OPTIONS = [
    { value: "nudge", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.nudge" },
    { value: "wake", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.wake" },
    { value: "recovery_wake", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.recoveryWake" },
] as const;
const ATTACH_POLICY_OPTIONS = [
    { value: "new_session", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.newSession" },
    { value: "attach_session", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.attachSession" },
    { value: "attach_run", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.attachRun" },
    { value: "resume_run", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.resumeRun" },
] as const;
function HooksPage() {
    const t = useT();
    const { locale } = useLocale();
    const [hooks, setHooks] = useState<HookConfig[]>(() => peekAdminJsonCache<HooksConfigResponse>("/api/hooks")?.hooks || []);
    const [isLoading, setIsLoading] = useState(() => !peekAdminJsonCache<HooksConfigResponse>("/api/hooks"));
    const [isToggling, setIsToggling] = useState<string | null>(null);
    // Dialog State
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isGuideOpen, setIsGuideOpen] = useState(false);
    const [editingHookName, setEditingHookName] = useState<string | null>(null);
    const [docContent, setDocContent] = useState("");
    const [formData, setFormData] = useState<HookConfig>({
        name: "",
        type: "command",
        target: "",
        events: [],
        enabled: true,
        triggerKind: "nudge",
        attachPolicy: "new_session",
    });
    const [eventsInput, setEventsInput] = useState("");
    const [payloadText, setPayloadText] = useState("{}");
    const [targetBindingText, setTargetBindingText] = useState("{}");
    const [recoveryAnchorText, setRecoveryAnchorText] = useState("{}");
    const [sourceMetadataText, setSourceMetadataText] = useState("{}");
    const loadDocumentation = async () => {
        try {
            const response = await fetch(locale.startsWith("en") ? "/HOOKS.en.md" : "/HOOKS.zh-CN.md");
            const fallback = await fetch("/HOOKS.zh-CN.md");
            const text = response.ok ? await response.text() : await fallback.text();
            setDocContent(text);
            setIsGuideOpen(true);
        }
        catch (error) {
            console.error("Failed to load documentation:", error);
        }
    };
    const fetchHooks = useCallback(async (force = false) => {
        if (!peekAdminJsonCache<HooksConfigResponse>("/api/hooks")) setIsLoading(true);
        try {
            const data = await fetchAdminJson<HooksConfigResponse>("/api/hooks", { force });
            setHooks(data.hooks || []);
        }
        catch (e) {
            console.error("Failed to fetch hooks:", e);
        }
        finally {
            setIsLoading(false);
        }
    }, []);
    useEffect(() => {
        fetchHooks();
    }, [fetchHooks]);
    const saveHooksConfig = async (newHooks: HookConfig[]) => {
        try {
            const res = await fetch("/api/hooks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                // send wrapped in hooks
                body: JSON.stringify({ hooks: newHooks }),
            });
            if (res.ok) {
                setHooks(newHooks);
                primeAdminJsonCache("/api/hooks", { hooks: newHooks });
                setIsDialogOpen(false);
            }
            else {
                console.error("Failed to save hooks");
            }
        }
        catch (e) {
            console.error(e);
        }
    };
    const handleToggle = async (name: string, currentEnabled: boolean) => {
        setIsToggling(name);
        try {
            const res = await fetch("/api/hooks/toggle", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, enabled: !currentEnabled }),
            });
            if (res.ok) {
                setHooks((prev) => {
                    const next = prev.map((h) => h.name === name ? { ...h, enabled: !currentEnabled } : h);
                    primeAdminJsonCache("/api/hooks", { hooks: next });
                    return next;
                });
            }
        }
        catch (e) {
            console.error(e);
        }
        finally {
            setIsToggling(null);
        }
    };
    const openAddDialog = () => {
        setEditingHookName(null);
        setFormData({
            name: "",
            type: "command",
            target: "",
            events: [],
            enabled: true,
            triggerKind: "nudge",
            attachPolicy: "new_session",
        });
        setEventsInput("");
        setPayloadText("{}");
        setTargetBindingText("{}");
        setRecoveryAnchorText("{}");
        setSourceMetadataText("{}");
        setIsDialogOpen(true);
    };
    const openEditDialog = (hook: HookConfig) => {
        setEditingHookName(hook.name);
        setFormData({ ...hook });
        setEventsInput(hook.events.join(", "));
        setPayloadText(formatJsonField(hook.payload));
        setTargetBindingText(formatJsonField(hook.targetBinding));
        setRecoveryAnchorText(formatJsonField(hook.recoveryAnchor));
        setSourceMetadataText(formatJsonField(hook.sourceMetadata));
        setIsDialogOpen(true);
    };
    const handleDelete = async (hookName: string) => {
        if (confirm(t("app.admin.dashboard.automation.hooks.page.k0f56c770", {
            hookName: hookName
        }))) {
            const newHooks = hooks.filter((h) => h.name !== hookName);
            await saveHooksConfig(newHooks);
        }
    };
    const handleSaveHook = async () => {
        let payload: Record<string, unknown> | undefined;
        let targetBinding: Record<string, unknown> | undefined;
        let recoveryAnchor: Record<string, unknown> | undefined;
        let sourceMetadata: Record<string, unknown> | undefined;
        try {
            payload = parseJsonField("payload", payloadText, locale);
            targetBinding = parseJsonField("targetBinding", targetBindingText, locale);
            recoveryAnchor = parseJsonField("recoveryAnchor", recoveryAnchorText, locale);
            sourceMetadata = parseJsonField("sourceMetadata", sourceMetadataText, locale);
        }
        catch (error) {
            alert(error instanceof Error ? error.message : t("app.admin.dashboard.automation.hooks.page.kb6a05c22"));
            return;
        }
        const updatedEvents = eventsInput
            .split(",")
            .map((e) => e.trim())
            .filter((e) => e.length > 0);
        const updatedHook: HookConfig = {
            ...formData,
            events: updatedEvents,
            triggerKind: formData.triggerKind || "nudge",
            attachPolicy: formData.attachPolicy || "new_session",
            payload,
            targetBinding,
            recoveryAnchor,
            sourceMetadata,
        };
        let newHooks: HookConfig[];
        if (editingHookName) {
            // Editing Mode
            newHooks = hooks.map((h) => h.name === editingHookName ? updatedHook : h);
        }
        else {
            // Adding Mode (Check for duplicate)
            if (hooks.find((h) => h.name === updatedHook.name)) {
                alert(t("app.admin.dashboard.automation.hooks.page.k6a8da340"));
                return;
            }
            newHooks = [...hooks, updatedHook];
        }
        await saveHooksConfig(newHooks);
    };
    const getIconForType = (type: string) => {
        switch (type) {
            case "command":
                return <Terminal className="h-4 w-4"/>;
            case "python":
                return <Code className="h-4 w-4"/>;
            case "agent":
                return <Zap className="h-4 w-4"/>;
            case "rpa":
                return <Workflow className="h-4 w-4"/>;
            default:
                return <Code className="h-4 w-4"/>;
        }
    };
    return (<div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t("app.admin.dashboard.automation.hooks.page.k9e0060cc")}</h1>
        </div>
        <div className="flex gap-2">
          <Button onClick={loadDocumentation} variant="secondary">
            <BookOpen className="mr-2 h-4 w-4"/>
            {t("app.admin.dashboard.automation.hooks.page.k2a0649a2")}
          </Button>
          <Button onClick={() => void fetchHooks(true)} variant="outline" disabled={isLoading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}/>
            {t("app.admin.dashboard.automation.hooks.page.k876e8c06")}
          </Button>
          <Button onClick={openAddDialog}>
            <Plus className="mr-2 h-4 w-4"/>
            {t("app.admin.dashboard.automation.hooks.page.ke444d89a")}
          </Button>
        </div>
      </div>

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>
              {editingHookName ? t("app.admin.dashboard.automation.hooks.page.k56c13657") : t("app.admin.dashboard.automation.hooks.page.ke444d89a")}
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">{t("app.admin.dashboard.automation.hooks.page.kc059aa92")}</Label>
              <Input id="name" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder={t("app.admin.dashboard.automation.hooks.page.kc80b2825")}/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type">{t("app.admin.dashboard.automation.hooks.page.k5e60f7aa")}</Label>
              <select id="type" className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50" value={formData.type} onChange={(e) => setFormData({
            ...formData,
            type: e.target.value as HookConfig["type"],
        })}>
                <option value="command">{t("app.admin.dashboard.automation.hooks.page.kd64c8b12")}</option>
                <option value="python">{t("app.admin.dashboard.automation.hooks.page.ke83fbd7a")}</option>
                <option value="agent">{t("app.admin.dashboard.automation.hooks.page.k7c6312bb")}</option>
                <option value="rpa">{t("app.admin.dashboard.automation.hooks.page.actionType.rpa")}</option>
              </select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="target">{t("app.admin.dashboard.automation.hooks.page.k8e6bca67")}</Label>
              <Input id="target" value={formData.target} onChange={(e) => setFormData({ ...formData, target: e.target.value })} placeholder={t("app.admin.dashboard.automation.hooks.page.k1c10a924")}/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="events">{t("app.admin.dashboard.automation.hooks.page.k61a20e0b")}</Label>
              <Input id="events" value={eventsInput} onChange={(e) => setEventsInput(e.target.value)} placeholder={t("app.admin.dashboard.automation.hooks.page.k1e27a1ac")}/>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="triggerKind">{t("app.admin.dashboard.automation.hooks.page.k01313cef")}</Label>
                <select id="triggerKind" className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm" value={formData.triggerKind || "nudge"} onChange={(e) => setFormData({ ...formData, triggerKind: e.target.value as HookConfig["triggerKind"] })}>
                  {TRIGGER_KIND_OPTIONS.map((option) => <option key={option.value} value={option.value}>{t(option.label)}</option>)}
                </select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="attachPolicy">{t("app.admin.dashboard.automation.hooks.page.ka1c2596c")}</Label>
                <select id="attachPolicy" className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm" value={formData.attachPolicy || "new_session"} onChange={(e) => setFormData({ ...formData, attachPolicy: e.target.value as HookConfig["attachPolicy"] })}>
                  {ATTACH_POLICY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{t(option.label)}</option>)}
                </select>
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="wakeReason">{t("app.admin.dashboard.automation.hooks.page.k4a33217d")}</Label>
              <Input id="wakeReason" value={formData.wakeReason || ""} onChange={(e) => setFormData({ ...formData, wakeReason: e.target.value })} placeholder={t("app.admin.dashboard.automation.hooks.page.ka6afa838")}/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="message">{t("app.admin.dashboard.automation.hooks.page.kebf5a38b")}</Label>
              <Textarea id="message" rows={3} value={formData.message || ""} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setFormData({ ...formData, message: e.target.value })} placeholder={t("app.admin.dashboard.automation.hooks.page.ka3226001")}/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="payload">{t("app.admin.dashboard.automation.hooks.page.payload")}</Label>
              <Textarea id="payload" rows={4} value={payloadText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setPayloadText(e.target.value)} className="font-mono text-xs" placeholder={t("app.admin.dashboard.automation.hooks.page.payloadPlaceholder")}/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="targetBinding">{t("app.admin.dashboard.automation.hooks.page.kb5a63537")}</Label>
              <Textarea id="targetBinding" rows={4} value={targetBindingText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setTargetBindingText(e.target.value)} className="font-mono text-xs"/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="recoveryAnchor">{t("app.admin.dashboard.automation.hooks.page.k6b5e0d20")}</Label>
              <Textarea id="recoveryAnchor" rows={4} value={recoveryAnchorText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setRecoveryAnchorText(e.target.value)} className="font-mono text-xs"/>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="sourceMetadata">{t("app.admin.dashboard.automation.hooks.page.k01d4c959")}</Label>
              <Textarea id="sourceMetadata" rows={4} value={sourceMetadataText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setSourceMetadataText(e.target.value)} className="font-mono text-xs"/>
            </div>
            <SettingToggleCard
              id="enabled"
              title={t("app.admin.dashboard.automation.hooks.page.ke682839a")}
              checked={formData.enabled}
              onCheckedChange={(c) => setFormData({ ...formData, enabled: c })}
              className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-2 items-center"
              titleClassName="text-sm font-normal cursor-pointer"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
              {t("app.admin.dashboard.automation.hooks.page.kb92cb20c")}
            </Button>
            <Button onClick={handleSaveHook} disabled={!formData.name || !formData.target}>
              {t("app.admin.dashboard.automation.hooks.page.k6010e1ed")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DocumentationGuideDialog open={isGuideOpen} onOpenChange={setIsGuideOpen} title={t("app.admin.dashboard.automation.hooks.page.k40269d76")} content={docContent}/>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {hooks.map((hook) => (<Card key={hook.name}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                {getIconForType(hook.type)}
                {hook.name}
              </CardTitle>
              <Switch checked={hook.enabled} onCheckedChange={() => handleToggle(hook.name, hook.enabled)} disabled={isToggling === hook.name}/>
            </CardHeader>
            <CardContent>
              <div className="text-sm space-y-2 mt-2">
                <p>
                  <strong>{t("app.admin.dashboard.automation.hooks.page.ke27961f8")}</strong>{" "}
                  <span>{t(getHookTypeLabel(hook.type))}</span>
                </p>
                <p>
                  <strong>{t("app.admin.dashboard.automation.hooks.page.k4f3ff135")}</strong>{" "}
                  <span>{hook.triggerKind || "nudge"}</span>
                </p>
                <p>
                  <strong>{t("app.admin.dashboard.automation.hooks.page.k6dbc1e17")}</strong>{" "}
                  <code className="bg-muted px-1 rounded truncate block mt-1">
                    {hook.target}
                  </code>
                </p>
                <p className="text-xs text-muted-foreground">
                  {hook.targetBinding || hook.recoveryAnchor
                ? t("app.admin.dashboard.automation.hooks.page.k8b8fc65a")
                : t("app.admin.dashboard.automation.hooks.page.k4a2c0d63")}
                </p>
                <div>
                  <strong>{t("app.admin.dashboard.automation.hooks.page.k37737399")}</strong>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {hook.events.map((event) => (<span key={event} className="bg-primary/10 text-primary text-xs px-2 py-0.5 rounded">
                        {event}
                      </span>))}
                  </div>
                </div>
              </div>
            </CardContent>
            <CardFooter className="flex justify-end gap-2 border-t pt-4 mt-auto">
              <Button variant="outline" size="sm" onClick={() => openEditDialog(hook)}>
                <Pencil className="mr-2 h-4 w-4"/> {t("app.admin.dashboard.automation.hooks.page.k75997619")}
              </Button>
              <Button variant="destructive" size="sm" onClick={() => handleDelete(hook.name)}>
                <Trash2 className="mr-2 h-4 w-4"/> {t("app.admin.dashboard.automation.hooks.page.k626f35dc")}
              </Button>
            </CardFooter>
          </Card>))}
        {hooks.length === 0 && (<div className="col-span-full text-center py-10 text-muted-foreground border rounded-lg border-dashed">
            {t("app.admin.dashboard.automation.hooks.page.ke2bdb15c")}
            <br />
            <Button onClick={openAddDialog} variant="outline" className="mt-4">
              <Plus className="mr-2 h-4 w-4"/>
              {t("app.admin.dashboard.automation.hooks.page.kbdce5025")}
            </Button>
          </div>)}
      </div>
    </div>);
}

export default HooksPage;
