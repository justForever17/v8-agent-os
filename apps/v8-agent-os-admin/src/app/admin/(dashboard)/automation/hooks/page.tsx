"use client";

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardFooter,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  RefreshCw,
  Code,
  Terminal,
  Zap,
  Plus,
  Pencil,
  Trash2,
  BookOpen
} from "lucide-react";
import { DocumentationGuideDialog } from "@/components/admin-shell/DocumentationGuideDialog";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface HookConfig {
  name: string;
  type: "command" | "python" | "agent";
  target: string;
  events: string[];
  enabled: boolean;
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
      return lt("命令行脚本", "Command");
    case "python":
      return lt("Python 脚本", "Python");
    case "agent":
      return lt("AutomationRuntime 任务", "Automation task");
    default:
      return type;
  }
}

function formatJsonField(value: Record<string, unknown> | undefined) {
  return value && Object.keys(value).length ? JSON.stringify(value, null, 2) : "{}";
}

function parseJsonField(label: string, value: string) {
  const raw = String(value || "").trim();
  if (!raw) return {};
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} 需要是合法 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

export default function HooksPage() {
  const t = useT();
  const { locale } = useLocale();
  const [hooks, setHooks] = useState<HookConfig[]>([]);
  const [isLoading, setIsLoading] = useState(false);
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
  const [targetBindingText, setTargetBindingText] = useState("{}");
  const [recoveryAnchorText, setRecoveryAnchorText] = useState("{}");
  const [sourceMetadataText, setSourceMetadataText] = useState("{}");

  const loadDocumentation = async () => {
    try {
      const response = await fetch(
        locale === "en" ? "/HOOKS.en.md" : "/HOOKS.zh-CN.md",
      );
      const fallback = await fetch("/HOOKS.zh-CN.md");
      const text = response.ok ? await response.text() : await fallback.text();
      setDocContent(text);
      setIsGuideOpen(true);
    } catch (error) {
      console.error("Failed to load documentation:", error);
    }
  };

  const fetchHooks = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch("/api/hooks");
      if (res.ok) {
        const data: HooksConfigResponse = await res.json();
        setHooks(data.hooks || []);
      }
    } catch (e) {
      console.error("Failed to fetch hooks:", e);
    } finally {
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
        setIsDialogOpen(false);
      } else {
        console.error("Failed to save hooks");
      }
    } catch (e) {
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
        setHooks((prev) =>
          prev.map((h) =>
            h.name === name ? { ...h, enabled: !currentEnabled } : h,
          ),
        );
      }
    } catch (e) {
      console.error(e);
    } finally {
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
    setTargetBindingText("{}");
    setRecoveryAnchorText("{}");
    setSourceMetadataText("{}");
    setIsDialogOpen(true);
  };

  const openEditDialog = (hook: HookConfig) => {
    setEditingHookName(hook.name);
    setFormData({ ...hook });
    setEventsInput(hook.events.join(", "));
    setTargetBindingText(formatJsonField(hook.targetBinding));
    setRecoveryAnchorText(formatJsonField(hook.recoveryAnchor));
    setSourceMetadataText(formatJsonField(hook.sourceMetadata));
    setIsDialogOpen(true);
  };

  const handleDelete = async (hookName: string) => {
    if (
      confirm(
        t(
          lt(
            `确定要删除钩子 "${hookName}" 吗？`,
            `Delete hook "${hookName}"?`,
          ),
        ),
      )
    ) {
      const newHooks = hooks.filter((h) => h.name !== hookName);
      await saveHooksConfig(newHooks);
    }
  };

  const handleSaveHook = async () => {
    let targetBinding: Record<string, unknown> | undefined;
    let recoveryAnchor: Record<string, unknown> | undefined;
    let sourceMetadata: Record<string, unknown> | undefined;
    try {
      targetBinding = parseJsonField("targetBinding", targetBindingText);
      recoveryAnchor = parseJsonField("recoveryAnchor", recoveryAnchorText);
      sourceMetadata = parseJsonField("sourceMetadata", sourceMetadataText);
    } catch (error) {
      alert(error instanceof Error ? error.message : t(lt("Wake ingress JSON 配置无效。", "Wake ingress JSON is invalid.")));
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
      targetBinding,
      recoveryAnchor,
      sourceMetadata,
    };

    let newHooks: HookConfig[];
    if (editingHookName) {
      // Editing Mode
      newHooks = hooks.map((h) =>
        h.name === editingHookName ? updatedHook : h,
      );
    } else {
      // Adding Mode (Check for duplicate)
      if (hooks.find((h) => h.name === updatedHook.name)) {
        alert(t(lt("已存在同名的钩子。", "A hook with the same name already exists.")));
        return;
      }
      newHooks = [...hooks, updatedHook];
    }
    await saveHooksConfig(newHooks);
  };

  const getIconForType = (type: string) => {
    switch (type) {
      case "command":
        return <Terminal className="h-4 w-4" />;
      case "python":
        return <Code className="h-4 w-4" />;
      case "agent":
        return <Zap className="h-4 w-4" />;
      default:
        return <Code className="h-4 w-4" />;
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t(lt("动作钩子管理", "Hooks"))}</h1>
          <p className="text-muted-foreground">
            {t(lt("管理命令、脚本和 AutomationRuntime 在关键时机触发的动作。", "Manage commands, scripts, and AutomationRuntime tasks triggered at key lifecycle moments."))}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={loadDocumentation} variant="secondary">
            <BookOpen className="mr-2 h-4 w-4" />
            {t(lt("配置教学", "Guide"))}
          </Button>
          <Button onClick={fetchHooks} variant="outline" disabled={isLoading}>
            <RefreshCw
              className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
            />
            {t(lt("刷新", "Refresh"))}
          </Button>
          <Button onClick={openAddDialog}>
            <Plus className="mr-2 h-4 w-4" />
            {t(lt("新建钩子", "New hook"))}
          </Button>
        </div>
      </div>

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>
              {editingHookName ? t(lt("编辑钩子", "Edit hook")) : t(lt("新建钩子", "New hook"))}
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">{t(lt("钩子名称", "Hook name"))}</Label>
              <Input
                id="name"
                value={formData.name}
                onChange={(e) =>
                  setFormData({ ...formData, name: e.target.value })
                }
                placeholder={t(lt("例如：代码执行前检查", "e.g. pre-run code check"))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type">{t(lt("触发类型", "Action type"))}</Label>
              <select
                id="type"
                className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                value={formData.type}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    type: e.target.value as HookConfig["type"],
                  })
                }
              >
                <option value="command">{t(lt("命令行脚本", "Command"))}</option>
                <option value="python">{t(lt("Python 脚本", "Python"))}</option>
                <option value="agent">{t(lt("AutomationRuntime 任务", "Automation task"))}</option>
              </select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="target">{t(lt("目标路径或命令", "Target path or command"))}</Label>
              <Input
                id="target"
                value={formData.target}
                onChange={(e) =>
                  setFormData({ ...formData, target: e.target.value })
                }
                placeholder={t(lt("例如：black . 或 core/scripts/linter.py", "e.g. black . or core/scripts/linter.py"))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="events">{t(lt("触发事件（用逗号分隔）", "Trigger events (comma separated)"))}</Label>
              <Input
                id="events"
                value={eventsInput}
                onChange={(e) => setEventsInput(e.target.value)}
                placeholder={t(lt("例如：on_agent_start, after_code_generated", "e.g. on_agent_start, after_code_generated"))}
              />
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="triggerKind">{t(lt("Wake 类型", "Wake trigger kind"))}</Label>
                <select
                  id="triggerKind"
                  className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={formData.triggerKind || "nudge"}
                  onChange={(e) => setFormData({ ...formData, triggerKind: e.target.value as HookConfig["triggerKind"] })}
                >
                  <option value="nudge">nudge</option>
                  <option value="wake">wake</option>
                  <option value="recovery_wake">recovery_wake</option>
                </select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="attachPolicy">{t(lt("附着策略", "Attach policy"))}</Label>
                <select
                  id="attachPolicy"
                  className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={formData.attachPolicy || "new_session"}
                  onChange={(e) => setFormData({ ...formData, attachPolicy: e.target.value as HookConfig["attachPolicy"] })}
                >
                  <option value="new_session">new_session</option>
                  <option value="attach_session">attach_session</option>
                  <option value="attach_run">attach_run</option>
                  <option value="resume_run">resume_run</option>
                </select>
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="wakeReason">{t(lt("唤醒原因", "Wake reason"))}</Label>
              <Input
                id="wakeReason"
                value={formData.wakeReason || ""}
                onChange={(e) => setFormData({ ...formData, wakeReason: e.target.value })}
                placeholder={t(lt("例如：scheduled_project_checkin", "e.g. scheduled_project_checkin"))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="message">{t(lt("消息模板", "Message"))}</Label>
              <Textarea
                id="message"
                rows={3}
                value={formData.message || ""}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setFormData({ ...formData, message: e.target.value })}
                placeholder={t(lt("没有 targetBinding / recoveryAnchor 时，这段消息只会作为 nudge 文本。", "Without targetBinding / recoveryAnchor this text only becomes a nudge message."))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="targetBinding">{t(lt("targetBinding（JSON）", "targetBinding (JSON)"))}</Label>
              <Textarea id="targetBinding" rows={4} value={targetBindingText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setTargetBindingText(e.target.value)} className="font-mono text-xs" />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="recoveryAnchor">{t(lt("recoveryAnchor（JSON）", "recoveryAnchor (JSON)"))}</Label>
              <Textarea id="recoveryAnchor" rows={4} value={recoveryAnchorText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setRecoveryAnchorText(e.target.value)} className="font-mono text-xs" />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="sourceMetadata">{t(lt("sourceMetadata（JSON）", "sourceMetadata (JSON)"))}</Label>
              <Textarea id="sourceMetadata" rows={4} value={sourceMetadataText} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setSourceMetadataText(e.target.value)} className="font-mono text-xs" />
            </div>
            <div className="flex items-center gap-2 mt-2">
              <Switch
                id="enabled"
                checked={formData.enabled}
                onCheckedChange={(c) =>
                  setFormData({ ...formData, enabled: c })
                }
              />
              <Label htmlFor="enabled">{t(lt("启用此钩子", "Enable this hook"))}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
              {t(lt("取消", "Cancel"))}
            </Button>
            <Button
              onClick={handleSaveHook}
              disabled={!formData.name || !formData.target}
            >
              {t(lt("保存", "Save"))}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DocumentationGuideDialog
        open={isGuideOpen}
        onOpenChange={setIsGuideOpen}
        title={t(lt("钩子使用说明", "Hook guide"))}
        content={docContent}
      />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {hooks.map((hook) => (
          <Card key={hook.name}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                {getIconForType(hook.type)}
                {hook.name}
              </CardTitle>
              <Switch
                checked={hook.enabled}
                onCheckedChange={() => handleToggle(hook.name, hook.enabled)}
                disabled={isToggling === hook.name}
              />
            </CardHeader>
            <CardContent>
              <div className="text-sm space-y-2 mt-2">
                <p>
                  <strong>{t(lt("类型：", "Type:"))}</strong>{" "}
                  <span>{t(getHookTypeLabel(hook.type))}</span>
                </p>
                <p>
                  <strong>{t(lt("Wake：", "Wake:"))}</strong>{" "}
                  <span>{hook.triggerKind || "nudge"}</span>
                </p>
                <p>
                  <strong>{t(lt("目标：", "Target:"))}</strong>{" "}
                  <code className="bg-muted px-1 rounded truncate block mt-1">
                    {hook.target}
                  </code>
                </p>
                <p className="text-xs text-muted-foreground">
                  {hook.targetBinding || hook.recoveryAnchor
                    ? t(lt("已配置显式 targetBinding / recoveryAnchor。", "Explicit targetBinding / recoveryAnchor configured."))
                    : t(lt("未提供 binding，运行时会自动降级为 nudge。", "No binding provided; runtime will degrade this trigger to nudge."))}
                </p>
                <div>
                  <strong>{t(lt("触发事件：", "Events:"))}</strong>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {hook.events.map((event) => (
                      <span
                        key={event}
                        className="bg-primary/10 text-primary text-xs px-2 py-0.5 rounded"
                      >
                        {event}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </CardContent>
            <CardFooter className="flex justify-end gap-2 border-t pt-4 mt-auto">
              <Button
                variant="outline"
                size="sm"
                onClick={() => openEditDialog(hook)}
              >
                <Pencil className="mr-2 h-4 w-4" /> {t(lt("编辑", "Edit"))}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleDelete(hook.name)}
              >
                <Trash2 className="mr-2 h-4 w-4" /> {t(lt("删除", "Delete"))}
              </Button>
            </CardFooter>
          </Card>
        ))}
        {hooks.length === 0 && (
          <div className="col-span-full text-center py-10 text-muted-foreground border rounded-lg border-dashed">
            {t(lt("还没有配置任何钩子。", "No hooks configured yet."))}
            <br />
            <Button onClick={openAddDialog} variant="outline" className="mt-4">
              <Plus className="mr-2 h-4 w-4" />
              {t(lt("新建第一个钩子", "Create the first hook"))}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
