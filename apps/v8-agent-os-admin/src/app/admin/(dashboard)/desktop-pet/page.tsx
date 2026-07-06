"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

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
};

function stringifyActionTable(value: DesktopPetActionRule[] | undefined) {
    return JSON.stringify(Array.isArray(value) ? value : [], null, 2);
}

function stringifyVoiceRules(value: DesktopPetVoiceRule[] | undefined) {
    return JSON.stringify(Array.isArray(value) ? value : [], null, 2);
}

function parseActionTable(value: string, arrayError: string) {
    const parsed = JSON.parse(value || "[]");
    if (!Array.isArray(parsed)) {
        throw new Error(arrayError);
    }
    return parsed.map((item) => ({
        id: String(item?.id || "").trim(),
        match: String(item?.match || "").trim(),
        emotion: String(item?.emotion || "").trim(),
        spectrum: String(item?.spectrum || "").trim(),
    }));
}

function parseVoiceRules(value: string, arrayError: string) {
    const parsed = JSON.parse(value || "[]");
    if (!Array.isArray(parsed)) {
        throw new Error(arrayError);
    }
    return parsed.map((item) => ({
        match: String(item?.match || "").trim(),
        phrase: String(item?.phrase || "").trim(),
        emotion: String(item?.emotion || "").trim(),
        speak: item?.speak !== false,
    }));
}

function clampNumber(value: number, min: number, max: number) {
    if (!Number.isFinite(value)) return min;
    return Math.min(max, Math.max(min, value));
}

export default function DesktopPetPage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<DesktopPetConfig> | null>(null);
    const [actionTableText, setActionTableText] = useState("[]");
    const [voiceRulesText, setVoiceRulesText] = useState("[]");
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState("");

    const loadData = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const next = await fetchConfigDomain<DesktopPetConfig>("desktop-pet");
            setEnvelope(next);
            setActionTableText(stringifyActionTable(next.data.actionTable));
            setVoiceRulesText(stringifyVoiceRules(next.data.eventVoice?.customRules));
        } catch (loadError) {
            setError(loadError instanceof Error ? loadError.message : t("app.admin.dashboard.desktopPet.errorLoad"));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        void loadData();
    }, [loadData]);

    const data = envelope?.data || {};
    const appearance = data.appearance || {};
    const eventVoice = data.eventVoice || {};
    const effectSpectrum = data.effectSpectrum || {};

    const summary = useMemo(() => {
        const actionCount = Array.isArray(data.actionTable) ? data.actionTable.length : 0;
        const voiceRuleCount = Array.isArray(data.eventVoice?.customRules) ? data.eventVoice.customRules.length : 0;
        return [
            { label: t("app.admin.dashboard.desktopPet.appearance"), value: t("app.admin.dashboard.desktopPet.scaleValue", { value: Number(appearance.petScale ?? 0.7).toFixed(2) }) },
            { label: t("app.admin.dashboard.desktopPet.eventVoice"), value: eventVoice.enabled === false ? t("app.admin.dashboard.desktopPet.off") : t("app.admin.dashboard.desktopPet.on") },
            { label: t("app.admin.dashboard.desktopPet.actionRules"), value: t("app.admin.dashboard.desktopPet.ruleCount", { count: actionCount }) },
            { label: t("app.admin.dashboard.desktopPet.eventVoiceRules"), value: t("app.admin.dashboard.desktopPet.ruleCount", { count: voiceRuleCount }) },
            { label: t("app.admin.dashboard.desktopPet.effectSpectrum"), value: effectSpectrum.preset || "soft" },
        ];
    }, [appearance.petScale, data.actionTable, data.eventVoice?.customRules, effectSpectrum.preset, eventVoice.enabled, t]);

    const updateData = (recipe: (current: DesktopPetConfig) => DesktopPetConfig) => {
        setEnvelope((current) => {
            if (!current) return current;
            return { ...current, data: recipe(current.data || {}) };
        });
    };

    const saveAll = async () => {
        if (!envelope) return;
        setSaving(true);
        setSaved(false);
        setError("");
        try {
            const parsedActionTable = parseActionTable(actionTableText, t("app.admin.dashboard.desktopPet.actionTableJsonArrayError"));
            const parsedVoiceRules = parseVoiceRules(voiceRulesText, t("app.admin.dashboard.desktopPet.eventVoiceRulesJsonArrayError"));
            const next = await saveConfigDomain<DesktopPetConfig>("desktop-pet", {
                data: {
                    ...envelope.data,
                    eventVoice: {
                        ...(envelope.data.eventVoice || {}),
                        customRules: parsedVoiceRules,
                    },
                    actionTable: parsedActionTable,
                },
            });
            setEnvelope(next);
            setActionTableText(stringifyActionTable(next.data.actionTable));
            setVoiceRulesText(stringifyVoiceRules(next.data.eventVoice?.customRules));
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } catch (saveError) {
            setError(saveError instanceof Error ? saveError.message : t("app.admin.dashboard.desktopPet.errorSave"));
        } finally {
            setSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={t("app.admin.dashboard.desktopPet.title")}
                description={t("app.admin.dashboard.desktopPet.description")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label={t("app.admin.dashboard.desktopPet.title")} />
                        <Button onClick={saveAll} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.desktopPet.save")}
                        </Button>
                    </div>
                }
            />

            {error ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                    {error}
                </div>
            ) : null}

            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
                {summary.map((item) => (
                    <div key={item.label} className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                        <div className="text-xs font-medium text-slate-500">{item.label}</div>
                        <div className="mt-1 text-lg font-semibold text-slate-900">{item.value}</div>
                    </div>
                ))}
            </div>

            <ConfigCard title={t("app.admin.dashboard.desktopPet.appearance")} description={t("app.admin.dashboard.desktopPet.appearanceDescription")}>
                <div className="grid gap-5 md:grid-cols-3">
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.petScale", { value: Number(appearance.petScale ?? 0.7).toFixed(2) })}</Label>
                        <Input
                            type="range"
                            min={0.4}
                            max={3}
                            step={0.05}
                            value={Number(appearance.petScale ?? 0.7)}
                            onChange={(event) =>
                                updateData((current) => ({
                                    ...current,
                                    appearance: {
                                        ...(current.appearance || {}),
                                        petScale: clampNumber(Number(event.target.value), 0.4, 3),
                                    },
                                }))
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.floatAmplitude", { value: Number(appearance.floatAmplitude ?? 8).toFixed(0) })}</Label>
                        <Input
                            type="range"
                            min={0}
                            max={24}
                            step={1}
                            value={Number(appearance.floatAmplitude ?? 8)}
                            onChange={(event) =>
                                updateData((current) => ({
                                    ...current,
                                    appearance: {
                                        ...(current.appearance || {}),
                                        floatAmplitude: clampNumber(Number(event.target.value), 0, 24),
                                    },
                                }))
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.floatSpeed", { value: Number(appearance.floatSpeed ?? 1).toFixed(1) })}</Label>
                        <Input
                            type="range"
                            min={0.1}
                            max={3}
                            step={0.1}
                            value={Number(appearance.floatSpeed ?? 1)}
                            onChange={(event) =>
                                updateData((current) => ({
                                    ...current,
                                    appearance: {
                                        ...(current.appearance || {}),
                                        floatSpeed: clampNumber(Number(event.target.value), 0.1, 3),
                                    },
                                }))
                            }
                        />
                    </div>
                </div>
            </ConfigCard>

            <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
                <ConfigCard title={t("app.admin.dashboard.desktopPet.eventVoice")} description={t("app.admin.dashboard.desktopPet.eventVoiceDescription")}>
                    <div className="space-y-4">
                        <label className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <span>
                                <span className="block text-sm font-semibold text-slate-900">{t("app.admin.dashboard.desktopPet.enableVoice")}</span>
                                <span className="text-xs text-slate-500">{t("app.admin.dashboard.desktopPet.enableVoiceHint")}</span>
                            </span>
                            <input
                                type="checkbox"
                                className="h-5 w-5 accent-slate-900"
                                checked={eventVoice.enabled !== false}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        eventVoice: { ...(current.eventVoice || {}), enabled: event.target.checked },
                                    }))
                                }
                            />
                        </label>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.desktopPet.voiceMode")}</Label>
                                <Select
                                    value={eventVoice.mode || "system_tts"}
                                    onValueChange={(value) =>
                                        updateData((current) => ({
                                            ...current,
                                            eventVoice: { ...(current.eventVoice || {}), mode: value },
                                        }))
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="system_tts">{t("app.admin.dashboard.desktopPet.voiceModeSystemTts")}</SelectItem>
                                        <SelectItem value="voice_tag">{t("app.admin.dashboard.desktopPet.voiceModeVoiceTag")}</SelectItem>
                                        <SelectItem value="muted">{t("app.admin.dashboard.desktopPet.voiceModeMuted")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.desktopPet.voiceRef")}</Label>
                                <Input
                                    value={eventVoice.voiceRef || ""}
                                    placeholder={t("app.admin.dashboard.desktopPet.voiceRefPlaceholder")}
                                    onChange={(event) =>
                                        updateData((current) => ({
                                            ...current,
                                            eventVoice: { ...(current.eventVoice || {}), voiceRef: event.target.value },
                                        }))
                                    }
                                />
                            </div>
                        </div>

                        <div className="grid gap-3 md:grid-cols-2">
                            <label className="flex items-center gap-2 text-sm text-slate-700">
                                <input
                                    type="checkbox"
                                    className="h-4 w-4 accent-slate-900"
                                    checked={eventVoice.speakVoiceTags !== false}
                                    onChange={(event) =>
                                        updateData((current) => ({
                                            ...current,
                                            eventVoice: { ...(current.eventVoice || {}), speakVoiceTags: event.target.checked },
                                        }))
                                    }
                                />
                                {t("app.admin.dashboard.desktopPet.speakVoiceTags")}
                            </label>
                            <label className="flex items-center gap-2 text-sm text-slate-700">
                                <input
                                    type="checkbox"
                                    className="h-4 w-4 accent-slate-900"
                                    checked={eventVoice.speakSupervisorReplies !== false}
                                    onChange={(event) =>
                                        updateData((current) => ({
                                            ...current,
                                            eventVoice: { ...(current.eventVoice || {}), speakSupervisorReplies: event.target.checked },
                                        }))
                                    }
                                />
                                {t("app.admin.dashboard.desktopPet.speakSupervisorReplies")}
                            </label>
                        </div>

                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.desktopPet.eventVoiceRules")}</Label>
                            <Textarea
                                value={voiceRulesText}
                                onChange={(event) => setVoiceRulesText(event.target.value)}
                                className="min-h-[180px] font-mono text-xs"
                                spellCheck={false}
                            />
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title={t("app.admin.dashboard.desktopPet.actionTable")} description={t("app.admin.dashboard.desktopPet.actionTableDescription")}>
                    <div className="space-y-3">
                        <Textarea
                            value={actionTableText}
                            onChange={(event) => setActionTableText(event.target.value)}
                            className="min-h-[260px] font-mono text-xs"
                            spellCheck={false}
                        />
                    </div>
                </ConfigCard>
            </div>

            <ConfigCard title={t("app.admin.dashboard.desktopPet.effectSpectrum")} description={t("app.admin.dashboard.desktopPet.effectSpectrumDescription")}>
                <div className="grid gap-4 md:grid-cols-3">
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.preset")}</Label>
                        <Select
                            value={effectSpectrum.preset || "soft"}
                            onValueChange={(value) =>
                                updateData((current) => ({
                                    ...current,
                                    effectSpectrum: { ...(current.effectSpectrum || {}), preset: value },
                                }))
                            }
                        >
                            <SelectTrigger>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="soft">{t("app.admin.dashboard.desktopPet.presetSoft")}</SelectItem>
                                <SelectItem value="focus">{t("app.admin.dashboard.desktopPet.presetFocus")}</SelectItem>
                                <SelectItem value="vivid">{t("app.admin.dashboard.desktopPet.presetVivid")}</SelectItem>
                                <SelectItem value="custom">{t("app.admin.dashboard.desktopPet.presetCustom")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.intensity", { value: Number(effectSpectrum.intensity ?? 0.75).toFixed(2) })}</Label>
                        <Input
                            type="range"
                            min={0}
                            max={1}
                            step={0.05}
                            value={Number(effectSpectrum.intensity ?? 0.75)}
                            onChange={(event) =>
                                updateData((current) => ({
                                    ...current,
                                    effectSpectrum: {
                                        ...(current.effectSpectrum || {}),
                                        intensity: Number(event.target.value || 0.75),
                                    },
                                }))
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktopPet.customColor")}</Label>
                        <Input
                            value={effectSpectrum.customGlowColor || ""}
                            placeholder="#66e3ff"
                            onChange={(event) =>
                                updateData((current) => ({
                                    ...current,
                                    effectSpectrum: { ...(current.effectSpectrum || {}), customGlowColor: event.target.value },
                                }))
                            }
                        />
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
