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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface HookConfig {
  name: string;
  type: "command" | "python" | "agent";
  target: string;
  events: string[];
  enabled: boolean;
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
  });
  const [eventsInput, setEventsInput] = useState("");

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
    });
    setEventsInput("");
    setIsDialogOpen(true);
  };

  const openEditDialog = (hook: HookConfig) => {
    setEditingHookName(hook.name);
    setFormData({ ...hook });
    setEventsInput(hook.events.join(", "));
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
    const updatedEvents = eventsInput
      .split(",")
      .map((e) => e.trim())
      .filter((e) => e.length > 0);
    const updatedHook = { ...formData, events: updatedEvents };

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

      <Dialog open={isGuideOpen} onOpenChange={setIsGuideOpen}>
        <DialogContent className="max-h-[80vh] sm:max-w-[700px] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t(lt("钩子使用说明", "Hook guide"))}</DialogTitle>
          </DialogHeader>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({ ...props }) => (
                  <h1 className="mt-6 mb-4 text-2xl font-bold" {...props} />
                ),
                h2: ({ ...props }) => (
                  <h2 className="mt-5 mb-3 text-xl font-semibold border-b pb-2" {...props} />
                ),
                h3: ({ ...props }) => (
                  <h3 className="mt-4 mb-2 text-lg font-medium" {...props} />
                ),
                p: ({ ...props }) => (
                  <p className="mb-4 leading-relaxed text-muted-foreground" {...props} />
                ),
                ul: ({ ...props }) => (
                  <ul className="mb-4 ml-6 list-disc space-y-1" {...props} />
                ),
                ol: ({ ...props }) => (
                  <ol className="mb-4 ml-6 list-decimal space-y-1" {...props} />
                ),
                li: ({ ...props }) => (
                  <li className="text-muted-foreground" {...props} />
                ),
                strong: ({ ...props }) => (
                  <strong className="font-semibold text-foreground" {...props} />
                ),
                blockquote: ({ ...props }) => (
                  <blockquote
                    className="my-4 border-l-4 border-primary pl-4 italic text-muted-foreground bg-muted p-2 rounded-r-md"
                    {...props}
                  />
                ),
                code: ({ className, children, ...props }) => {
                  const match = /language-(\w+)/.exec(className || "");
                  return match ? (
                    <pre className="my-4 overflow-x-auto rounded-md bg-muted p-4 block">
                      <code className={className} {...props}>
                        {children}
                      </code>
                    </pre>
                  ) : (
                    <code
                      className="rounded bg-muted px-[0.3rem] py-[0.2rem] font-mono text-sm text-primary"
                      {...props}
                    >
                      {children}
                    </code>
                  );
                },
              }}
            >
              {docContent}
            </ReactMarkdown>
          </div>
        </DialogContent>
      </Dialog>

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
                  <strong>{t(lt("目标：", "Target:"))}</strong>{" "}
                  <code className="bg-muted px-1 rounded truncate block mt-1">
                    {hook.target}
                  </code>
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
