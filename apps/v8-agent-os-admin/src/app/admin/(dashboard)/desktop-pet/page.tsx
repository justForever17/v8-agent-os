"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { useLocale } from "@/components/providers/LocaleProvider";

type DesktopPetActionRule = {
  id?: string;
  match?: string;
  emotion?: string;
  spectrum?: string;
};

type DesktopPetVoiceRule = {
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

type EventPreset = {
  key: string;
  labelKey: string;
  match: string;
  emotion: string;
  spectrum: string;
  speak: boolean;
  phraseKey?: string;
};

type EventRuleRow = {
  key: string;
  id?: string;
  label: string;
  match: string;
  phrase: string;
  emotion: string;
  spectrum: string;
  speak: boolean;
  custom?: boolean;
};

const API_PATH = "/api/admin/config/desktop-pet";

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

const EVENT_PRESETS: EventPreset[] = [
  {
    key: "idle",
    labelKey: "app.admin.dashboard.desktopPet.events.idle",
    match: "idle|settled|\u9759\u9ed8|\u7a7a\u95f2",
    emotion: "idle",
    spectrum: "cyan",
    speak: false,
  },
  {
    key: "thinking",
    labelKey: "app.admin.dashboard.desktopPet.events.thinking",
    match: "reasoning|thinking|\u601d\u8003",
    emotion: "thinking",
    spectrum: "violet",
    speak: false,
  },
  {
    key: "tool",
    labelKey: "app.admin.dashboard.desktopPet.events.tool",
    match: "tool_start|tool_result|\u5de5\u5177",
    emotion: "tool_calling",
    spectrum: "blue",
    speak: false,
  },
  {
    key: "waiting",
    labelKey: "app.admin.dashboard.desktopPet.events.waiting",
    match: "ask_user|approval|\u7b49\u5f85\u7528\u6237|\u786e\u8ba4",
    emotion: "curious",
    spectrum: "golden_amber",
    speak: true,
    phraseKey: "app.admin.dashboard.desktopPet.defaultPhrases.waiting",
  },
  {
    key: "done",
    labelKey: "app.admin.dashboard.desktopPet.events.done",
    match: "complete|completed|done|\u5b8c\u6210|artifact_ready",
    emotion: "happy",
    spectrum: "emerald_green",
    speak: true,
    phraseKey: "app.admin.dashboard.desktopPet.defaultPhrases.done",
  },
  {
    key: "failed",
    labelKey: "app.admin.dashboard.desktopPet.events.failed",
    match: "error|failed|\u5931\u8d25|\u5f02\u5e38",
    emotion: "worried",
    spectrum: "crimson_red",
    speak: true,
    phraseKey: "app.admin.dashboard.desktopPet.defaultPhrases.failed",
  },
  {
    key: "audio",
    labelKey: "app.admin.dashboard.desktopPet.events.audio",
    match: "audio|voice|\u8bed\u97f3",
    emotion: "listening",
    spectrum: "cyan",
    speak: false,
  },
  {
    key: "artifact",
    labelKey: "app.admin.dashboard.desktopPet.events.artifact",
    match: "artifact|\u4ea7\u7269",
    emotion: "happy",
    spectrum: "emerald_green",
    speak: true,
    phraseKey: "app.admin.dashboard.desktopPet.defaultPhrases.artifact",
  },
];

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

function buildEventRows(config: DesktopPetConfig, t: (key: string) => string): EventRuleRow[] {
  const actions = Array.isArray(config.actionTable) ? config.actionTable : [];
  const voices = Array.isArray(config.eventVoice?.customRules) ? config.eventVoice?.customRules ?? [] : [];
  const usedActions = new Set<number>();
  const usedVoices = new Set<number>();

  const rows: EventRuleRow[] = EVENT_PRESETS.map((preset) => {
    const actionIndex = actions.findIndex((rule) => rule.id === preset.key || normalizeText(rule.match) === preset.match);
    const voiceIndex = voices.findIndex((rule) => normalizeText(rule.match) === preset.match);
    const action = actionIndex >= 0 ? actions[actionIndex] : undefined;
    const voice = voiceIndex >= 0 ? voices[voiceIndex] : undefined;
    if (actionIndex >= 0) usedActions.add(actionIndex);
    if (voiceIndex >= 0) usedVoices.add(voiceIndex);
    return {
      key: preset.key,
      id: preset.key,
      label: t(preset.labelKey),
      match: normalizeText(voice?.match || action?.match || preset.match),
      phrase: normalizeText(voice?.phrase || (preset.phraseKey ? t(preset.phraseKey) : "")),
      emotion: normalizeText(voice?.emotion || action?.emotion || preset.emotion),
      spectrum: normalizeText(action?.spectrum || preset.spectrum),
      speak: voice ? voice.speak !== false : preset.speak,
    };
  });

  const customByMatch = new Map<string, EventRuleRow>();
  actions.forEach((action, index) => {
    if (usedActions.has(index)) return;
    const match = normalizeText(action.match).trim();
    const id = normalizeText(action.id).trim() || `custom_action_${index + 1}`;
    const key = `custom:${id}:${match || index}`;
    customByMatch.set(key, {
      key,
      id,
      label: t("app.admin.dashboard.desktopPet.customEvent"),
      match,
      phrase: "",
      emotion: normalizeText(action.emotion || "idle"),
      spectrum: normalizeText(action.spectrum || "cyan"),
      speak: false,
      custom: true,
    });
  });
  voices.forEach((voice, index) => {
    if (usedVoices.has(index)) return;
    const match = normalizeText(voice.match).trim();
    const existingKey = Array.from(customByMatch.keys()).find((key) => customByMatch.get(key)?.match === match);
    const key = existingKey || `custom:voice:${match || index}`;
    const current = customByMatch.get(key);
    customByMatch.set(key, {
      key,
      id: current?.id || key.replace(/^custom:/, "").replace(/[^a-zA-Z0-9_-]/g, "_"),
      label: t("app.admin.dashboard.desktopPet.customEvent"),
      match,
      phrase: normalizeText(voice.phrase),
      emotion: normalizeText(voice.emotion || current?.emotion || "idle"),
      spectrum: normalizeText(current?.spectrum || "cyan"),
      speak: voice.speak !== false,
      custom: true,
    });
  });

  return rows.concat(Array.from(customByMatch.values()));
}

function rowsToConfig(rows: EventRuleRow[], config: DesktopPetConfig): DesktopPetConfig {
  const normalized = rows
    .map((row) => ({
      ...row,
      match: row.match.trim(),
      phrase: row.phrase.trim(),
      emotion: row.emotion.trim() || "idle",
      spectrum: row.spectrum.trim() || "cyan",
    }))
    .filter((row) => row.match.length > 0);

  return {
    ...config,
    actionTable: normalized.map((row) => ({
      id: row.id || row.key,
      match: row.match,
      emotion: row.emotion,
      spectrum: row.spectrum,
    })),
    eventVoice: {
      ...(config.eventVoice || {}),
      customRules: normalized.map((row) => ({
        match: row.match,
        phrase: row.phrase,
        emotion: row.emotion,
        speak: row.speak,
      })),
    },
  };
}

export default function DesktopPetSettingsPage() {
  const { t } = useLocale();
  const [envelope, setEnvelope] = useState<ConfigEnvelope>(DEFAULT_ENVELOPE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const data = useMemo(() => envelope.data || {}, [envelope.data]);
  const eventRows = useMemo(() => buildEventRows(data, t), [data, t]);
  const attachmentCapture = {
    cameraEnabled: data.attachmentCapture?.cameraEnabled === true,
    includeDesktopScreenshot: data.attachmentCapture?.includeDesktopScreenshot === true,
    layout: data.attachmentCapture?.layout || "desktop_pip_camera",
  };

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(API_PATH, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as ConfigEnvelope;
      setEnvelope({
        ...DEFAULT_ENVELOPE,
        ...payload,
        data: {
          ...DEFAULT_ENVELOPE.data,
          ...(payload.data || {}),
          appearance: {
            ...DEFAULT_ENVELOPE.data.appearance,
            ...(payload.data?.appearance || {}),
          },
          eventVoice: {
            ...DEFAULT_ENVELOPE.data.eventVoice,
            ...(payload.data?.eventVoice || {}),
          },
          effectSpectrum: {
            ...DEFAULT_ENVELOPE.data.effectSpectrum,
            ...(payload.data?.effectSpectrum || {}),
          },
          attachmentCapture: {
            ...DEFAULT_ENVELOPE.data.attachmentCapture,
            ...(payload.data?.attachmentCapture || {}),
          },
        },
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

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
      const key = `custom_${Date.now()}`;
      return rowsToConfig(
        rows.concat({
          key: `custom:${key}`,
          id: key,
          label: t("app.admin.dashboard.desktopPet.customEvent"),
          match: "",
          phrase: "",
          emotion: "idle",
          spectrum: "cyan",
          speak: false,
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
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: envelope.data || {} }),
      });
      const payload = (await response.json()) as { ok?: boolean; error?: string };
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      setMessage(t("app.admin.dashboard.desktopPet.saved"));
      await loadConfig();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setSaving(false);
    }
  }, [envelope.data, loadConfig, t]);

  const petScale = clampNumber(data.appearance?.petScale, 1, 0.6, 1.8);
  const floatAmplitude = clampNumber(data.appearance?.floatAmplitude, 14, 0, 40);
  const floatSpeed = clampNumber(data.appearance?.floatSpeed, 3.5, 1, 8);
  const glowIntensity = clampNumber(data.effectSpectrum?.intensity, 0.75, 0, 1);

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

      {error ? <Card className="border-red-200 bg-red-50 text-sm text-red-700"><CardContent className="py-3">{error}</CardContent></Card> : null}
      {message ? <Card className="border-emerald-200 bg-emerald-50 text-sm text-emerald-700"><CardContent className="py-3">{message}</CardContent></Card> : null}

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
              <Checkbox checked={data.eventVoice?.enabled !== false} onCheckedChange={(checked) => updateEventVoice({ enabled: checked === true })} />
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
                <Checkbox checked={data.eventVoice?.speakVoiceTags !== false} onCheckedChange={(checked) => updateEventVoice({ speakVoiceTags: checked === true })} />
                {t("app.admin.dashboard.desktopPet.speakVoiceTags")}
              </label>
              <label className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm">
                <Checkbox checked={data.eventVoice?.speakSupervisorReplies !== false} onCheckedChange={(checked) => updateEventVoice({ speakSupervisorReplies: checked === true })} />
                {t("app.admin.dashboard.desktopPet.speakSupervisorReplies")}
              </label>
            </div>
          </div>
        </ConfigCard>
      </div>

      <ConfigCard title={t("app.admin.dashboard.desktopPet.eventResponse")} description={t("app.admin.dashboard.desktopPet.eventResponseDescription")}>
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={addCustomEventRow}>
            <Plus className="mr-2 h-4 w-4" />
            {t("app.admin.dashboard.desktopPet.addCustomEvent")}
          </Button>
        </div>
        <div className="overflow-x-auto">
          <div className="min-w-[980px] space-y-2">
            <div className="grid grid-cols-[130px_1.1fr_1.2fr_150px_150px_90px_44px] gap-2 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              <span>{t("app.admin.dashboard.desktopPet.eventName")}</span>
              <span>{t("app.admin.dashboard.desktopPet.triggerWords")}</span>
              <span>{t("app.admin.dashboard.desktopPet.spokenPhrase")}</span>
              <span>{t("app.admin.dashboard.desktopPet.action")}</span>
              <span>{t("app.admin.dashboard.desktopPet.spectrumName")}</span>
              <span>{t("app.admin.dashboard.desktopPet.speak")}</span>
              <span />
            </div>
            {eventRows.map((row) => (
              <div key={row.key} className="grid grid-cols-[130px_1.1fr_1.2fr_150px_150px_90px_44px] items-center gap-2 rounded-xl border bg-background px-2 py-2">
                <div className="truncate text-sm font-medium" title={row.label}>
                  {row.label}
                </div>
                <Input value={row.match} onChange={(event) => updateEventRow(row.key, { match: event.target.value })} placeholder={t("app.admin.dashboard.desktopPet.triggerWordsPlaceholder")} />
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
                  <Checkbox checked={row.speak} onCheckedChange={(checked) => updateEventRow(row.key, { speak: checked === true })} />
                </label>
                {row.custom ? (
                  <Button variant="ghost" size="icon" onClick={() => deleteEventRow(row.key)} aria-label={t("app.admin.dashboard.desktopPet.deleteEvent")}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                ) : (
                  <span />
                )}
              </div>
            ))}
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
            <Checkbox
              checked={attachmentCapture.cameraEnabled}
              onCheckedChange={(checked) =>
                updateAttachmentCapture({
                  cameraEnabled: checked === true,
                  includeDesktopScreenshot: checked === true ? attachmentCapture.includeDesktopScreenshot : false,
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
              <Checkbox
                checked={attachmentCapture.includeDesktopScreenshot}
                onCheckedChange={(checked) =>
                  updateAttachmentCapture({
                    includeDesktopScreenshot: checked === true,
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
