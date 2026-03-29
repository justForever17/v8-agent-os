"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { getRuntimeDisplayName, isCoreRuntimeKind } from "@/lib/runtime-admin";

type RuntimePolicy = {
    enabled?: boolean;
};

type RuntimeDescriptor = {
    kind: string;
    displayName: string;
    summary?: string;
    policy?: RuntimePolicy;
};

type RuntimeSnapshot = {
    runtimes?: RuntimeDescriptor[];
};

export function RuntimeConfigWorkbench({
    kind,
    fallbackDisplayName,
    governanceHref = "/admin/runtime-governance",
}: {
    kind: string;
    fallbackDisplayName: string;
    governanceHref?: string;
}) {
    const t = useT();
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [runtime, setRuntime] = useState<RuntimeDescriptor | null>(null);

    const loadRuntime = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/runtime-capabilities", { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Runtime capabilities failed: ${response.status}`);
            }
            const payload: RuntimeSnapshot = await response.json().catch(() => ({}));
            const matched = (payload.runtimes || []).find((item) => item.kind === kind) || null;
            setRuntime(matched);
        } catch (error) {
            console.error("Failed to load runtime config workbench:", error);
            toast({
                title: t(lt("Runtime 加载失败", "Runtime load failed")),
                description: t(lt("当前无法读取 runtime 配置。", "Unable to read runtime config right now.")),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [kind, t, toast]);

    useEffect(() => {
        void loadRuntime();
    }, [loadRuntime]);

    const displayName = useMemo(
        () => getRuntimeDisplayName({ kind, displayName: runtime?.displayName || fallbackDisplayName }),
        [fallbackDisplayName, kind, runtime?.displayName],
    );

    const enabled = runtime ? runtime.policy?.enabled !== false : false;
    const disabled = isCoreRuntimeKind(kind) || !runtime;

    const handleToggle = useCallback(async (checked: boolean) => {
        if (disabled) return;
        setSaving(true);
        try {
            const response = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: checked }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload?.detail || payload?.error || response.status));
            }
            const matched = ((payload?.snapshot?.runtimes as RuntimeDescriptor[] | undefined) || []).find((item) => item.kind === kind) || null;
            setRuntime(matched);
        } catch (error) {
            console.error("Failed to save runtime config:", error);
            toast({
                title: t(lt("保存失败", "Save failed")),
                description: t(lt("当前无法更新 runtime 状态。", "Unable to update runtime state right now.")),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    }, [disabled, kind, t, toast]);

    return (
        <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
            <CardContent className="space-y-4 p-6">
                {loading ? (
                    <div className="flex h-24 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/80">
                        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                    </div>
                ) : (
                    <>
                        <div className="flex items-center justify-between gap-4">
                            <div className="space-y-2">
                                <div className="text-sm font-semibold text-slate-900">{displayName}</div>
                                <div className="text-xs text-slate-500">
                                    {runtime
                                        ? (enabled ? t(lt("当前已启用。", "Currently enabled.")) : t(lt("当前已关闭。", "Currently disabled.")))
                                        : t(lt("当前 runtime 尚未注册到 capability registry。", "This runtime is not registered in the capability registry yet."))}
                                </div>
                            </div>
                            <Switch
                                checked={enabled}
                                disabled={disabled || saving}
                                onCheckedChange={(checked) => void handleToggle(checked)}
                                aria-label={`${displayName} toggle`}
                                className={disabled ? "data-[state=checked]:bg-slate-300 data-[state=unchecked]:bg-slate-200" : undefined}
                            />
                        </div>
                        <div className="flex justify-end">
                            <Link
                                href={governanceHref}
                                className="inline-flex items-center gap-2 text-sm font-medium text-sky-700 transition hover:text-sky-800"
                            >
                                {t(lt("进入 Runtime 治理", "Open Runtime governance"))}
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </div>
                    </>
                )}
            </CardContent>
        </Card>
    );
}
