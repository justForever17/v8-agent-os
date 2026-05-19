"use client";

import { useCallback, useEffect, useState } from "react";
import { Database, Loader2, Save, ShieldCheck, TrafficCone } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { ti } from "@/i18n/admin-legacy";
type SessionLanePolicy = "queue" | "reject" | "interrupt_then_replace";
type RuntimeStabilityPayload = {
  strictSupervisorDurability?: boolean;
  sessionLanePolicy?: SessionLanePolicy;
  paths?: {
    configPath?: string;
    stateDbPath?: string;
    checkpointDbPath?: string;
  };
  summaries?: {
    strictSupervisorDurability?: string;
    sessionLanePolicy?: Record<string, string>;
  };
};
const POLICY_OPTIONS: Array<{
  value: SessionLanePolicy;
  title: string;
  description: string;
}> = [{
  value: "queue",
  title: "components.runtime.RuntimeStabilityPanel.k09c98e28",
  description: "components.runtime.RuntimeStabilityPanel.k45db2c65"
}, {
  value: "reject",
  title: "components.runtime.RuntimeStabilityPanel.k74dbd7fe",
  description: "components.runtime.RuntimeStabilityPanel.k2faec923"
}, {
  value: "interrupt_then_replace",
  title: "components.runtime.RuntimeStabilityPanel.kfc82fe4c",
  description: "components.runtime.RuntimeStabilityPanel.k33ce5a8c"
}];
export function RuntimeStabilityPanel() {
  const t = useT();
  const {
    toast
  } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<RuntimeStabilityPayload>({
    strictSupervisorDurability: true,
    sessionLanePolicy: "queue",
    paths: {},
    summaries: {}
  });
  const loadConfig = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/runtime-stability", {
        cache: "no-store"
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || ti(t, "k408658f78d"));
      }
      setConfig({
        strictSupervisorDurability: Boolean(data?.strictSupervisorDurability ?? true),
        sessionLanePolicy: (data?.sessionLanePolicy || "queue") as SessionLanePolicy,
        paths: data?.paths || {},
        summaries: data?.summaries || {}
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeStabilityPanel.k4e9dbcba",
        description: error instanceof Error ? error.message : ti(t, "k5f76edc5de")
      });
    } finally {
      setLoading(false);
    }
  }, [t, toast]);
  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);
  const saveConfig = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/runtime-stability", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          strictSupervisorDurability: Boolean(config.strictSupervisorDurability),
          sessionLanePolicy: config.sessionLanePolicy || "queue"
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || ti(t, "k66d4f1d131"));
      }
      toast({
        title: "components.runtime.RuntimeStabilityPanel.k9bfe7d6d",
        description: "components.runtime.RuntimeStabilityPanel.k250649f7"
      });
      setConfig({
        strictSupervisorDurability: Boolean(data?.strictSupervisorDurability ?? true),
        sessionLanePolicy: (data?.sessionLanePolicy || "queue") as SessionLanePolicy,
        paths: data?.paths || {},
        summaries: data?.summaries || {}
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeStabilityPanel.k12769ce1",
        description: error instanceof Error ? error.message : ti(t, "k5f76edc5de")
      });
    } finally {
      setSaving(false);
    }
  };
  if (loading) {
    return <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>;
  }
  return <div className="space-y-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">{ti(t, "k04f443a030")}</h1>
                    <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                        {ti(t, "ke4a88fb83d")}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Badge variant="outline">{ti(t, "k66c99c8687")}</Badge>
                    <Badge variant="secondary">{ti(t, "kae782f2aeb")}</Badge>
                </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5 text-emerald-600" />
                            <CardTitle>{ti(t, "k939ee6c6e2")}</CardTitle>
                        </div>
                        <CardDescription>
                            {config.summaries?.strictSupervisorDurability || ti(t, "k2e9a23fd54")}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex items-center justify-between rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium">{ti(t, "k38c8aca2ba")}</div>
                                <div className="text-xs text-muted-foreground">
                                    {ti(t, "k16fe2a63d8")}
                                </div>
                            </div>
                            <Switch checked={Boolean(config.strictSupervisorDurability)} onCheckedChange={checked => setConfig(current => ({
              ...current,
              strictSupervisorDurability: checked
            }))} />

                        </div>

                        <div className="rounded-xl border border-border/60 bg-background px-4 py-3 text-sm">
                            <div className="font-medium">{ti(t, "kbf1a6e9f9c")}</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.configPath || "~/.v8-agent-os/config.json#runtimeStability"}</div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <Database className="h-5 w-5 text-sky-600" />
                            <CardTitle>{ti(t, "k2f74f4c7f8")}</CardTitle>
                        </div>
                        <CardDescription>{ti(t, "kc1f24be8ba")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm">
                        <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="font-medium">{ti(t, "ke00981dabb")}</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.stateDbPath || "~/.v8-agent-os/state.db"}</div>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="font-medium">Graph Checkpoint</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.checkpointDbPath || "~/.v8-agent-os/checkpoints.db"}</div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <div className="flex items-center gap-2">
                        <TrafficCone className="h-5 w-5 text-amber-600" />
                        <CardTitle>{ti(t, "kb05897cb29")}</CardTitle>
                    </div>
                    <CardDescription>
                        {config.summaries?.sessionLanePolicy?.[config.sessionLanePolicy || "queue"] || ti(t, "kfe367770b4")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 lg:grid-cols-3">
                    {POLICY_OPTIONS.map(option => {
          const active = (config.sessionLanePolicy || "queue") === option.value;
          return <button key={option.value} type="button" className={cn("rounded-2xl border px-4 py-4 text-left transition-colors", active ? "border-emerald-500/60 bg-emerald-500/10 shadow-sm" : "border-border/60 bg-background hover:border-foreground/20 hover:bg-muted/30")} onClick={() => setConfig(current => ({
            ...current,
            sessionLanePolicy: option.value
          }))}>

                                <div className="text-sm font-semibold">{option.title}</div>
                                <div className="mt-2 text-xs leading-5 text-muted-foreground">{option.description}</div>
                            </button>;
        })}
                </CardContent>
            </Card>

            <div className="flex flex-wrap items-center justify-end gap-2">
                <Button variant="outline" onClick={() => void loadConfig()} disabled={saving}>
                    {ti(t, "k7784972fc8")}
                </Button>
                <Button onClick={() => void saveConfig()} disabled={saving}>
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                    {ti(t, "k45de6be9e3")}
                </Button>
            </div>
        </div>;
}
