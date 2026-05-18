"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { ti } from "@/i18n/admin-legacy";
type MachinePosture = "dedicated_runtime_host" | "developer_mixed_host";
type SafetyGuardianConfig = {
  enabled: boolean;
  machinePosture: MachinePosture;
  skillRules?: {
    declarationVerdict?: "allow" | "audit" | "review";
    localSecretReadVerdict?: "audit" | "review" | "block";
    binaryPayloadVerdict?: "audit" | "review" | "block";
    browserProfileAccessVerdict?: Record<MachinePosture, "review" | "block">;
    llmReviewEnabledFor?: Array<"review" | "block">;
  };
  networkMutationRules?: {
    defaultExternalMutationVerdict?: Record<MachinePosture, "audit" | "review">;
    sensitivePayloadVerdict?: "audit" | "review" | "block";
    credentialExfiltrationVerdict?: "review" | "block";
  };
  computerUseRules?: {
    defaultMutationVerdict?: Record<MachinePosture, "audit" | "review">;
    hotkeyLifecycleVerdict?: "audit" | "review" | "block";
    destructiveKeywordVerdict?: "review" | "block";
  };
  systemIntegrityRules?: {
    packageInstallVerdict?: Record<MachinePosture, "audit" | "review">;
    destructiveCommandVerdict?: "review" | "block";
  };
  v8IntegrityRules?: {
    protectedConfigWriteVerdict?: "audit" | "review" | "block";
    protectedRuntimeProcessVerdict?: "review" | "block";
  };
  channelGroupGuard?: {
    enabled?: boolean;
    allowlistOnly?: boolean;
    requireMention?: boolean;
    auditOnly?: boolean;
  };
  [key: string]: unknown;
};
const DEFAULT_CONFIG: SafetyGuardianConfig = {
  enabled: true,
  machinePosture: "dedicated_runtime_host",
  skillRules: {
    declarationVerdict: "audit",
    localSecretReadVerdict: "review",
    binaryPayloadVerdict: "review",
    browserProfileAccessVerdict: {
      dedicated_runtime_host: "review",
      developer_mixed_host: "block"
    },
    llmReviewEnabledFor: ["review"]
  },
  networkMutationRules: {
    defaultExternalMutationVerdict: {
      dedicated_runtime_host: "audit",
      developer_mixed_host: "review"
    },
    sensitivePayloadVerdict: "review",
    credentialExfiltrationVerdict: "block"
  },
  computerUseRules: {
    defaultMutationVerdict: {
      dedicated_runtime_host: "audit",
      developer_mixed_host: "review"
    },
    hotkeyLifecycleVerdict: "review",
    destructiveKeywordVerdict: "block"
  },
  systemIntegrityRules: {
    packageInstallVerdict: {
      dedicated_runtime_host: "audit",
      developer_mixed_host: "review"
    },
    destructiveCommandVerdict: "block"
  },
  v8IntegrityRules: {
    protectedConfigWriteVerdict: "review",
    protectedRuntimeProcessVerdict: "block"
  },
  channelGroupGuard: {
    enabled: false,
    allowlistOnly: false,
    requireMention: false,
    auditOnly: false
  }
};
type Translator = ReturnType<typeof useT>;
function postureLabel(t: Translator, value?: string | null) {
  return value === "developer_mixed_host" ? t("app.admin.dashboard.safety.control.page.label.posture.developer") : t("app.admin.dashboard.safety.control.page.label.posture.dedicated");
}
function verdictLabel(t: Translator, value?: string | null) {
  if (value === "allow") return t("app.admin.dashboard.safety.control.page.label.verdict.allow");
  if (value === "audit") return t("app.admin.dashboard.safety.control.page.label.verdict.audit");
  if (value === "block") return t("app.admin.dashboard.safety.control.page.label.verdict.block");
  if (value === "review") return t("app.admin.dashboard.safety.control.page.label.verdict.review");
  return t("app.admin.dashboard.safety.control.page.label.unknown");
}
function verdictOptions<T extends string>(t: Translator, values: T[]) {
  return values.map(value => ({
    value,
    label: verdictLabel(t, value)
  }));
}
function normalizeConfig(data: unknown): SafetyGuardianConfig {
  const raw = data && typeof data === "object" ? data as SafetyGuardianConfig : DEFAULT_CONFIG;
  const browserProfileAccessVerdict: Record<MachinePosture, "review" | "block"> = {
    dedicated_runtime_host: raw.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host ?? DEFAULT_CONFIG.skillRules!.browserProfileAccessVerdict!.dedicated_runtime_host,
    developer_mixed_host: raw.skillRules?.browserProfileAccessVerdict?.developer_mixed_host ?? DEFAULT_CONFIG.skillRules!.browserProfileAccessVerdict!.developer_mixed_host
  };
  const defaultExternalMutationVerdict: Record<MachinePosture, "audit" | "review"> = {
    dedicated_runtime_host: raw.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host ?? DEFAULT_CONFIG.networkMutationRules!.defaultExternalMutationVerdict!.dedicated_runtime_host,
    developer_mixed_host: raw.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host ?? DEFAULT_CONFIG.networkMutationRules!.defaultExternalMutationVerdict!.developer_mixed_host
  };
  const defaultMutationVerdict: Record<MachinePosture, "audit" | "review"> = {
    dedicated_runtime_host: raw.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host ?? DEFAULT_CONFIG.computerUseRules!.defaultMutationVerdict!.dedicated_runtime_host,
    developer_mixed_host: raw.computerUseRules?.defaultMutationVerdict?.developer_mixed_host ?? DEFAULT_CONFIG.computerUseRules!.defaultMutationVerdict!.developer_mixed_host
  };
  const packageInstallVerdict: Record<MachinePosture, "audit" | "review"> = {
    dedicated_runtime_host: raw.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host ?? DEFAULT_CONFIG.systemIntegrityRules!.packageInstallVerdict!.dedicated_runtime_host,
    developer_mixed_host: raw.systemIntegrityRules?.packageInstallVerdict?.developer_mixed_host ?? DEFAULT_CONFIG.systemIntegrityRules!.packageInstallVerdict!.developer_mixed_host
  };
  return {
    ...DEFAULT_CONFIG,
    ...raw,
    machinePosture: raw.machinePosture === "developer_mixed_host" ? "developer_mixed_host" : "dedicated_runtime_host",
    skillRules: {
      ...DEFAULT_CONFIG.skillRules,
      ...(raw.skillRules || {}),
      browserProfileAccessVerdict
    },
    networkMutationRules: {
      ...DEFAULT_CONFIG.networkMutationRules,
      ...(raw.networkMutationRules || {}),
      defaultExternalMutationVerdict
    },
    computerUseRules: {
      ...DEFAULT_CONFIG.computerUseRules,
      ...(raw.computerUseRules || {}),
      defaultMutationVerdict
    },
    systemIntegrityRules: {
      ...DEFAULT_CONFIG.systemIntegrityRules,
      ...(raw.systemIntegrityRules || {}),
      packageInstallVerdict
    },
    v8IntegrityRules: {
      ...DEFAULT_CONFIG.v8IntegrityRules,
      ...(raw.v8IntegrityRules || {})
    },
    channelGroupGuard: {
      ...DEFAULT_CONFIG.channelGroupGuard,
      ...(raw.channelGroupGuard || {})
    }
  };
}
function VerdictSelect<T extends string>({
  value,
  onChange,
  options
}: {
  value: T;
  onChange: (next: T) => void;
  options: Array<{
    value: T;
    label: string;
  }>;
}) {
  return <Select value={value} onValueChange={next => onChange(next as T)}>
            <SelectTrigger>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {options.map(option => <SelectItem key={option.value} value={option.value}>
                        {option.label}
                    </SelectItem>)}
            </SelectContent>
        </Select>;
}
export function SafetyGuardianPanel() {
  const t = useT();
  const [config, setConfig] = useState<SafetyGuardianConfig>(DEFAULT_CONFIG);
  const [rawJson, setRawJson] = useState(JSON.stringify(DEFAULT_CONFIG, null, 2));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [parseError, setParseError] = useState("");
  const syncConfig = useCallback((next: unknown) => {
    const normalized = normalizeConfig(next);
    setConfig(normalized);
    setRawJson(JSON.stringify(normalized, null, 2));
    setParseError("");
  }, []);
  const loadConfig = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/settings/safety-guardian", {
        cache: "no-store"
      });
      const payload = response.ok ? await response.json().catch(() => ({})) : {};
      syncConfig(payload);
    } finally {
      setLoading(false);
    }
  }, [syncConfig]);
  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);
  const summaryBadges = useMemo(() => [config.enabled ? ti(t, "k59469aaf39") : ti(t, "k962178f2c8"), `${t("app.admin.dashboard.safety.control.page.field.machinePosture")}: ${postureLabel(t, config.machinePosture)}`, `${ti(t, "kca5e86463f")}: ${verdictLabel(t, config.skillRules?.declarationVerdict)} / ${verdictLabel(t, config.skillRules?.localSecretReadVerdict)}`], [config, t]);
  const updateAndSync = (updater: (previous: SafetyGuardianConfig) => SafetyGuardianConfig) => {
    setConfig(previous => {
      const next = normalizeConfig(updater(previous));
      setRawJson(JSON.stringify(next, null, 2));
      setParseError("");
      return next;
    });
  };
  const saveConfig = async () => {
    setSaving(true);
    try {
      const parsed = JSON.parse(rawJson);
      setParseError("");
      const response = await fetch("/api/settings/safety-guardian", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(parsed)
      });
      const payload = response.ok ? await response.json().catch(() => ({})) : {};
      syncConfig(payload?.config || parsed);
    } catch (error) {
      setParseError(error instanceof Error ? error.message : ti(t, "k2a0c230780"));
    } finally {
      setSaving(false);
    }
  };
  if (loading) {
    return <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardContent className="py-8 text-sm text-slate-500">{ti(t, "k5030156a3f")}</CardContent>
            </Card>;
  }
  return <div className="space-y-6">
            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <div className="flex items-start justify-between gap-4">
                        <div className="space-y-2">
                            <CardTitle className="flex items-center gap-2 text-base">
                                <ShieldCheck className="h-4 w-4 text-sky-600" />
                                {ti(t, "kc93d1a8986")}
                            </CardTitle>
                            <CardDescription>{ti(t, "k1d79ff5b87")}</CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => void loadConfig()} disabled={saving}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                {ti(t, "k38108eaa1d")}
                            </Button>
                            <Button size="sm" onClick={() => void saveConfig()} disabled={saving}>
                                <Save className="mr-2 h-4 w-4" />
                                {saving ? ti(t, "k6644f06197") : ti(t, "kf5d4126103")}
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                    {summaryBadges.map(badge => <Badge key={badge} variant="outline">
                            {badge}
                        </Badge>)}
                </CardContent>
            </Card>

            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">{ti(t, "k7014655986")}</CardTitle>
                    <CardDescription>{ti(t, "ka5024c64d9")}</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.safety.control.page.field.machinePosture")}</Label>
                        <VerdictSelect<MachinePosture> value={config.machinePosture} onChange={next => updateAndSync(previous => ({
            ...previous,
            machinePosture: next
          }))} options={[{
            value: "dedicated_runtime_host",
            label: postureLabel(t, "dedicated_runtime_host")
          }, {
            value: "developer_mixed_host",
            label: postureLabel(t, "developer_mixed_host")
          }]} />

                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "k3ba0189e84")}</div>
                            <div className="text-xs text-slate-500">{ti(t, "k47ef7efee1")}</div>
                        </div>
                        <Switch checked={config.enabled} onCheckedChange={checked => updateAndSync(previous => ({
            ...previous,
            enabled: checked
          }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>{ti(t, "kca5e86463f")}</Label>
                        <VerdictSelect<"allow" | "audit" | "review"> value={config.skillRules?.declarationVerdict ?? "audit"} onChange={next => updateAndSync(previous => ({
            ...previous,
            skillRules: {
              ...previous.skillRules,
              declarationVerdict: next
            }
          }))} options={verdictOptions(t, ["allow", "audit", "review"])} />

                    </div>
                    <div className="space-y-2">
                        <Label>{ti(t, "k99a0615912")}</Label>
                        <VerdictSelect<"audit" | "review" | "block"> value={config.skillRules?.localSecretReadVerdict ?? "review"} onChange={next => updateAndSync(previous => ({
            ...previous,
            skillRules: {
              ...previous.skillRules,
              localSecretReadVerdict: next
            }
          }))} options={verdictOptions(t, ["audit", "review", "block"])} />

                    </div>
                    <div className="space-y-2">
                        <Label>{ti(t, "k701cb8bdf7")}</Label>
                        <VerdictSelect<"audit" | "review"> value={config.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host ?? "audit"} onChange={next => updateAndSync(previous => ({
            ...previous,
            networkMutationRules: {
              ...previous.networkMutationRules,
              defaultExternalMutationVerdict: {
                dedicated_runtime_host: next,
                developer_mixed_host: previous.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host ?? "review"
              }
            }
          }))} options={verdictOptions(t, ["audit", "review"])} />

                    </div>
                    <div className="space-y-2">
                        <Label>{ti(t, "k5b0d1c630a")}</Label>
                        <VerdictSelect<"audit" | "review"> value={config.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host ?? "audit"} onChange={next => updateAndSync(previous => ({
            ...previous,
            computerUseRules: {
              ...previous.computerUseRules,
              defaultMutationVerdict: {
                dedicated_runtime_host: next,
                developer_mixed_host: previous.computerUseRules?.defaultMutationVerdict?.developer_mixed_host ?? "review"
              }
            }
          }))} options={verdictOptions(t, ["audit", "review"])} />

                    </div>
                    <div className="space-y-2">
                        <Label>{ti(t, "k56c6e05480")}</Label>
                        <VerdictSelect<"review" | "block"> value={config.skillRules?.browserProfileAccessVerdict?.developer_mixed_host ?? "block"} onChange={next => updateAndSync(previous => ({
            ...previous,
            skillRules: {
              ...previous.skillRules,
              browserProfileAccessVerdict: {
                dedicated_runtime_host: previous.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host ?? "review",
                developer_mixed_host: next
              }
            }
          }))} options={verdictOptions(t, ["review", "block"])} />

                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "k87828ffb47")}</div>
                            <div className="text-xs text-slate-500">{ti(t, "k196fd9bec5")}</div>
                        </div>
                        <Switch checked={Boolean(config.channelGroupGuard?.auditOnly)} onCheckedChange={checked => updateAndSync(previous => ({
            ...previous,
            channelGroupGuard: {
              ...previous.channelGroupGuard,
              auditOnly: checked
            }
          }))} />

                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "kb33c9229a3")}</div>
                            <div className="text-xs text-slate-500">{ti(t, "k8a574f21e1")}</div>
                        </div>
                        <Switch checked={Boolean(config.skillRules?.llmReviewEnabledFor?.includes("review"))} onCheckedChange={checked => updateAndSync(previous => ({
            ...previous,
            skillRules: {
              ...previous.skillRules,
              llmReviewEnabledFor: checked ? ["review"] : []
            }
          }))} />

                    </div>
                </CardContent>
            </Card>

            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">{ti(t, "k255b15c831")}</CardTitle>
                    <CardDescription>{ti(t, "ke883c314b7")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    <Textarea value={rawJson} onChange={event => setRawJson(event.target.value)} className="min-h-[420px] font-mono text-xs" />
                    {parseError ? <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{parseError}</div> : null}
                </CardContent>
            </Card>
        </div>;
}
