"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";
import {
  DESKTOP_PET_EVENT_CATALOG,
  desktopPetEventLabel,
  expandLegacyDesktopPetEvents,
  normalizeDesktopPetEventId,
  type DesktopPetEventId,
} from "@v8/session-realtime";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import type { ShellDesktopPetState } from "@/components/layout/ShellWindowControls";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useLocale } from "@/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache, primeAdminJsonCache } from "@/lib/admin-client-cache";

type DesktopPetActionRule = {
  id?: string;
  event?: DesktopPetEventId | string;
  /** @deprecated Read-only compatibility for pre-event-catalog configurations. */
  match?: string;
  emotion?: string;
  spectrum?: string;
};

type DesktopPetVoiceRule = {
  event?: DesktopPetEventId | string;
  /** @deprecated Read-only compatibility for pre-event-catalog configurations. */
  match?: string;
  phrase?: string;
  emotion?: string;
  speak?: boolean;
};

type DesktopPetAttachmentCapture = {
  cameraEnabled?: boolean;
  includeDesktopScreenshot?: boolean;
  layout?: "desktop_pip_camera" | string;
};

type DesktopPetConfig = {
  appearance?: {
    petScale?: number;
    floatAmplitude?: number;
    floatSpeed?: number;
  };
  eventVoice?: {
    enabled?: boolean;
    mode?: string;
    voiceRef?: string;
    speakVoiceTags?: boolean;
    speakSupervisorReplies?: boolean;
    customRules?: DesktopPetVoiceRule[];
  };
  actionTable?: DesktopPetActionRule[];
  effectSpectrum?: {
    preset?: string;
    intensity?: number;
    customGlowColor?: string;
  };
  attachmentCapture?: DesktopPetAttachmentCapture;
};

type ConfigEnvelope = {
  data: DesktopPetConfig;
  sourcePath?: string | null;
  source?: string | null;
  savePath?: string | string[] | null;
  reloadRequired?: boolean;
  key?: string | null;
  exists?: boolean;
};

type EventRuleRow = {
  key: string;
  id?: string;
  event: DesktopPetEventId;
  phrase: string;
  emotion: string;
  spectrum: string;
  speak: boolean;
  custom?: boolean;
};

const API_PATH = "/api/config-registry/desktop-pet";

const DEFAULT_ENVELOPE: ConfigEnvelope = {
  data: {
    appearance: {
      petScale: 1,
      floatAmplitude: 14,
      floatSpeed: 3.5,
    },
    eventVoice: {
      enabled: true,
      mode: "system_tts",
      voiceRef: "",
      speakVoiceTags: true,
      speakSupervisorReplies: true,
      customRules: [],
    },
    actionTable: [],
    effectSpectrum: {
      preset: "soft",
      intensity: 0.75,
      customGlowColor: "#66e3ff",
    },
    attachmentCapture: {
      cameraEnabled: false,
      includeDesktopScreenshot: false,
      layout: "desktop_pip_camera",
    },
  },
};

function normalizeDesktopPetEnvelope(payload?: ConfigEnvelope | null): ConfigEnvelope {
  return {
    ...DEFAULT_ENVELOPE,
    ...(payload || {}),
    data: {
      ...DEFAULT_ENVELOPE.data,
      ...(payload?.data || {}),
      appearance: {
        ...DEFAULT_ENVELOPE.data.appearance,
        ...(payload?.data?.appearance || {}),
      },
      eventVoice: {
        ...DEFAULT_ENVELOPE.data.eventVoice,
        ...(payload?.data?.eventVoice || {}),
      },
      effectSpectrum: {
        ...DEFAULT_ENVELOPE.data.effectSpectrum,
        ...(payload?.data?.effectSpectrum || {}),
      },
      attachmentCapture: {
        ...DEFAULT_ENVELOPE.data.attachmentCapture,
        ...(payload?.data?.attachmentCapture || {}),
      },
    },
  };
}

const DEFAULT_EVENT_IDS: DesktopPetEventId[] = [
  "run.reasoning.delta",
  "tool.started",
  "tool.finished",
  "ask_user.requested",
  "approval.requested",
  "artifact.recorded",
  "run.completed",
  "run.failed",
];

function defaultPhraseForEvent(eventId: DesktopPetEventId, t: (key: string) => string) {
  if (eventId === "ask_user.requested" || eventId === "approval.requested") {
    return t("app.admin.dashboard.desktopPet.defaultPhrases.waiting");
  }
  if (eventId === "artifact.recorded") return t("app.admin.dashboard.desktopPet.defaultPhrases.artifact");
  if (eventId === "run.completed") return t("app.admin.dashboard.desktopPet.defaultPhrases.done");
  if (eventId === "run.failed" || eventId === "agent.failed" || eventId === "subagent.task.failed") {
    return t("app.admin.dashboard.desktopPet.defaultPhrases.failed");
  }
  return "";
}

const ACTION_OPTIONS = [
  { value: "idle", labelKey: "app.admin.dashboard.desktopPet.actions.idle" },
  { value: "thinking", labelKey: "app.admin.dashboard.desktopPet.actions.thinking" },
  { value: "tool_calling", labelKey: "app.admin.dashboard.desktopPet.actions.toolCalling" },
  { value: "listening", labelKey: "app.admin.dashboard.desktopPet.actions.listening" },
  { value: "curious", labelKey: "app.admin.dashboard.desktopPet.actions.curious" },
  { value: "happy", labelKey: "app.admin.dashboard.desktopPet.actions.happy" },
  { value: "worried", labelKey: "app.admin.dashboard.desktopPet.actions.worried" },
  { value: "resting", labelKey: "app.admin.dashboard.desktopPet.actions.resting" },
];

const SPECTRUM_OPTIONS = [
  { value: "cyan", labelKey: "app.admin.dashboard.desktopPet.spectrum.cyan" },
  { value: "violet", labelKey: "app.admin.dashboard.desktopPet.spectrum.violet" },
  { value: "blue", labelKey: "app.admin.dashboard.desktopPet.spectrum.blue" },
  { value: "golden_amber", labelKey: "app.admin.dashboard.desktopPet.spectrum.amber" },
  { value: "emerald_green", labelKey: "app.admin.dashboard.desktopPet.spectrum.green" },
  { value: "crimson_red", labelKey: "app.admin.dashboard.desktopPet.spectrum.red" },
];

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  const numberValue = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.min(max, Math.max(min, numberValue));
}

function normalizeText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function normalizeAdminVoiceMode(value: unknown): string {
  const mode = normalizeText(value).trim().toLowerCase();
  if (mode === "voice_tag" || mode === "voice_tag_only" || mode === "voice-tag-only") return "voice_tag_only";
  if (mode === "muted" || mode === "off" || mode === "disabled" || mode === "none") return "off";
  return "system_tts";
}

function eventIdsForRule(rule: DesktopPetActionRule | DesktopPetVoiceRule): DesktopPetEventId[] {
  const exact = normalizeDesktopPetEventId(rule.event);
  return exact ? [exact] : expandLegacyDesktopPetEvents(rule.match || ("id" in rule ? rule.id : ""));
}

function buildEventRows(config: DesktopPetConfig, t: (key: string) => string): EventRuleRow[] {
  const actions = Array.isArray(config.actionTable) ? config.actionTable : [];
  const voices = Array.isArray(config.eventVoice?.customRules) ? config.eventVoice?.customRules ?? [] : [];
  const actionByEvent = new Map<DesktopPetEventId, DesktopPetActionRule>();
  const voiceByEvent = new Map<DesktopPetEventId, DesktopPetVoiceRule>();
  actions.forEach((action) => {
    eventIdsForRule(action).forEach((eventId) => {
      if (!actionByEvent.has(eventId)) actionByEvent.set(eventId, action);
    });
  });
  voices.forEach((voice) => {
    eventIdsForRule(voice).forEach((eventId) => {
      if (!voiceByEvent.has(eventId)) voiceByEvent.set(eventId, voice);
    });
  });

  const hasStructuredEvents = actions.some((rule) => Boolean(normalizeDesktopPetEventId(rule.event)))
    || voices.some((rule) => Boolean(normalizeDesktopPetEventId(rule.event)));
  const eventIds = [...new Set([
    ...(hasStructuredEvents ? [] : DEFAULT_EVENT_IDS),
    ...actionByEvent.keys(),
    ...voiceByEvent.keys(),
  ])];

  return eventIds.map((eventId) => {
    const action = actionByEvent.get(eventId);
    const voice = voiceByEvent.get(eventId);
    const catalog = DESKTOP_PET_EVENT_CATALOG.find((candidate) => candidate.id === eventId)!;
    return {
      key: `event:${eventId}`,
      id: normalizeText(action?.id).trim() || eventId.replace(/[^a-zA-Z0-9_-]/g, "_"),
      event: eventId,
      phrase: normalizeText(voice?.phrase) || defaultPhraseForEvent(eventId, t),
      emotion: normalizeText(voice?.emotion || action?.emotion || catalog.defaultEmotion),
      spectrum: normalizeText(action?.spectrum || catalog.defaultSpectrum),
      speak: voice ? voice.speak !== false : catalog.defaultSpeak,
      custom: true,
    };
  });
}

function rowsToConfig(rows: EventRuleRow[], config: DesktopPetConfig): DesktopPetConfig {
  const normalizedByEvent = new Map<DesktopPetEventId, EventRuleRow>();
  rows.forEach((row) => {
    const event = normalizeDesktopPetEventId(row.event);
    if (!event) return;
    normalizedByEvent.set(event, {
      ...row,
      event,
      phrase: row.phrase.trim(),
      emotion: row.emotion.trim() || "idle",
      spectrum: row.spectrum.trim() || "cyan",
    });
  });
  const normalized = [...normalizedByEvent.values()];
  const legacyActions = (config.actionTable || []).filter((rule) => eventIdsForRule(rule).length === 0);
  const legacyVoices = (config.eventVoice?.customRules || []).filter((rule) => eventIdsForRule(rule).length === 0);

  return {
    ...config,
    actionTable: [
      ...normalized.map((row): DesktopPetActionRule => ({
        id: row.id || row.event.replace(/[^a-zA-Z0-9_-]/g, "_"),
        event: row.event,
        emotion: row.emotion,
        spectrum: row.spectrum,
      })),
      ...legacyActions,
    ],
    eventVoice: {
      ...(config.eventVoice || {}),
      customRules: [
        ...normalized.map((row): DesktopPetVoiceRule => ({
          event: row.event,
          phrase: row.phrase,
          emotion: row.emotion,
          speak: row.speak,
        })),
        ...legacyVoices,
      ],
    },
  };
}

export default function DesktopPetSettingsPage() {
  const { locale, t } = useLocale();
  const [envelope, setEnvelope] = useState<ConfigEnvelope>(() => (
    normalizeDesktopPetEnvelope(peekAdminJsonCache<ConfigEnvelope>(API_PATH))
  ));
  const [loading, setLoading] = useState(() => peekAdminJsonCache<ConfigEnvelope>(API_PATH) === undefined);
  const [saving, setSaving] = useState(false);
  const [runtimeState, setRuntimeState] = useState<ShellDesktopPetState | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const data = useMemo(() => envelope.data || {}, [envelope.data]);
  const eventRows = useMemo(() => buildEventRows(data, t), [data, t]);
  const attachmentCapture = {
    cameraEnabled: data.attachmentCapture?.cameraEnabled === true,
    includeDesktopScreenshot: data.attachmentCapture?.includeDesktopScreenshot === true,
    layout: data.attachmentCapture?.layout || "desktop_pip_camera",
  };

  const loadConfig = useCallback(async (force = false) => {
    if (peekAdminJsonCache<ConfigEnvelope>(API_PATH) === undefined) setLoading(true);
    setError(null);
    try {
      const payload = await fetchAdminJson<ConfigEnvelope>(API_PATH, { force });
      setEnvelope(normalizeDesktopPetEnvelope(payload));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    const shell = window.v8osShell;
    if (!shell?.isShell) return;
    let mounted = true;
    void shell.getDesktopPetState().then((state) => {
      if (mounted) setRuntimeState(state);
    }).catch((runtimeError) => {
      if (mounted) setError(runtimeError instanceof Error ? runtimeError.message : String(runtimeError));
    });
    const unsubscribe = shell.onDesktopPetStateChange((state) => {
      setRuntimeState(state);
    });
    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  const updateData = useCallback((patch: (current: DesktopPetConfig) => DesktopPetConfig) => {
    setEnvelope((current) => ({
      ...current,
      data: patch(current.data || {}),
    }));
  }, []);

  const updateAppearance = useCallback(
    (patch: Partial<NonNullable<DesktopPetConfig["appearance"]>>) => {
      updateData((current) => ({
        ...current,
        appearance: {
          ...(current.appearance || {}),
          ...patch,
        },
      }));
    },
    [updateData],
  );

  const updateEventVoice = useCallback(
    (patch: Partial<NonNullable<DesktopPetConfig["eventVoice"]>>) => {
      updateData((current) => ({
        ...current,
        eventVoice: {
          ...(current.eventVoice || {}),
          ...patch,
        },
      }));
    },
    [updateData],
  );

  const updateEffectSpectrum = useCallback(
    (patch: Partial<NonNullable<DesktopPetConfig["effectSpectrum"]>>) => {
      updateData((current) => ({
        ...current,
        effectSpectrum: {
          ...(current.effectSpectrum || {}),
          ...patch,
        },
      }));
    },
    [updateData],
  );

  const updateAttachmentCapture = useCallback(
    (patch: Partial<DesktopPetAttachmentCapture>) => {
      updateData((current) => ({
        ...current,
        attachmentCapture: {
          ...(current.attachmentCapture || {}),
          layout: "desktop_pip_camera",
          ...patch,
        },
      }));
    },
    [updateData],
  );

  const updateEventRow = useCallback(
    (rowKey: string, patch: Partial<EventRuleRow>) => {
      updateData((current) => {
        const rows = buildEventRows(current, t).map((row) => (row.key === rowKey ? { ...row, ...patch } : row));
        return rowsToConfig(rows, current);
      });
    },
    [t, updateData],
  );

  const addCustomEventRow = useCallback(() => {
    updateData((current) => {
      const rows = buildEventRows(current, t);
      const usedEvents = new Set(rows.map((row) => row.event));
      const catalog = DESKTOP_PET_EVENT_CATALOG.find((event) => !usedEvents.has(event.id));
      if (!catalog) return current;
      const key = `custom_${Date.now()}`;
      return rowsToConfig(
        rows.concat({
          key: `custom:${key}`,
          id: key,
          event: catalog.id,
          phrase: defaultPhraseForEvent(catalog.id, t),
          emotion: catalog.defaultEmotion,
          spectrum: catalog.defaultSpectrum,
          speak: catalog.defaultSpeak,
          custom: true,
        }),
        current,
      );
    });
  }, [t, updateData]);

  const deleteEventRow = useCallback(
    (rowKey: string) => {
      updateData((current) => rowsToConfig(buildEventRows(current, t).filter((row) => row.key !== rowKey), current));
    },
    [t, updateData],
  );

  const saveAll = useCallback(async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(API_PATH, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: envelope.data || {} }),
      });
      const payload = (await response.json()) as { ok?: boolean; error?: string };
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      const nextEnvelope = normalizeDesktopPetEnvelope(envelope);
      setEnvelope(nextEnvelope);
      primeAdminJsonCache(API_PATH, nextEnvelope);
      setMessage(t("app.admin.dashboard.desktopPet.saved"));
      void loadConfig(true);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setSaving(false);
    }
  }, [envelope.data, loadConfig, t]);

  const setDesktopPetRuntimeEnabled = useCallback(async (enabled: boolean) => {
    const shell = window.v8osShell;
    if (!shell?.isShell) return;
    setRuntimeBusy(true);
    setError(null);
    try {
      setRuntimeState(await shell.setDesktopPetEnabled(enabled));
    } catch (runtimeError) {
      setError(runtimeError instanceof Error ? runtimeError.message : String(runtimeError));
    } finally {
      setRuntimeBusy(false);
    }
  }, []);

  const petScale = clampNumber(data.appearance?.petScale, 1, 0.6, 1.8);
  const floatAmplitude = clampNumber(data.appearance?.floatAmplitude, 14, 0, 40);
  const floatSpeed = clampNumber(data.appearance?.floatSpeed, 3.5, 1, 8);
  const glowIntensity = clampNumber(data.effectSpectrum?.intensity, 0.75, 0, 1);
  const runtimeUnavailable = runtimeState?.available === false;
  const linuxRuntimeUnavailable = runtimeUnavailable
    && runtimeState.reasonCode === "linux_desktop_pet_input_passthrough_unreliable";
  const runtimeStatus = !runtimeState
    ? t("app.admin.dashboard.desktopPet.runtimeUnavailable")
    : linuxRuntimeUnavailable
      ? t(runtimeState.enabled
          ? "app.admin.dashboard.desktopPet.runtimeLinuxUnavailableRunning"
          : "app.admin.dashboard.desktopPet.runtimeLinuxUnavailable")
      : t(`app.admin.dashboard.desktopPet.runtimeStates.${runtimeState.state}`);

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title={t("app.admin.dashboard.desktopPet.title")}
        description={t("app.admin.dashboard.desktopPet.description")}
        actions={
          <Button onClick={saveAll} disabled={saving || loading}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            {t("app.admin.dashboard.desktopPet.save")}
          </Button>
        }
      />

      {error ? <Card className="border-destructive/30 bg-destructive/10 text-sm text-destructive"><CardContent className="py-3">{error}</CardContent></Card> : null}
      {message ? <Card className="border-emerald-500/30 bg-emerald-500/10 text-sm text-emerald-700 dark:text-emerald-300"><CardContent className="py-3">{message}</CardContent></Card> : null}

      <ConfigCard title={t("app.admin.dashboard.desktopPet.runtimeTitle")} description={t("app.admin.dashboard.desktopPet.runtimeDescription")}>
        <div className="flex items-center justify-between gap-4 rounded-xl border bg-muted/20 px-4 py-3">
          <div className="min-w-0">
            <div className="font-medium">{t("app.admin.dashboard.desktopPet.runtimeToggle")}</div>
            <div className="mt-1 text-sm text-muted-foreground">
              {runtimeStatus}
            </div>
          </div>
          <Switch
            checked={Boolean(runtimeState?.enabled)}
            disabled={!runtimeState
              || runtimeBusy
              || runtimeState.state === "starting"
              || runtimeState.state === "stopping"
              || (runtimeUnavailable && !runtimeState.enabled)}
            onCheckedChange={(checked) => { void setDesktopPetRuntimeEnabled(checked); }}
            aria-label={t("app.admin.dashboard.desktopPet.runtimeToggle")}
          />
        </div>
      </ConfigCard>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <ConfigCard title={t("app.admin.dashboard.desktopPet.appearance")} description={t("app.admin.dashboard.desktopPet.appearanceDescription")}>
          <div className="grid gap-6 lg:grid-cols-3">
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span>{t("app.admin.dashboard.desktopPet.petScale")}</span>
                <span className="text-muted-foreground">{petScale.toFixed(2)}</span>
              </div>
              <Slider value={[petScale]} min={0.6} max={1.8} step={0.05} onValueChange={([value]) => updateAppearance({ petScale: value })} />
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span>{t("app.admin.dashboard.desktopPet.floatAmplitude")}</span>
                <span className="text-muted-foreground">{Math.round(floatAmplitude)}</span>
              </div>
              <Slider value={[floatAmplitude]} min={0} max={40} step={1} onValueChange={([value]) => updateAppearance({ floatAmplitude: value })} />
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span>{t("app.admin.dashboard.desktopPet.floatSpeed")}</span>
                <span className="text-muted-foreground">{floatSpeed.toFixed(1)}s</span>
              </div>
              <Slider value={[floatSpeed]} min={1} max={8} step={0.1} onValueChange={([value]) => updateAppearance({ floatSpeed: value })} />
            </div>
          </div>
          <div className="mt-6 grid gap-4 lg:grid-cols-3">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("app.admin.dashboard.desktopPet.effectPreset")}</label>
              <Select value={data.effectSpectrum?.preset || "soft"} onValueChange={(value) => updateEffectSpectrum({ preset: value })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="soft">{t("app.admin.dashboard.desktopPet.effectSoft")}</SelectItem>
                  <SelectItem value="energy">{t("app.admin.dashboard.desktopPet.effectEnergy")}</SelectItem>
                  <SelectItem value="focus">{t("app.admin.dashboard.desktopPet.effectFocus")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span>{t("app.admin.dashboard.desktopPet.effectIntensity")}</span>
                <span className="text-muted-foreground">{glowIntensity.toFixed(2)}</span>
              </div>
              <Slider value={[glowIntensity]} min={0} max={1} step={0.05} onValueChange={([value]) => updateEffectSpectrum({ intensity: value })} />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("app.admin.dashboard.desktopPet.customGlowColor")}</label>
              <Input value={data.effectSpectrum?.customGlowColor || "#66e3ff"} onChange={(event) => updateEffectSpectrum({ customGlowColor: event.target.value })} />
            </div>
          </div>
        </ConfigCard>

        <ConfigCard title={t("app.admin.dashboard.desktopPet.eventVoice")} description={t("app.admin.dashboard.desktopPet.eventVoiceDescription")}>
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="flex items-center justify-between rounded-xl border bg-muted/20 px-4 py-3 text-sm">
              <span>
                <span className="block font-medium">{t("app.admin.dashboard.desktopPet.enableVoice")}</span>
                <span className="text-muted-foreground">{t("app.admin.dashboard.desktopPet.enableVoiceHint")}</span>
              </span>
              <Switch checked={data.eventVoice?.enabled !== false} onCheckedChange={(checked) => updateEventVoice({ enabled: checked })} />
            </label>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("app.admin.dashboard.desktopPet.voiceMode")}</label>
              <Select value={normalizeAdminVoiceMode(data.eventVoice?.mode)} onValueChange={(value) => updateEventVoice({ mode: value })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="system_tts">{t("app.admin.dashboard.desktopPet.voiceModeSystemTts")}</SelectItem>
                  <SelectItem value="voice_tag_only">{t("app.admin.dashboard.desktopPet.voiceModeVoiceTagOnly")}</SelectItem>
                  <SelectItem value="off">{t("app.admin.dashboard.desktopPet.voiceModeOff")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("app.admin.dashboard.desktopPet.voiceRef")}</label>
              <Input value={data.eventVoice?.voiceRef || ""} placeholder={t("app.admin.dashboard.desktopPet.voiceRefPlaceholder")} onChange={(event) => updateEventVoice({ voiceRef: event.target.value })} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm">
                <Switch checked={data.eventVoice?.speakVoiceTags !== false} onCheckedChange={(checked) => updateEventVoice({ speakVoiceTags: checked })} />
                {t("app.admin.dashboard.desktopPet.speakVoiceTags")}
              </label>
              <label className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm">
                <Switch checked={data.eventVoice?.speakSupervisorReplies !== false} onCheckedChange={(checked) => updateEventVoice({ speakSupervisorReplies: checked })} />
                {t("app.admin.dashboard.desktopPet.speakSupervisorReplies")}
              </label>
            </div>
          </div>
        </ConfigCard>
      </div>

      <ConfigCard title={t("app.admin.dashboard.desktopPet.eventResponse")} description={t("app.admin.dashboard.desktopPet.eventResponseDescription")}>
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={addCustomEventRow} disabled={eventRows.length >= DESKTOP_PET_EVENT_CATALOG.length}>
            <Plus className="mr-2 h-4 w-4" />
            {t("app.admin.dashboard.desktopPet.addCustomEvent")}
          </Button>
        </div>
        <div className="overflow-x-auto">
          <div className="min-w-[980px] space-y-2">
            <div className="grid grid-cols-[minmax(220px,1.25fr)_minmax(220px,1.2fr)_150px_150px_90px_44px] gap-2 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              <span>{t("app.admin.dashboard.desktopPet.listenEvent")}</span>
              <span>{t("app.admin.dashboard.desktopPet.spokenPhrase")}</span>
              <span>{t("app.admin.dashboard.desktopPet.action")}</span>
              <span>{t("app.admin.dashboard.desktopPet.spectrumName")}</span>
              <span>{t("app.admin.dashboard.desktopPet.speak")}</span>
              <span />
            </div>
            {eventRows.map((row) => (
              <div key={row.key} className="grid grid-cols-[minmax(220px,1.25fr)_minmax(220px,1.2fr)_150px_150px_90px_44px] items-center gap-2 rounded-xl border bg-background px-2 py-2">
                <Select value={row.event} onValueChange={(value) => updateEventRow(row.key, { event: value as DesktopPetEventId })}>
                  <SelectTrigger aria-label={t("app.admin.dashboard.desktopPet.listenEvent")}>
                    <SelectValue>{desktopPetEventLabel(row.event, locale)}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {DESKTOP_PET_EVENT_CATALOG.map((eventOption) => (
                      <SelectItem
                        key={eventOption.id}
                        value={eventOption.id}
                        disabled={eventOption.id !== row.event && eventRows.some((candidate) => candidate.event === eventOption.id)}
                      >
                        {desktopPetEventLabel(eventOption.id, locale)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input value={row.phrase} onChange={(event) => updateEventRow(row.key, { phrase: event.target.value })} placeholder={t("app.admin.dashboard.desktopPet.spokenPhrasePlaceholder")} />
                <Select value={row.emotion || "idle"} onValueChange={(value) => updateEventRow(row.key, { emotion: value })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ACTION_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {t(option.labelKey)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={row.spectrum || "cyan"} onValueChange={(value) => updateEventRow(row.key, { spectrum: value })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SPECTRUM_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {t(option.labelKey)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className="flex items-center justify-center">
                  <Switch checked={row.speak} onCheckedChange={(checked) => updateEventRow(row.key, { speak: checked })} />
                </label>
                <Button variant="ghost" size="icon" onClick={() => deleteEventRow(row.key)} aria-label={t("app.admin.dashboard.desktopPet.deleteEvent")}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
            {!eventRows.length ? (
              <div className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
                {t("app.admin.dashboard.desktopPet.noEventRules")}
              </div>
            ) : null}
          </div>
        </div>
      </ConfigCard>

      <ConfigCard title={t("app.admin.dashboard.desktopPet.visualAttachment")} description={t("app.admin.dashboard.desktopPet.visualAttachmentDescription")}>
        <div className="grid gap-4 lg:grid-cols-2">
          <label className="flex items-center justify-between rounded-xl border bg-muted/20 px-4 py-3 text-sm">
            <span>
              <span className="block font-medium">{t("app.admin.dashboard.desktopPet.cameraAttachment")}</span>
              <span className="text-muted-foreground">{t("app.admin.dashboard.desktopPet.cameraAttachmentHint")}</span>
            </span>
            <Switch
              checked={attachmentCapture.cameraEnabled}
              onCheckedChange={(checked) =>
                updateAttachmentCapture({
                  cameraEnabled: checked,
                  includeDesktopScreenshot: checked ? attachmentCapture.includeDesktopScreenshot : false,
                })
              }
            />
          </label>
          {attachmentCapture.cameraEnabled ? (
            <label className="flex items-center justify-between rounded-xl border bg-muted/20 px-4 py-3 text-sm">
              <span>
                <span className="block font-medium">{t("app.admin.dashboard.desktopPet.desktopSnapshot")}</span>
                <span className="text-muted-foreground">{t("app.admin.dashboard.desktopPet.desktopSnapshotHint")}</span>
              </span>
              <Switch
                checked={attachmentCapture.includeDesktopScreenshot}
                onCheckedChange={(checked) =>
                  updateAttachmentCapture({
                    includeDesktopScreenshot: checked,
                    layout: "desktop_pip_camera",
                  })
                }
              />
            </label>
          ) : null}
        </div>
        {attachmentCapture.cameraEnabled ? (
          <div className="mt-4 rounded-xl border border-dashed bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
            {attachmentCapture.includeDesktopScreenshot ? t("app.admin.dashboard.desktopPet.desktopPipCamera") : t("app.admin.dashboard.desktopPet.cameraOnlySnapshot")}
          </div>
        ) : null}
      </ConfigCard>

      <SourceMetaRow
        source={envelope.source || "config.json#desktopPet"}
        savePath={envelope.savePath || envelope.sourcePath || "~/.v8-agent-os/config.json#desktopPet"}
        reloadRequired={Boolean(envelope.reloadRequired)}
      />
    </div>
  );
}
