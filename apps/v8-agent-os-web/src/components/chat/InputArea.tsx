"use client";
/* eslint-disable @next/next/no-img-element */

import * as React from "react";
import type { PluginReferenceSummary } from "@v8/session-realtime";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Paperclip, Send, Mic, Loader2, Square, X, PlayCircle, AlertCircle, CheckCircle2, Info, Command, AtSign, Gauge, Orbit, CornerDownRight, Shield, ShieldAlert, ShieldCheck } from "lucide-react";
import { ChangeEvent, FormEvent } from "react";
import { MediaViewerLightbox, MediaItem } from "./MediaViewerLightbox";
import { useT } from "@/components/providers/LocaleProvider";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// --- Helper Component: VideoThumbnail ---
function VideoThumbnail({ url, uploading, onRemove, onClick }: { url: string, uploading: boolean, onRemove: () => void, onClick: () => void }) {
    const [durationStr, setDurationStr] = React.useState<string | null>(null);

    React.useEffect(() => {
        const video = document.createElement('video');
        video.src = url;
        video.preload = 'metadata';
        
        video.onloadedmetadata = () => {
            const duration = video.duration;
            if (isFinite(duration) && duration > 0) {
                const m = Math.floor(duration / 60).toString().padStart(2, '0');
                const s = Math.floor(duration % 60).toString().padStart(2, '0');
                setDurationStr(`${m}:${s}`);
            }
        };

        return () => {
            video.onloadedmetadata = null;
            video.src = '';
        };
    }, [url]);

    return (
        <div 
            className="relative w-16 h-16 rounded-lg overflow-hidden shrink-0 group border border-white/20 shadow-sm animate-in zoom-in-50 duration-200 cursor-pointer"
            onClick={onClick}
        >
            <video src={url} className="w-full h-full object-cover" muted playsInline />
            
            {/* Play overlay for video */}
            <div className="absolute inset-0 bg-black/20 group-hover:bg-black/40 transition-colors flex items-center justify-center">
                <PlayCircle className="w-6 h-6 text-white/80 opacity-0 group-hover:opacity-100 transition-opacity drop-shadow-md" />
            </div>

            {/* Duration Badge */}
            {durationStr && !uploading && (
                <div className="absolute bottom-1 right-1 px-1 rounded bg-black/70 text-white text-[9px] font-medium tracking-wide">
                    {durationStr}
                </div>
            )}

            {uploading && (
                <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
                    <Loader2 className="w-5 h-5 animate-spin text-white" />
                </div>
            )}

            <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onRemove(); }}
                className="absolute top-0 right-0 p-1 bg-black/50 text-white rounded-bl-lg opacity-0 group-hover:opacity-100 transition-all hover:bg-black/70 z-10"
            >
                <X className="w-3 h-3" />
            </button>
        </div>
    );
}

function mergeFloat32Chunks(chunks: Float32Array[]): Float32Array {
    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
    }
    return merged;
}

function downsampleTo16k(buffer: Float32Array, inputSampleRate: number): Float32Array {
    const outputSampleRate = 16000;
    if (inputSampleRate === outputSampleRate) {
        return buffer;
    }

    const sampleRateRatio = inputSampleRate / outputSampleRate;
    const newLength = Math.round(buffer.length / sampleRateRatio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;

    while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
        let accum = 0;
        let count = 0;

        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
            accum += buffer[i];
            count += 1;
        }

        result[offsetResult] = count > 0 ? accum / count : 0;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
    }

    return result;
}

function encodeWavBlob(samples: Float32Array, sampleRate: number): Blob {
    const bytesPerSample = 2;
    const blockAlign = bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset: number, value: string) => {
        for (let i = 0; i < value.length; i += 1) {
            view.setUint8(offset + i, value.charCodeAt(i));
        }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
    }

    return new Blob([buffer], { type: "audio/wav" });
}


interface InputAreaProps {
    input: string;
    handleInputChange: (e: ChangeEvent<HTMLTextAreaElement>) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    handleSubmit: (e: FormEvent<HTMLFormElement>, options?: { data?: any }) => void | boolean | Promise<void | boolean>;
    onVoiceTranscript?: (text: string) => void;
    onVoiceAudioMessage?: (data: VoiceAudioMessageData) => void | Promise<void>;
    isLoading: boolean;
    onStop?: () => void;
    selectedAgentName?: string;
    shellClassName?: string;
    reasoningEffortControl?: ReasoningEffortControl | null;
    contextSessionRefs?: ContextSessionReference[];
    onRemoveContextSessionRef?: (sessionId: string) => void;
    contextUsagePercent?: number | null;
}

interface ContextSessionReference {
    sessionId: string;
    source: "history_menu";
}

type ReasoningEffortLevel = "auto" | "low" | "medium" | "high";
type SafetyApprovalMode = "manual" | "reduced" | "minimal";
type SpecCommandAction = "new" | "continue" | "list" | "approve" | "clarify" | "analyze" | "annex";
const SAFETY_APPROVAL_MODE_STORAGE_KEY = "v8-web-safety-approval-mode";

interface ReasoningEffortControl {
    visible?: boolean;
    supported?: boolean;
    levels?: string[];
    defaultLevel?: string;
    modelRef?: string;
}

interface VoiceAudioMessageData {
    fileUrls: string[];
    attachments: Array<Record<string, unknown>>;
    safetyApprovalMode?: SafetyApprovalMode;
}

interface AudioInputStatusPayload {
    route?: string;
    stt?: { usable?: boolean; reason?: string };
    visionAudio?: { usable?: boolean; modelId?: string; reason?: string };
    error?: string;
}

type InlineNoticeTone = "info" | "success" | "error";

interface InlineNotice {
    tone: InlineNoticeTone;
    message: string;
}

interface CommandPresetSummary {
    name: string;
    filename?: string;
    summary?: string;
    path?: string;
    contentHash?: string;
    specCommandAction?: SpecCommandAction;
    readOnlyKind?: "context_usage";
    usagePercent?: number;
}

function ContextUsageRing({ percent }: { percent: number }) {
    const radius = 8;
    const circumference = Math.PI * 2 * radius;
    const dash = circumference * Math.max(0, Math.min(100, percent)) / 100;
    return (
        <svg viewBox="0 0 20 20" className="mt-0.5 h-5 w-5 shrink-0 -rotate-90" aria-hidden="true">
            <circle cx="10" cy="10" r={radius} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-muted-foreground/25" />
            <circle cx="10" cy="10" r={radius} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeDasharray={`${dash} ${circumference - dash}`} className="text-primary" />
        </svg>
    );
}

interface SkillReferenceSummary {
    name: string;
    description?: string;
    path?: string;
}

interface SubagentFamilySummary {
    familyId: string;
    displayName?: string;
    aliases?: string[];
    description?: string;
    memberCount?: number;
}

type MentionPickerItem =
    | { kind: "skill"; key: string; skill: SkillReferenceSummary }
    | { kind: "subagent_family"; key: string; family: SubagentFamilySummary }
    | { kind: "plugin"; key: string; plugin: PluginReferenceSummary };

function isSkillReferenceSummaryCandidate(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isSubagentFamilyCandidate(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isPluginCandidate(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function InputArea({
    input,
    handleInputChange,
    handleSubmit,
    onVoiceTranscript,
    onVoiceAudioMessage,
    isLoading,
    onStop,
    selectedAgentName,
    shellClassName,
    reasoningEffortControl,
    contextSessionRefs = [],
    onRemoveContextSessionRef,
    contextUsagePercent = null,
}: InputAreaProps) {
    const t = useT();
    const [commandPresets, setCommandPresets] = React.useState<CommandPresetSummary[]>([]);
    const [commandsLoaded, setCommandsLoaded] = React.useState(false);
    const [commandsLoading, setCommandsLoading] = React.useState(false);
    const [selectedCommandPreset, setSelectedCommandPreset] = React.useState<CommandPresetSummary | null>(null);
    const [skills, setSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [subagentFamilies, setSubagentFamilies] = React.useState<SubagentFamilySummary[]>([]);
    const [plugins, setPlugins] = React.useState<PluginReferenceSummary[]>([]);
    const [skillsLoaded, setSkillsLoaded] = React.useState(false);
    const [skillsLoading, setSkillsLoading] = React.useState(false);
    const [selectedSkills, setSelectedSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [selectedSubagentFamilies, setSelectedSubagentFamilies] = React.useState<SubagentFamilySummary[]>([]);
    const [selectedPlugins, setSelectedPlugins] = React.useState<PluginReferenceSummary[]>([]);
    const [specModeEnabled, setSpecModeEnabled] = React.useState(false);
    const [reasoningEffort, setReasoningEffort] = React.useState<ReasoningEffortLevel>("auto");
    const [reasoningEffortOpen, setReasoningEffortOpen] = React.useState(false);
    const [safetyApprovalMode, setSafetyApprovalMode] = React.useState<SafetyApprovalMode>("reduced");
    const [safetyApprovalModeOpen, setSafetyApprovalModeOpen] = React.useState(false);
    const [files, setFiles] = React.useState<File[]>([]);
    const [uploadedUrls, setUploadedUrls] = React.useState<string[]>([]);
    const [uploading, setUploading] = React.useState(false);
    const [isRecording, setIsRecording] = React.useState(false);
    const [isTranscribing, setIsTranscribing] = React.useState(false);
    const fileInputRef = React.useRef<HTMLInputElement>(null);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const mediaStreamRef = React.useRef<MediaStream | null>(null);
    const audioContextRef = React.useRef<AudioContext | null>(null);
    const sourceNodeRef = React.useRef<MediaStreamAudioSourceNode | null>(null);
    const processorNodeRef = React.useRef<ScriptProcessorNode | null>(null);
    const muteGainRef = React.useRef<GainNode | null>(null);
    const audioChunksRef = React.useRef<Float32Array[]>([]);
    const sampleRateRef = React.useRef(16000);
    const inlineNoticeTimerRef = React.useRef<number | null>(null);

    const safetyApprovalOptions = React.useMemo(() => ([
        {
            mode: "manual" as const,
            title: t("web.chat.safetyApproval.manual.title"),
            description: t("web.chat.safetyApproval.manual.description"),
        },
        {
            mode: "reduced" as const,
            title: t("web.chat.safetyApproval.reduced.title"),
            description: t("web.chat.safetyApproval.reduced.description"),
        },
        {
            mode: "minimal" as const,
            title: t("web.chat.safetyApproval.minimal.title"),
            description: t("web.chat.safetyApproval.minimal.description"),
        },
    ]), [t]);
    const activeSafetyApprovalOption = safetyApprovalOptions.find((option) => option.mode === safetyApprovalMode) || safetyApprovalOptions[1]!;
    const SafetyApprovalIcon = safetyApprovalMode === "manual"
        ? ShieldAlert
        : safetyApprovalMode === "minimal"
            ? ShieldCheck
            : Shield;

    React.useEffect(() => {
        if (typeof window === "undefined") return;
        const stored = window.localStorage.getItem(SAFETY_APPROVAL_MODE_STORAGE_KEY);
        if (stored === "manual" || stored === "reduced" || stored === "minimal") {
            setSafetyApprovalMode(stored);
        }
    }, []);

    React.useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(SAFETY_APPROVAL_MODE_STORAGE_KEY, safetyApprovalMode);
    }, [safetyApprovalMode]);
    const [isFocused, setIsFocused] = React.useState(false);
    const [inlineNotice, setInlineNotice] = React.useState<InlineNotice | null>(null);
    
    // Lightbox state
    const [viewerOpen, setViewerOpen] = React.useState(false);
    const [viewerStartingIndex, setViewerStartingIndex] = React.useState(0);
    const reasoningEffortLevels = React.useMemo<ReasoningEffortLevel[]>(() => {
        const rawLevels = Array.isArray(reasoningEffortControl?.levels) ? reasoningEffortControl.levels : [];
        const allowed = new Set(rawLevels.map((level) => String(level || "").trim().toLowerCase()));
        const levels: ReasoningEffortLevel[] = ["auto", "low", "medium", "high"].filter((level): level is ReasoningEffortLevel =>
            level === "auto" || allowed.has(level)
        );
        return levels.includes("auto") ? levels : ["auto", ...levels];
    }, [reasoningEffortControl?.levels]);
    const reasoningEffortVisible = Boolean(reasoningEffortControl?.visible && reasoningEffortLevels.length > 1);
    const reasoningEffortLabel = React.useMemo(() => {
        const labels: Record<ReasoningEffortLevel, string> = {
            auto: t("web.generated.ad302baaf1"),
            low: t("web.generated.1638d14b43"),
            medium: t("web.generated.5ea71d390a"),
            high: t("web.generated.29b755916c"),
        };
        return labels[reasoningEffort] || labels.auto;
    }, [reasoningEffort, t]);
    const specCommandPresets = React.useMemo<CommandPresetSummary[]>(() => ([
        { name: "spec new", summary: "新建一套 Spec，并从澄清问题开始。", specCommandAction: "new" },
        { name: "spec continue", summary: "继续当前或最近的 Spec。", specCommandAction: "continue" },
        { name: "spec list", summary: "列出当前工作区的 Spec。", specCommandAction: "list" },
        { name: "spec approve", summary: "打开当前 Spec 阶段审批上下文。", specCommandAction: "approve" },
        { name: "spec clarify", summary: "补充 Spec 澄清问题和记录。", specCommandAction: "clarify" },
        { name: "spec analyze", summary: "只读分析需求、设计和任务的闭环质量。", specCommandAction: "analyze" },
        { name: "spec annex", summary: "为复杂 Spec 生成或查看 research/contracts/quickstart 附录。", specCommandAction: "annex" },
    ]), []);
    const contextUsageCommand = React.useMemo<CommandPresetSummary | null>(() => (
        typeof contextUsagePercent === "number" && Number.isFinite(contextUsagePercent)
            ? {
                name: "context",
                summary: `Current usage ${Math.max(0, Math.min(100, Math.round(contextUsagePercent)))}%`,
                readOnlyKind: "context_usage",
                usagePercent: Math.max(0, Math.min(100, Math.round(contextUsagePercent))),
            }
            : null
    ), [contextUsagePercent]);

    const slashQuery = React.useMemo(() => {
        if (selectedCommandPreset) return "";
        const trimmed = input.trimStart();
        if (!trimmed.startsWith("/")) return "";
        return trimmed.slice(1).trim();
    }, [input, selectedCommandPreset]);
    const skillQuery = React.useMemo(() => {
        const trimmed = input.trimStart();
        if (!trimmed.startsWith("@")) return "";
        return trimmed.slice(1).trim();
    }, [input]);
    const isCommandPickerOpen = !selectedCommandPreset && input.trimStart().startsWith("/");
    const isSkillPickerOpen = input.trimStart().startsWith("@");
    const filteredCommandPresets = React.useMemo(() => {
        const allCommands = [...(contextUsageCommand ? [contextUsageCommand] : []), ...specCommandPresets, ...commandPresets];
        if (!slashQuery) {
            return allCommands;
        }
        const keyword = slashQuery.toLowerCase();
        return allCommands.filter((preset) =>
            preset.name.toLowerCase().includes(keyword)
            || String(preset.summary || "").toLowerCase().includes(keyword)
            || String(preset.filename || "").toLowerCase().includes(keyword)
        );
    }, [commandPresets, contextUsageCommand, slashQuery, specCommandPresets]);
    const filteredMentionItems = React.useMemo<MentionPickerItem[]>(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}::${skill.path || ""}`));
        const selectedFamilyIds = new Set(selectedSubagentFamilies.map((family) => family.familyId));
        const selectedPluginIds = new Set(selectedPlugins.map((plugin) => plugin.pluginId));
        const base: MentionPickerItem[] = [
            ...skills
                .filter((skill) => !selectedKeys.has(`${skill.name}::${skill.path || ""}`))
                .map((skill) => ({ kind: "skill" as const, key: `skill:${skill.name}:${skill.path || ""}`, skill })),
            ...subagentFamilies
                .filter((family) => family.familyId && !selectedFamilyIds.has(family.familyId))
                .map((family) => ({ kind: "subagent_family" as const, key: `family:${family.familyId}`, family })),
            ...plugins
                .filter((plugin) => plugin.pluginId && !selectedPluginIds.has(plugin.pluginId))
                .map((plugin) => ({ kind: "plugin" as const, key: `plugin:${plugin.pluginId}`, plugin })),
        ];
        if (!skillQuery) {
            return base;
        }
        const keyword = skillQuery.toLowerCase();
        return base.filter((item) =>
            item.kind === "skill"
                ? (
                    item.skill.name.toLowerCase().includes(keyword)
                    || String(item.skill.description || "").toLowerCase().includes(keyword)
                    || String(item.skill.path || "").toLowerCase().includes(keyword)
                )
                : item.kind === "subagent_family" ? (
                    item.family.familyId.toLowerCase().includes(keyword)
                    || String(item.family.displayName || "").toLowerCase().includes(keyword)
                    || String(item.family.description || "").toLowerCase().includes(keyword)
                    || (item.family.aliases || []).some((alias) => String(alias || "").toLowerCase().includes(keyword))
                )
                : (
                    item.plugin.pluginId.toLowerCase().includes(keyword)
                    || item.plugin.displayName.toLowerCase().includes(keyword)
                    || String(item.plugin.description || "").toLowerCase().includes(keyword)
                )
        );
    }, [selectedSkills, selectedSubagentFamilies, selectedPlugins, skillQuery, skills, subagentFamilies, plugins]);

    const updateInputValue = React.useCallback((nextValue: string) => {
        handleInputChange({
            target: { value: nextValue },
        } as ChangeEvent<HTMLTextAreaElement>);
    }, [handleInputChange]);

    const clearInlineNoticeTimer = React.useCallback(() => {
        if (typeof window !== "undefined" && inlineNoticeTimerRef.current !== null) {
            window.clearTimeout(inlineNoticeTimerRef.current);
            inlineNoticeTimerRef.current = null;
        }
    }, []);

    const showInlineNotice = React.useCallback(
        (tone: InlineNoticeTone, message: string, durationMs = tone === "error" ? 5200 : 3200) => {
            clearInlineNoticeTimer();
            setInlineNotice({ tone, message });
            if (durationMs > 0 && typeof window !== "undefined") {
                inlineNoticeTimerRef.current = window.setTimeout(() => {
                    setInlineNotice((current) => (current?.message === message ? null : current));
                    inlineNoticeTimerRef.current = null;
                }, durationMs);
            }
        },
        [clearInlineNoticeTimer]
    );

    const dismissInlineNotice = React.useCallback(() => {
        clearInlineNoticeTimer();
        setInlineNotice(null);
    }, [clearInlineNoticeTimer]);

    const loadCommandPresets = React.useCallback(async () => {
        if (commandsLoaded || commandsLoading) return;
        setCommandsLoading(true);
        try {
            const res = await fetch("/api/commands", { cache: "no-store" });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    typeof payload?.error === "string"
                        ? payload.error
                        : t("web.generated.32ebe23460")
                );
            }
            setCommandPresets(Array.isArray(payload?.items) ? payload.items : []);
            setCommandsLoaded(true);
        } catch (error) {
            const message = error instanceof Error ? error.message : t("web.generated.32ebe23460");
            showInlineNotice("error", message);
        } finally {
            setCommandsLoading(false);
        }
    }, [commandsLoaded, commandsLoading, showInlineNotice, t]);

    const loadSkills = React.useCallback(async () => {
        if (skillsLoaded || skillsLoading) return;
        setSkillsLoading(true);
        try {
            const [res, pluginRes] = await Promise.all([
                fetch("/api/skills/list", { cache: "no-store" }),
                fetch("/api/plugins/mentions", { cache: "no-store" }),
            ]);
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    typeof payload?.error === "string"
                        ? payload.error
                        : t("web.generated.f1c2a360b6")
                );
            }
            const nextSkills: unknown[] = Array.isArray(payload?.skills) ? payload.skills : [];
            const nextFamilies: unknown[] = Array.isArray(payload?.subagentFamilies) ? payload.subagentFamilies : [];
            const pluginPayload = await pluginRes.json().catch(() => ({}));
            if (!pluginRes.ok) {
                throw new Error(typeof pluginPayload?.error === "string" ? pluginPayload.error : "插件目录加载失败");
            }
            const nextPlugins: unknown[] = Array.isArray(pluginPayload?.items) ? pluginPayload.items : [];
            setSkills(
                nextSkills
                    .filter(isSkillReferenceSummaryCandidate)
                    .map((item): SkillReferenceSummary => ({
                        name: String(item.name || "").trim(),
                        description: String(item.description || "").trim(),
                        path: String(item.path || "").trim(),
                    }))
                    .filter((item: SkillReferenceSummary) => item.name || item.path)
            );
            setSubagentFamilies(
                nextFamilies
                    .filter(isSubagentFamilyCandidate)
                    .map((item): SubagentFamilySummary => ({
                        familyId: String(item.familyId || item.id || item.name || "").trim(),
                        displayName: String(item.displayName || item.name || item.familyId || "").trim(),
                        aliases: Array.isArray(item.aliases) ? item.aliases.map((alias) => String(alias || "").trim()).filter(Boolean) : [],
                        description: String(item.description || "").trim(),
                        memberCount: Number(item.memberCount || 0) || 0,
                    }))
                    .filter((item) => item.familyId)
            );
            setPlugins(
                nextPlugins
                    .filter(isPluginCandidate)
                    .map((item): PluginReferenceSummary => ({
                        pluginId: String(item.pluginId || "").trim(),
                        displayName: String(item.displayName || item.pluginId || "").trim(),
                        description: String(item.description || "").trim(),
                        status: (["ready", "not_installed", "needs_configuration", "offline", "invalid"] as const).includes(item.status as never)
                            ? item.status as PluginReferenceSummary["status"]
                            : "invalid",
                        configurationUrl: String(item.configurationUrl || "").trim(),
                        componentIds: Array.isArray(item.componentIds) ? item.componentIds.map((value) => String(value || "").trim()).filter(Boolean) : undefined,
                        grantScope: "task",
                    }))
                    .filter((item) => item.pluginId)
            );
            setSkillsLoaded(true);
        } catch (error) {
            const message = error instanceof Error ? error.message : t("web.generated.f1c2a360b6");
            showInlineNotice("error", message);
        } finally {
            setSkillsLoading(false);
        }
    }, [showInlineNotice, skillsLoaded, skillsLoading, t]);

    React.useEffect(() => {
        if (isCommandPickerOpen) {
            void loadCommandPresets();
        }
    }, [isCommandPickerOpen, loadCommandPresets]);

    React.useEffect(() => {
        if (isSkillPickerOpen) {
            void loadSkills();
        }
    }, [isSkillPickerOpen, loadSkills]);

    React.useEffect(() => {
        if (!reasoningEffortVisible || !reasoningEffortLevels.includes(reasoningEffort)) {
            setReasoningEffort("auto");
            setReasoningEffortOpen(false);
        }
    }, [reasoningEffort, reasoningEffortLevels, reasoningEffortVisible]);

    const selectCommandPreset = React.useCallback((preset: CommandPresetSummary) => {
        if (preset.readOnlyKind === "context_usage") {
            updateInputValue("");
            dismissInlineNotice();
            return;
        }
        setSelectedCommandPreset(preset);
        updateInputValue("");
        dismissInlineNotice();
    }, [dismissInlineNotice, updateInputValue]);

    const selectSkillReference = React.useCallback((skill: SkillReferenceSummary) => {
        setSelectedSkills((current) => {
            const alreadySelected = current.some((item) => item.name === skill.name && (item.path || "") === (skill.path || ""));
            if (alreadySelected) {
                return current;
            }
            return [...current, skill];
        });
        updateInputValue("");
        dismissInlineNotice();
    }, [dismissInlineNotice, updateInputValue]);

    const selectSubagentFamilyReference = React.useCallback((family: SubagentFamilySummary) => {
        setSelectedSubagentFamilies((current) => {
            const alreadySelected = current.some((item) => item.familyId === family.familyId);
            if (alreadySelected) {
                return current;
            }
            return [...current, family];
        });
        updateInputValue("");
        dismissInlineNotice();
    }, [dismissInlineNotice, updateInputValue]);

    const selectPluginReference = React.useCallback((plugin: PluginReferenceSummary) => {
        setSelectedPlugins((current) => current.some((item) => item.pluginId === plugin.pluginId)
            ? current
            : [...current, { ...plugin, grantScope: "task" }]);
        updateInputValue("");
        dismissInlineNotice();
    }, [dismissInlineNotice, updateInputValue]);

    const selectMentionItem = React.useCallback((item: MentionPickerItem) => {
        if (item.kind === "skill") {
            selectSkillReference(item.skill);
            return;
        }
        if (item.kind === "subagent_family") {
            selectSubagentFamilyReference(item.family);
            return;
        }
        selectPluginReference(item.plugin);
    }, [selectPluginReference, selectSkillReference, selectSubagentFamilyReference]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (isCommandPickerOpen) {
                if (filteredCommandPresets.length > 0) {
                    selectCommandPreset(filteredCommandPresets[0]);
                }
                return;
            }
            if (isSkillPickerOpen) {
                if (filteredMentionItems.length > 0) {
                    selectMentionItem(filteredMentionItems[0]);
                }
                return;
            }
            const form = e.currentTarget.closest('form');
            if (form && !isLoading) form.requestSubmit();
        }
    };

    const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) {
            const newFiles = Array.from(e.target.files);
            if (files.length + newFiles.length > 14) {
                showInlineNotice("error", t("web.generated.ee75d524b0"));
                return;
            }
            setFiles((prev) => [...prev, ...newFiles]);

            setUploading(true);
            try {
                const uploadPromises = newFiles.map(async (file) => {
                    const formData = new FormData();
                    formData.append('file', file);
                    const res = await fetch(`/api/upload`, { method: 'POST', body: formData });
                    if (!res.ok) throw new Error("Failed to upload file");
                    const { url } = await res.json();
                    return url;
                });
                const urls = await Promise.all(uploadPromises);
                setUploadedUrls((prev) => [...prev, ...urls]);
            } catch (error) {
                console.error('Upload failed:', error);
                showInlineNotice("error", t("web.generated.a0608c519e"));
            } finally {
                setUploading(false);
            }
        }
    };

    const removeFile = (index: number) => {
        setFiles((prev) => prev.filter((_, i) => i !== index));
        setUploadedUrls((prev) => prev.filter((_, i) => i !== index));
    };

    React.useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
        }
    }, [input]);

    const stopRecordingTracks = React.useCallback(() => {
        processorNodeRef.current?.disconnect();
        sourceNodeRef.current?.disconnect();
        muteGainRef.current?.disconnect();
        processorNodeRef.current = null;
        sourceNodeRef.current = null;
        muteGainRef.current = null;
        if (mediaStreamRef.current) {
            mediaStreamRef.current.getTracks().forEach((track) => track.stop());
            mediaStreamRef.current = null;
        }
        const audioContext = audioContextRef.current;
        audioContextRef.current = null;
        if (audioContext && audioContext.state !== "closed") {
            void audioContext.close().catch(() => undefined);
        }
    }, []);

    React.useEffect(() => {
        return () => {
            stopRecordingTracks();
            clearInlineNoticeTimer();
        };
    }, [clearInlineNoticeTimer, stopRecordingTracks]);

    const getAudioInputStatus = React.useCallback(async (): Promise<AudioInputStatusPayload> => {
        const res = await fetch("/api/audio/input-status", { method: "GET", cache: "no-store" });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(
                typeof payload?.error === "string"
                    ? payload.error
                    : t("web.generated.c54263a7ac")
            );
        }
        return payload;
    }, [t]);

    const uploadAndSendVoiceAudio = React.useCallback(async (blob: Blob) => {
        if (!onVoiceAudioMessage) {
            throw new Error(t("web.generated.31e1cf987b"));
        }
        const file = new File([blob], "voice-input.wav", { type: "audio/wav" });
        const formData = new FormData();
        formData.append("file", file);
        const res = await fetch("/api/upload", { method: "POST", body: formData });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(
                typeof payload?.error === "string"
                    ? payload.error
                    : t("web.generated.54e079b2b9")
            );
        }
        const url = String(payload?.url || payload?.publicUrl || "").trim();
        if (!url) {
            throw new Error(t("web.generated.84c0a1448b"));
        }
        const messageData = {
            fileUrls: [url],
            attachments: [{
                url,
                publicUrl: String(payload?.publicUrl || url),
                name: file.name,
                mimeType: file.type,
                size: file.size,
                mediaKind: "audio",
                source: "os_web_voice_upload",
            }],
            safetyApprovalMode,
        };
        try {
            const result = onVoiceAudioMessage(messageData);
            void Promise.resolve(result).catch((error) => {
                const message = error instanceof Error ? error.message : t("web.generated.accc20bbec");
                showInlineNotice("error", t("web.generated.8b30d36521", { value0: message }));
            });
        } catch (error) {
            const message = error instanceof Error ? error.message : t("web.generated.accc20bbec");
            showInlineNotice("error", t("web.generated.8b30d36521", { value0: message }));
        }
    }, [onVoiceAudioMessage, safetyApprovalMode, showInlineNotice, t]);

    const transcribeAudio = React.useCallback(async (blob: Blob) => {
        try {
            setIsTranscribing(true);
            const status = await getAudioInputStatus();
            if (status.route === "vision_audio") {
                await uploadAndSendVoiceAudio(blob);
                return;
            }
            if (status.route !== "stt") {
                throw new Error(
                    String(status.stt?.reason || status.visionAudio?.reason || status.error || "").trim()
                    || t("web.generated.b114fb0b72")
                );
            }

            const file = new File([blob], "voice-input.wav", { type: "audio/wav" });
            const formData = new FormData();
            formData.append("file", file);

            const res = await fetch("/api/audio/stt", {
                method: "POST",
                body: formData,
            });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                        typeof payload?.detail === "string"
                            ? payload.detail
                        : typeof payload?.error === "string"
                            ? payload.error
                            : t("web.generated.d2461f19b1")
                );
            }

            const text = typeof payload?.text === "string" ? payload.text.trim() : "";
            if (!text) {
                throw new Error(t("web.generated.130c17ee76"));
            }

            onVoiceTranscript?.(text);
        } catch (error) {
            const message = error instanceof Error ? error.message : t("web.generated.d2461f19b1");
            showInlineNotice("error", t("web.generated.8b30d36521", { value0: message }));
        } finally {
            setIsTranscribing(false);
        }
    }, [getAudioInputStatus, onVoiceTranscript, showInlineNotice, t, uploadAndSendVoiceAudio]);

    const startRecording = React.useCallback(async () => {
        if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
            showInlineNotice("error", t("web.generated.88d4317072"));
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const AudioContextCtor =
                window.AudioContext ||
                (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
            if (!AudioContextCtor) {
                stream.getTracks().forEach((track) => track.stop());
                showInlineNotice("error", t("web.generated.8467e7e315"));
                return;
            }

            const audioContext = new AudioContextCtor();
            const sourceNode = audioContext.createMediaStreamSource(stream);
            const processorNode = audioContext.createScriptProcessor(4096, 1, 1);
            const muteGain = audioContext.createGain();
            muteGain.gain.value = 0;

            mediaStreamRef.current = stream;
            audioChunksRef.current = [];
            sampleRateRef.current = audioContext.sampleRate;
            audioContextRef.current = audioContext;
            sourceNodeRef.current = sourceNode;
            processorNodeRef.current = processorNode;
            muteGainRef.current = muteGain;

            processorNode.onaudioprocess = (event) => {
                const channel = event.inputBuffer.getChannelData(0);
                audioChunksRef.current.push(new Float32Array(channel));
            };

            sourceNode.connect(processorNode);
            processorNode.connect(muteGain);
            muteGain.connect(audioContext.destination);
            await audioContext.resume();
            setIsRecording(true);
            showInlineNotice("info", t("web.generated.04e71973d4"), 0);
        } catch (error) {
            stopRecordingTracks();
            const message = error instanceof Error ? error.message : t("web.generated.74f6b01f49");
            showInlineNotice("error", t("web.generated.2f87592da8", { value0: message }));
        }
    }, [showInlineNotice, stopRecordingTracks, t]);

    const stopRecording = React.useCallback(() => {
        if (!isRecording) {
            return;
        }

        setIsRecording(false);
        const mergedSamples = mergeFloat32Chunks(audioChunksRef.current);
        audioChunksRef.current = [];
        const sampleRate = sampleRateRef.current || 16000;
        stopRecordingTracks();

        if (mergedSamples.length === 0) {
            showInlineNotice("error", t("web.generated.0695bf8b42"));
            return;
        }

        const downsampled = downsampleTo16k(mergedSamples, sampleRate);
        const wavBlob = encodeWavBlob(downsampled, 16000);
        void transcribeAudio(wavBlob);
    }, [isRecording, showInlineNotice, stopRecordingTracks, t, transcribeAudio]);

    const handleMicClick = React.useCallback(() => {
        if (isTranscribing) return;
        if (isRecording) {
            stopRecording();
            return;
        }
        void startRecording();
    }, [isRecording, isTranscribing, startRecording, stopRecording]);

    const hasTypedMessage = input.trim().length > 0;
    const canSubmit = (!isCommandPickerOpen && !isSkillPickerOpen && hasTypedMessage)
        || files.length > 0
        || Boolean(selectedCommandPreset)
        || selectedSkills.length > 0
        || selectedSubagentFamilies.length > 0
        || selectedPlugins.length > 0;
    const canQueueWhileRunning = isLoading && canSubmit;

    // Convert attached files to MediaItems for Lightbox
    const mediaItems: MediaItem[] = files.map((f) => {
        const isVideo = f.type.startsWith('video/');
        const itemType: 'video' | 'image' = isVideo ? 'video' : 'image';
        return {
            type: itemType,
            src: URL.createObjectURL(f),
            name: f.name,
            file: f
        };
    }).filter(item => item.type === 'video' || item.type === 'image'); // exclude raw docs from lightbox

    return (
        <form
            onSubmit={async (e) => {
                const nextData: Record<string, unknown> = {};
                const pendingSpecMode = specModeEnabled;
                nextData.safetyApprovalMode = safetyApprovalMode;
                if (contextSessionRefs.length > 0) {
                    nextData.contextSessionRefs = contextSessionRefs;
                }
                if (uploadedUrls.length > 0) {
                    nextData.fileUrls = uploadedUrls;
                    nextData.attachments = uploadedUrls.map((url, index) => ({
                        url,
                        publicUrl: url,
                        name: files[index]?.name || undefined,
                        mimeType: files[index]?.type || undefined,
                        size: typeof files[index]?.size === "number" ? files[index]?.size : undefined,
                        source: "os_web_upload",
                    }));
                }
                if (selectedCommandPreset?.name) {
                    if (selectedCommandPreset.specCommandAction) {
                        nextData.specMode = true;
                        nextData.specCommand = { action: selectedCommandPreset.specCommandAction };
                    } else {
                        nextData.commandPreset = { name: selectedCommandPreset.name };
                    }
                }
                if (selectedSkills.length > 0) {
                    nextData.skillReferences = selectedSkills.map((skill) => ({
                        name: skill.name,
                        description: skill.description || "",
                        path: skill.path || "",
                    }));
                }
                if (selectedPlugins.length > 0) {
                    nextData.pluginReferences = selectedPlugins.map((plugin) => ({
                        pluginId: plugin.pluginId,
                        name: plugin.displayName,
                        scope: plugin.grantScope,
                        componentIds: plugin.componentIds,
                    }));
                }
                const contextMentions = [
                    ...selectedSkills.map((skill) => ({
                        kind: "skill",
                        name: skill.name,
                        label: skill.name,
                        description: skill.description || "",
                        path: skill.path || "",
                        sourceType: "explicit_mention",
                    })),
                    ...selectedSubagentFamilies.map((family) => ({
                        kind: "subagent_family",
                        id: family.familyId,
                        familyId: family.familyId,
                        name: family.displayName || family.familyId,
                        label: family.displayName || family.familyId,
                        description: family.description || "",
                        sourceType: "explicit_mention",
                    })),
                ];
                if (contextMentions.length > 0) {
                    nextData.contextMentions = contextMentions;
                }
                if (reasoningEffortVisible) {
                    nextData.supervisorReasoningEffort = reasoningEffort;
                }
                if (pendingSpecMode) {
                    nextData.specMode = true;
                }
                if (pendingSpecMode) {
                    setSpecModeEnabled(false);
                }

                await handleSubmit(e, { data: nextData });
                setFiles([]);
                setUploadedUrls([]);
                setSelectedCommandPreset(null);
                setSelectedSkills([]);
                setSelectedSubagentFamilies([]);
                setSelectedPlugins([]);
            }}
            className={cn(
                "relative mx-auto flex w-full flex-col overflow-hidden rounded-[1.25rem] border shadow-sm transition-all duration-500",
                shellClassName ?? "max-w-4xl",
                isFocused 
                    ? "bg-stone-50/95 dark:bg-stone-900/90 shadow-[0_8px_32px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_32px_rgba(0,0,0,0.3)] border-orange-500/30 dark:border-amber-500/30" 
                    : "bg-stone-50/60 dark:bg-stone-900/50 backdrop-blur-xl hover:bg-stone-50/80 hover:dark:bg-stone-900/70 border-stone-200/60 dark:border-stone-800/60",
                specModeEnabled && "border-red-500/55 shadow-[0_0_0_1px_rgba(239,68,68,0.26),0_0_28px_rgba(248,113,113,0.24)] animate-pulse"
            )}
            style={{ backdropFilter: 'blur(16px) saturate(120%)' }}
        >
            {/* Unified Input Box Top Area: File Previews (if any) */}
            {files.length > 0 && (
                <div className="scrollbar-none flex min-h-[4rem] items-end gap-2.5 overflow-x-auto px-3 pb-0 pt-3">
                    {files.map((file, i) => {
                        const url = URL.createObjectURL(file);
                        const isVideo = file.type.startsWith('video/');
                        const isImage = file.type.startsWith('image/');
                        
                        // Use the new VideoThumbnail for videos
                        if (isVideo) {
                            return (
                                <VideoThumbnail 
                                    key={i} 
                                    url={url} 
                                    uploading={uploading} 
                                    onRemove={() => removeFile(i)} 
                                    onClick={() => {
                                        setViewerStartingIndex(i);
                                        setViewerOpen(true);
                                    }}
                                />
                            );
                        }
                        
                        // Default image/other render
                        return (
                            <div 
                                key={i} 
                                className="relative w-16 h-16 rounded-lg overflow-hidden shrink-0 group border border-zinc-200 dark:border-white/10 shadow-sm animate-in zoom-in-50 duration-200 cursor-pointer"
                                onClick={() => {
                                    if (isImage) {
                                        setViewerStartingIndex(i);
                                        setViewerOpen(true);
                                    }
                                }}
                            >
                                {isImage ? (
                                    <img src={url} alt="preview" className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
                                ) : (
                                    <div className="w-full h-full flex items-center justify-center bg-zinc-100 dark:bg-zinc-800 text-[10px] text-muted-foreground p-1 break-all text-center leading-tight">
                                        {file.name.split('.').pop()}
                                    </div>
                                )}

                                {uploading && (
                                    <div className="absolute inset-0 bg-black/40 flex items-center justify-center z-20">
                                        <Loader2 className="w-5 h-5 animate-spin text-white" />
                                    </div>
                                )}

                                <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); removeFile(i); }}
                                    className="absolute top-0 right-0 p-1 bg-black/50 text-white rounded-bl-lg opacity-0 group-hover:opacity-100 transition-all hover:bg-black/70 z-30"
                                >
                                    <X className="w-3 h-3" />
                                </button>
                            </div>
                        );
                    })}
                </div>
            )}

            <div className="flex flex-col relative">
                {(selectedCommandPreset || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0 || selectedPlugins.length > 0 || contextSessionRefs.length > 0) && (
                    <div className="flex min-h-[28px] flex-wrap items-center gap-1 px-2.5 pt-1.5">
                        {contextSessionRefs.map((reference) => (
                            <div
                                key={reference.sessionId}
                                className="inline-flex max-w-full items-center gap-1 rounded-full border border-cyan-200 bg-cyan-50 px-2 py-0.5 text-[10px] font-medium text-cyan-700 dark:border-cyan-500/20 dark:bg-cyan-500/10 dark:text-cyan-200 sm:text-[11px]"
                                title={reference.sessionId}
                            >
                                <CornerDownRight className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span className="truncate">{t("web.chat.contextSessionRef")} · {reference.sessionId.slice(0, 12)}</span>
                                <button
                                    type="button"
                                    onClick={() => onRemoveContextSessionRef?.(reference.sessionId)}
                                    className="rounded-full text-current/70 transition hover:text-current"
                                    aria-label={t("web.chat.removeContextSessionRef")}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                        {selectedCommandPreset && (
                            <div className="inline-flex max-w-full items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[10px] font-medium text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200 sm:text-[11px]">
                                <Command className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span className="truncate">{t("web.generated.91c1d26378")}：{selectedCommandPreset.name}</span>
                                <button
                                    type="button"
                                    onClick={() => setSelectedCommandPreset(null)}
                                    className="rounded-full text-current/70 transition hover:text-current"
                                    aria-label={t("web.generated.6b8a8efb3b")}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        )}
                        {selectedSkills.map((skill) => (
                            <div
                                key={`${skill.name}:${skill.path || ""}`}
                                className="inline-flex max-w-full items-center gap-1 rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2 py-0.5 text-[10px] font-medium text-fuchsia-700 dark:border-fuchsia-500/20 dark:bg-fuchsia-500/10 dark:text-fuchsia-200 sm:text-[11px]"
                                title={skill.path || skill.description || skill.name}
                            >
                                <AtSign className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span className="truncate">{skill.name}</span>
                                <button
                                    type="button"
                                    onClick={() => setSelectedSkills((current) => current.filter((item) => !(item.name === skill.name && (item.path || "") === (skill.path || ""))))}
                                    className="rounded-full text-current/70 transition hover:text-current"
                                    aria-label={t("web.generated.67c18a5ab0")}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                        {selectedSubagentFamilies.map((family) => (
                            <div
                                key={family.familyId}
                                className="inline-flex max-w-full items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-medium text-sky-700 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-200 sm:text-[11px]"
                                title={family.description || family.familyId}
                            >
                                <AtSign className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span className="truncate">{family.displayName || family.familyId}</span>
                                <button
                                    type="button"
                                    onClick={() => setSelectedSubagentFamilies((current) => current.filter((item) => item.familyId !== family.familyId))}
                                    className="rounded-full text-current/70 transition hover:text-current"
                                    aria-label={t("web.generated.0f220b7fb7")}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                        {selectedPlugins.map((plugin) => (
                            <div
                                key={plugin.pluginId}
                                className="inline-flex max-w-full items-center gap-1 border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200 sm:text-[11px]"
                                title={plugin.description || plugin.pluginId}
                            >
                                <span className={cn("size-1.5 rounded-full", plugin.status === "ready" ? "bg-emerald-500" : "bg-amber-500")} />
                                <span className="truncate">{plugin.displayName}</span>
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <button type="button" className="border-l border-current/15 pl-1 text-[9px] text-current/75 hover:text-current">
                                            {plugin.grantScope === "session" ? "本会话" : "本任务"}
                                        </button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="start" side="top" className="w-48 rounded-md p-1">
                                        <DropdownMenuItem onSelect={() => setSelectedPlugins((current) => current.map((item) => item.pluginId === plugin.pluginId ? { ...item, grantScope: "task" } : item))} className="rounded-sm text-xs">
                                            <span className="flex-1">仅当前任务</span>{plugin.grantScope === "task" ? <CheckCircle2 className="size-3.5" /> : null}
                                        </DropdownMenuItem>
                                        <DropdownMenuItem onSelect={() => setSelectedPlugins((current) => current.map((item) => item.pluginId === plugin.pluginId ? { ...item, grantScope: "session" } : item))} className="rounded-sm text-xs">
                                            <span className="flex-1">本会话持续授权</span>{plugin.grantScope === "session" ? <CheckCircle2 className="size-3.5" /> : null}
                                        </DropdownMenuItem>
                                    </DropdownMenuContent>
                                </DropdownMenu>
                                <button
                                    type="button"
                                    onClick={() => setSelectedPlugins((current) => current.filter((item) => item.pluginId !== plugin.pluginId))}
                                    className="text-current/65 transition hover:text-current"
                                    aria-label={`移除 ${plugin.displayName}`}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <Textarea
                    ref={textareaRef}
                    value={input}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    onFocus={() => setIsFocused(true)}
                    onBlur={() => setIsFocused(false)}
                    placeholder={selectedAgentName ? t("web.generated.04cdce87c7", { value0: selectedAgentName }) : t("web.generated.a706426e02")}
                    className="custom-scrollbar min-h-[44px] max-h-[172px] w-full resize-none overflow-y-auto border-none bg-transparent px-3 py-2 text-[14px] leading-relaxed placeholder:text-muted-foreground/50 shadow-none focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 sm:text-[15px]"
                />

                {isCommandPickerOpen && (
                    <div className="mx-3 mb-2 rounded-2xl border border-border/60 bg-background/95 shadow-lg backdrop-blur-xl">
                        <div className="border-b border-border/50 px-3 py-2 text-[11px] text-muted-foreground">
                            {t("web.generated.b27144499f")}<span className="font-medium text-foreground">/</span>{t("web.generated.2e1be7fa21")}
                        </div>
                        <div className="max-h-32 sm:max-h-40 overflow-y-auto px-1 py-1">
                            {commandsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t("web.generated.8a3ecc1a85")}</div>
                            ) : filteredCommandPresets.length > 0 ? (
                                filteredCommandPresets.map((preset) => (
                                    <button
                                        key={preset.name}
                                        type="button"
                                        onClick={() => selectCommandPreset(preset)}
                                        className="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition hover:bg-accent/50"
                                    >
                                        {preset.readOnlyKind === "context_usage" && typeof preset.usagePercent === "number"
                                            ? <ContextUsageRing percent={preset.usagePercent} />
                                            : <Command className="mt-0.5 h-4 w-4 shrink-0 text-orange-500" />}
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-sm font-medium text-foreground">
                                                {preset.name}
                                            </div>
                                            {preset.summary && (
                                                <div className="line-clamp-1 sm:line-clamp-2 text-[11px] leading-4.5 text-muted-foreground">
                                                    {preset.summary}
                                                </div>
                                            )}
                                        </div>
                                    </button>
                                ))
                            ) : (
                                <div className="px-3 py-3 text-sm text-muted-foreground">
                                    {commandsLoaded
                                        ? t("web.generated.088733e9c0")
                                        : t("web.generated.3b0ae80c9c")}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {isSkillPickerOpen && (
                    <div className="mx-3 mb-2 rounded-md border border-border/70 bg-background/98 shadow-md backdrop-blur-xl">
                        <div className="border-b border-fuchsia-200/40 px-3 py-2 text-[11px] text-muted-foreground dark:border-fuchsia-500/10">
                            {t("web.generated.b27144499f")}<span className="font-medium text-fuchsia-600">@</span>{t("web.generated.7c6c5d2a05")}
                        </div>
                        <div className="max-h-32 overflow-y-auto px-1 py-1 sm:max-h-40">
                            {skillsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t("web.generated.2aeb46969c")}</div>
                            ) : filteredMentionItems.length > 0 ? (
                                filteredMentionItems.map((item, index) => (
                                    <button
                                        key={item.key}
                                        type="button"
                                        onClick={() => selectMentionItem(item)}
                                        className={cn(
                                            "flex w-full items-start gap-2 rounded-md px-3 py-2 text-left transition",
                                            item.kind === "skill"
                                                ? "hover:bg-fuchsia-50/70 dark:hover:bg-fuchsia-500/10"
                                                : item.kind === "subagent_family"
                                                    ? "hover:bg-sky-50/70 dark:hover:bg-sky-500/10"
                                                    : "hover:bg-emerald-50/70 dark:hover:bg-emerald-500/10",
                                            index > 0 && filteredMentionItems[index - 1]?.kind !== item.kind ? "mt-1 border-t border-border/50 pt-3" : ""
                                        )}
                                    >
                                        <AtSign className={cn(
                                            "mt-0.5 h-4 w-4 shrink-0",
                                            item.kind === "skill" ? "text-fuchsia-500" : item.kind === "subagent_family" ? "text-sky-500" : "text-emerald-500"
                                        )} />
                                        <div className="min-w-0 flex-1">
                                            <div className="flex items-center gap-2">
                                                <span className="truncate text-sm font-medium text-foreground">
                                                    {item.kind === "skill" ? item.skill.name : item.kind === "subagent_family" ? (item.family.displayName || item.family.familyId) : item.plugin.displayName}
                                                </span>
                                                <span className="shrink-0 border border-border/60 bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                                    {item.kind === "skill" ? "Skill" : item.kind === "subagent_family" ? "Family" : "Plugin"}
                                                </span>
                                            </div>
                                            {(item.kind === "skill" ? item.skill.description : item.kind === "subagent_family" ? item.family.description : item.plugin.description) && (
                                                <div className="line-clamp-1 text-[11px] leading-4.5 text-muted-foreground sm:line-clamp-2">
                                                    {item.kind === "skill" ? item.skill.description : item.kind === "subagent_family" ? item.family.description : item.plugin.description}
                                                </div>
                                            )}
                                            {item.kind === "skill" && item.skill.path && (
                                                <div className="truncate text-[10px] text-fuchsia-700/80 dark:text-fuchsia-300/80">
                                                    {item.skill.path}
                                                </div>
                                            )}
                                            {item.kind === "subagent_family" && item.family.memberCount ? (
                                                <div className="truncate text-[10px] text-sky-700/80 dark:text-sky-300/80">
                                                    {t("web.generated.87a9341147", { value0: item.family.memberCount })}
                                                </div>
                                            ) : null}
                                            {item.kind === "plugin" ? (
                                                <div className={cn("text-[10px]", item.plugin.status === "ready" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")}>
                                                    {item.plugin.status === "ready"
                                                        ? "已就绪 · 默认仅当前任务"
                                                        : item.plugin.status === "not_installed"
                                                            ? "未安装 · 提交后返回安装入口"
                                                            : item.plugin.status === "needs_configuration"
                                                                ? "需配置 · 提交后返回配置入口"
                                                                : item.plugin.status === "offline"
                                                                    ? "离线 · 提交后返回阻断原因"
                                                                    : "状态不可用 · 提交后返回诊断入口"}
                                                </div>
                                            ) : null}
                                        </div>
                                    </button>
                                ))
                            ) : (
                                <div className="px-3 py-3 text-sm text-muted-foreground">
                                    {skillsLoaded
                                        ? t("web.generated.2a87cc8956")
                                        : t("web.generated.51c1734f23")}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {inlineNotice && (
                    <div
                        className={cn(
                            "mx-3 mb-1 flex items-start gap-2 rounded-xl border px-3 py-2 text-xs sm:text-sm",
                            inlineNotice.tone === "error" && "border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200",
                            inlineNotice.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200",
                            inlineNotice.tone === "info" && "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
                        )}
                    >
                        {inlineNotice.tone === "error" ? (
                            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        ) : inlineNotice.tone === "success" ? (
                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                        ) : (
                            <Info className="mt-0.5 h-4 w-4 shrink-0" />
                        )}
                        <div className="min-w-0 flex-1 leading-5">{inlineNotice.message}</div>
                        <button
                            type="button"
                            onClick={dismissInlineNotice}
                            className="mt-0.5 rounded text-current/70 transition hover:text-current"
                            aria-label={t("web.generated.17b50303e2")}
                        >
                            <X className="h-3.5 w-3.5" />
                        </button>
                    </div>
                )}

                <div className="flex items-center justify-between px-3 pb-2 pt-0">
                    <div className="flex items-center gap-1.5">
                        <Button
                            type="button"
                            variant={specModeEnabled ? "secondary" : "ghost"}
                            size="icon"
                            onClick={() => setSpecModeEnabled((current) => !current)}
                            aria-pressed={specModeEnabled}
                            className={cn(
                                "h-[28px] w-[28px] rounded-lg transition-colors",
                                specModeEnabled
                                    ? "bg-violet-500/12 text-violet-700 shadow-[0_0_16px_rgba(139,92,246,0.22)] hover:bg-violet-500/18 dark:bg-violet-500/15 dark:text-violet-200"
                                    : "text-muted-foreground hover:bg-zinc-100/50 hover:text-foreground dark:hover:bg-zinc-800/50"
                            )}
                            title={t("web.generated.d802f23b2a")}
                        >
                            <Orbit className={cn("h-4 w-4", specModeEnabled && "animate-[spin_1.6s_linear_infinite]")} />
                        </Button>
                        <DropdownMenu open={safetyApprovalModeOpen} onOpenChange={setSafetyApprovalModeOpen}>
                            <DropdownMenuTrigger asChild>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    aria-label={activeSafetyApprovalOption.title}
                                    title={activeSafetyApprovalOption.title}
                                    className={cn(
                                        "h-[28px] w-[28px] rounded-lg transition-colors",
                                        safetyApprovalMode === "manual"
                                            ? "text-rose-500 hover:bg-rose-500/10"
                                            : safetyApprovalMode === "minimal"
                                                ? "text-emerald-500 hover:bg-emerald-500/10"
                                                : "text-amber-500 hover:bg-amber-500/10"
                                    )}
                                >
                                    <SafetyApprovalIcon className="h-4 w-4" />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                side="top"
                                align="start"
                                sideOffset={8}
                                collisionPadding={12}
                                className="z-[90] max-h-[min(50vh,360px)] w-72 overflow-y-auto rounded-2xl border-zinc-200/70 bg-white/95 p-1.5 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-zinc-700/70 dark:bg-zinc-950/95"
                            >
                                {safetyApprovalOptions.map((option) => {
                                    const active = option.mode === safetyApprovalMode;
                                    return (
                                        <DropdownMenuItem
                                            key={option.mode}
                                            onSelect={() => setSafetyApprovalMode(option.mode)}
                                            className={cn(
                                                "cursor-pointer items-start gap-2 rounded-xl px-2.5 py-2 text-left",
                                                active
                                                    ? "bg-amber-500/10 text-amber-800 focus:bg-amber-500/15 dark:bg-amber-400/12 dark:text-amber-100"
                                                    : "text-zinc-600 focus:bg-zinc-100/80 dark:text-zinc-300 dark:focus:bg-zinc-800/80"
                                            )}
                                        >
                                            <Shield className={cn("mt-0.5 h-4 w-4 shrink-0", active ? "text-amber-500" : "text-muted-foreground")} />
                                            <span className="min-w-0 flex-1">
                                                <span className="block text-[12px] font-semibold">{option.title}</span>
                                                <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">{option.description}</span>
                                            </span>
                                        </DropdownMenuItem>
                                    );
                                })}
                            </DropdownMenuContent>
                        </DropdownMenu>
                        {reasoningEffortVisible ? (
                            <div className="relative">
                                <button
                                    type="button"
                                    onClick={() => setReasoningEffortOpen((current) => !current)}
                                    className={cn(
                                        "group inline-flex h-[28px] items-center gap-1 rounded-full border px-2 text-[10px] font-semibold tracking-[0.04em] transition-all",
                                        reasoningEffort === "auto"
                                            ? "border-zinc-200/70 bg-white/55 text-zinc-500 hover:border-amber-300/60 hover:text-zinc-700 dark:border-zinc-700/60 dark:bg-zinc-900/40 dark:text-zinc-400 dark:hover:border-amber-400/35"
                                            : "border-amber-300/60 bg-amber-100/70 text-amber-800 shadow-[0_4px_18px_rgba(245,158,11,0.16)] hover:bg-amber-100 dark:border-amber-400/35 dark:bg-amber-500/12 dark:text-amber-100"
                                    )}
                                    title={t("web.generated.9d1e18be56")}
                                >
                                    <Gauge className="h-3.5 w-3.5" />
                                    <span>{t("web.generated.97372606ee")}·{reasoningEffortLabel}</span>
                                </button>
                                {reasoningEffortOpen ? (
                                    <div className="absolute bottom-full left-0 z-30 mb-2 flex overflow-hidden rounded-2xl border border-zinc-200/70 bg-white/95 p-1 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-zinc-700/70 dark:bg-zinc-950/95">
                                        {reasoningEffortLevels.map((level) => {
                                            const active = level === reasoningEffort;
                                            const labels: Record<ReasoningEffortLevel, string> = {
                                                auto: t("web.generated.0f2b6402a6"),
                                                low: t("web.generated.1638d14b43"),
                                                medium: t("web.generated.c3c3788ddb"),
                                                high: t("web.generated.29b755916c"),
                                            };
                                            return (
                                                <button
                                                    key={level}
                                                    type="button"
                                                    onClick={() => {
                                                        setReasoningEffort(level);
                                                        setReasoningEffortOpen(false);
                                                    }}
                                                    className={cn(
                                                        "h-7 rounded-xl px-2.5 text-[11px] font-medium transition",
                                                        active
                                                            ? "bg-amber-500 text-white shadow-sm"
                                                            : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                                                    )}
                                                >
                                                    {labels[level]}
                                                </button>
                                            );
                                        })}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                        <input type="file" multiple className="hidden" ref={fileInputRef} onChange={handleFileSelect} />
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className={cn("h-[28px] w-[28px] rounded-lg text-muted-foreground transition-colors hover:bg-zinc-100/50 hover:text-foreground dark:hover:bg-zinc-800/50", files.length > 0 ? "text-primary" : "")}
                            onClick={() => fileInputRef.current?.click()}
                            title={t("web.generated.ebd1e88cea")}
                        >
                            <Paperclip className="h-4 w-4" />
                        </Button>
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={handleMicClick}
                            disabled={uploading || isTranscribing}
                            className={cn(
                                "h-[28px] w-[28px] rounded-lg transition-colors",
                                isRecording
                                    ? "text-red-500 bg-red-500/10 hover:bg-red-500/15"
                                    : "text-muted-foreground hover:text-foreground hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50",
                                isTranscribing ? "opacity-60 cursor-wait" : ""
                            )}
                            title={isRecording ? t("web.generated.bb6d8587d6") : isTranscribing ? t("web.generated.8fa10622ed") : t("web.generated.5ca09a650a")}
                        >
                            {isTranscribing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
                        </Button>
                    </div>

                    <Button
                        type={isLoading && !canQueueWhileRunning ? "button" : "submit"}
                        size="icon"
                        className={cn(
                            "h-[32px] w-[32px] rounded-xl shadow-sm transition-all duration-300",
                            isLoading && !canQueueWhileRunning
                                ? "bg-stone-200 text-stone-400 dark:bg-stone-800 dark:text-stone-500"
                                : (canSubmit
                                    ? "bg-gradient-to-br from-orange-400 to-amber-600 dark:from-orange-500 dark:to-amber-700 text-white shadow-[0_0_12px_rgba(249,115,22,0.4)] hover:shadow-[0_0_16px_rgba(249,115,22,0.6)] hover:scale-105 border border-orange-300/50 dark:border-amber-500/50"
                                    : "bg-stone-100 text-stone-300 dark:bg-stone-800/60 dark:text-stone-600 cursor-not-allowed")
                        )}
                        disabled={uploading || (!isLoading && !canSubmit)}
                        onClick={(e) => {
                            if (isLoading && !canQueueWhileRunning && onStop) {
                                e.preventDefault();
                                onStop();
                            }
                        }}
                    >
                        {isLoading && !canQueueWhileRunning ? (
                            <div className="relative flex items-center justify-center w-full h-full group">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-100 dark:bg-zinc-800 rounded-xl">
                                    <Square className="h-3 w-3 fill-destructive text-destructive" />
                                </div>
                            </div>
                        ) : canQueueWhileRunning ? (
                            <CornerDownRight className="h-4 w-4" />
                        ) : (
                            <Send className="ml-0.5 h-4 w-4" />
                        )}
                    </Button>
                </div>
            </div>

            {/* Media Viewer Lightbox */}
            <MediaViewerLightbox 
                isOpen={viewerOpen} 
                onClose={() => setViewerOpen(false)} 
                items={mediaItems} 
                initialIndex={viewerStartingIndex} 
            />
        </form>
    );
}
