"use client";
/* eslint-disable @next/next/no-img-element */

import * as React from "react";
import {
    buildComposerInlineSegments,
    composerTextContainsReference,
    insertComposerReference,
    removeComposerReferenceAtBackspace,
    resolveComposerInlineQuery,
    stripComposerReferences,
    type ComposerInlineReference,
} from "@v8/session-realtime/composer-inline-references";
import type { PluginReferenceSummary, SupervisorRuntimeMode } from "@v8/session-realtime";
import { ReasoningEffortControl } from "@v8/product-ui";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Paperclip, Send, Mic, Loader2, Square, X, PlayCircle, AlertCircle, CheckCircle2, Info, Command, AtSign, Gauge, Orbit, CornerDownRight, Shield, ShieldAlert, ShieldCheck, Code2, Sparkles, Search, Palette, MonitorCog, Workflow } from "lucide-react";
import { ChangeEvent, FormEvent } from "react";
import { MediaViewerLightbox, MediaItem } from "./MediaViewerLightbox";
import { useT } from "@/components/providers/LocaleProvider";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
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
    sessionRunning?: boolean;
    canStopRun?: boolean;
    onStop?: () => void;
    selectedAgentName?: string;
    shellClassName?: string;
    reasoningEffortControl?: ReasoningEffortControl | null;
    reasoningEffort?: ReasoningEffortLevel;
    onReasoningEffortChange?: (level: ReasoningEffortLevel) => void | Promise<void>;
    contextSessionRefs?: ContextSessionReference[];
    onRemoveContextSessionRef?: (sessionId: string) => void;
    contextUsagePercent?: number | null;
    supervisorRuntimeMode?: SupervisorRuntimeMode;
    onSupervisorRuntimeModeChange?: (mode: SupervisorRuntimeMode) => void | Promise<void>;
    onManualMemory?: () => Promise<{ accepted: boolean; message: string }>;
    uploadScope?: {
        sessionId?: string | null;
        workspaceId?: string | null;
        workspacePath?: string | null;
        projectId?: string | null;
    };
}

interface ContextSessionReference {
    sessionId: string;
    source: "history_menu";
}

type ReasoningEffortLevel = "auto" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
type SafetyApprovalMode = "manual" | "reduced" | "minimal";
type SpecCommandAction = "new" | "continue" | "list" | "approve" | "clarify" | "analyze" | "annex";
const SAFETY_APPROVAL_MODE_STORAGE_KEY = "v8-web-safety-approval-mode";

interface ReasoningEffortControl {
    visible?: boolean;
    supported?: boolean;
    levels?: string[];
    defaultLevel?: string;
    modelDefaultLevel?: string;
    profileDefaultLevel?: string;
    sessionLevel?: string;
    effectiveLevel?: string;
    selectionSource?: "session" | "model_default";
    modelRef?: string;
    sessionId?: string;
}

interface VoiceAudioMessageData {
    fileUrls: string[];
    attachments: Array<Record<string, unknown>>;
    supervisorRuntimeMode: SupervisorRuntimeMode;
    safetyApprovalMode?: SafetyApprovalMode;
}

type UploadedSourceDescriptor = {
    id?: string;
    sourceId?: string;
    sourceKind?: string;
    resourceRole?: string;
    name?: string;
    url?: string;
    publicUrl?: string;
    previewUrl?: string;
    workspacePath?: string;
    workspaceRelativePath?: string;
    resourceRef?: Record<string, unknown>;
    type?: string;
    size?: number;
};

function appendUploadScope(
    formData: FormData,
    scope: InputAreaProps["uploadScope"],
    sourceKind: "web_upload" | "web_voice",
) {
    formData.set("sourceKind", sourceKind);
    const entries = {
        sessionId: scope?.sessionId,
        workspaceId: scope?.workspaceId,
        workspacePath: scope?.workspacePath,
        projectId: scope?.projectId,
    };
    for (const [key, value] of Object.entries(entries)) {
        const normalized = String(value || "").trim();
        if (normalized) formData.set(key, normalized);
    }
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
    memoryAction?: "session_extraction";
    readOnlyKind?: "context_usage";
    usagePercent?: number;
}

function ContextUsageRing({ percent }: { percent: number }) {
    const radius = 8;
    const circumference = Math.PI * 2 * radius;
    const dash = circumference * Math.max(0, Math.min(100, percent)) / 100;
    return (
        <svg viewBox="0 0 20 20" className="h-5 w-5 shrink-0 -rotate-90" aria-hidden="true">
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
    sessionRunning = false,
    canStopRun = isLoading,
    onStop,
    selectedAgentName,
    shellClassName,
    reasoningEffortControl,
    reasoningEffort = "auto",
    onReasoningEffortChange,
    contextSessionRefs = [],
    onRemoveContextSessionRef,
    contextUsagePercent = null,
    supervisorRuntimeMode = "auto",
    onSupervisorRuntimeModeChange,
    onManualMemory,
    uploadScope,
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
    const [reasoningEffortOpen, setReasoningEffortOpen] = React.useState(false);
    const [supervisorRuntimeModeOpen, setSupervisorRuntimeModeOpen] = React.useState(false);
    const [safetyApprovalMode, setSafetyApprovalMode] = React.useState<SafetyApprovalMode>("reduced");
    const [safetyApprovalModeOpen, setSafetyApprovalModeOpen] = React.useState(false);
    const [files, setFiles] = React.useState<File[]>([]);
    const [uploadedSources, setUploadedSources] = React.useState<UploadedSourceDescriptor[]>([]);
    const [uploading, setUploading] = React.useState(false);
    const [isFileDragActive, setIsFileDragActive] = React.useState(false);
    const [isRecording, setIsRecording] = React.useState(false);
    const [isTranscribing, setIsTranscribing] = React.useState(false);
    const fileInputRef = React.useRef<HTMLInputElement>(null);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const inputMirrorRef = React.useRef<HTMLDivElement>(null);
    const reasoningEffortMenuRef = React.useRef<HTMLDivElement>(null);
    const [selectionRange, setSelectionRange] = React.useState({ start: input.length, end: input.length });
    const mediaStreamRef = React.useRef<MediaStream | null>(null);
    const audioContextRef = React.useRef<AudioContext | null>(null);
    const sourceNodeRef = React.useRef<MediaStreamAudioSourceNode | null>(null);
    const processorNodeRef = React.useRef<ScriptProcessorNode | null>(null);
    const muteGainRef = React.useRef<GainNode | null>(null);
    const audioChunksRef = React.useRef<Float32Array[]>([]);
    const sampleRateRef = React.useRef(16000);
    const inlineNoticeTimerRef = React.useRef<number | null>(null);
    const fileDragDepthRef = React.useRef(0);

    const composerReferences = React.useMemo<ComposerInlineReference[]>(() => [
        ...(selectedCommandPreset ? [{
            kind: "command" as const,
            id: `command:${selectedCommandPreset.name}`,
            label: selectedCommandPreset.name,
        }] : []),
        ...selectedSkills.map((skill) => ({
            kind: "skill" as const,
            id: `skill:${skill.path || skill.name}`,
            label: skill.name || skill.path || "skill",
        })),
        ...selectedSubagentFamilies.map((family) => ({
            kind: "subagent_family" as const,
            id: `subagent_family:${family.familyId}`,
            label: family.displayName || family.familyId,
        })),
        ...selectedPlugins.map((plugin) => ({
            kind: "plugin" as const,
            id: `plugin:${plugin.pluginId}`,
            label: plugin.displayName || plugin.pluginId,
        })),
    ], [selectedCommandPreset, selectedPlugins, selectedSkills, selectedSubagentFamilies]);
    const composerSegments = React.useMemo(
        () => buildComposerInlineSegments(input, composerReferences),
        [composerReferences, input],
    );

    const supervisorRuntimeModeOptions = React.useMemo(() => ([
        {
            mode: "auto" as const,
            title: t("web.chat.runtimeMode.auto.title"),
            description: t("web.chat.runtimeMode.auto.description"),
            icon: Sparkles,
        },
        {
            mode: "engineering" as const,
            title: t("web.chat.runtimeMode.engineering.title"),
            description: t("web.chat.runtimeMode.engineering.description"),
            icon: Code2,
        },
        {
            mode: "research" as const,
            title: t("web.chat.runtimeMode.research.title"),
            description: t("web.chat.runtimeMode.research.description"),
            icon: Search,
        },
        {
            mode: "creative_media" as const,
            title: t("web.chat.runtimeMode.creativeMedia.title"),
            description: t("web.chat.runtimeMode.creativeMedia.description"),
            icon: Palette,
        },
        {
            mode: "computer_use" as const,
            title: t("web.chat.runtimeMode.computerUse.title"),
            description: t("web.chat.runtimeMode.computerUse.description"),
            icon: MonitorCog,
        },
        {
            mode: "rpa" as const,
            title: t("web.chat.runtimeMode.rpa.title"),
            description: t("web.chat.runtimeMode.rpa.description"),
            icon: Workflow,
        },
    ]), [t]);
    const activeSupervisorRuntimeModeOption = supervisorRuntimeModeOptions.find((option) => option.mode === supervisorRuntimeMode)
        || supervisorRuntimeModeOptions[0]!;
    const ActiveSupervisorRuntimeModeIcon = activeSupervisorRuntimeModeOption.icon;

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
        const known = new Set<ReasoningEffortLevel>(["auto", "none", "minimal", "low", "medium", "high", "xhigh", "max"]);
        const levels = rawLevels
            .map((level) => String(level || "").trim().toLowerCase())
            .filter((level): level is ReasoningEffortLevel => known.has(level as ReasoningEffortLevel));
        return levels.includes("auto") ? levels : ["auto", ...levels];
    }, [reasoningEffortControl?.levels]);
    const reasoningEffortVisible = Boolean(reasoningEffortControl?.visible && reasoningEffortLevels.length > 1);
    React.useEffect(() => {
        if (!reasoningEffortOpen) return;

        const handlePointerDown = (event: PointerEvent) => {
            const target = event.target;
            if (target instanceof Node && !reasoningEffortMenuRef.current?.contains(target)) {
                setReasoningEffortOpen(false);
            }
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") setReasoningEffortOpen(false);
        };

        document.addEventListener("pointerdown", handlePointerDown);
        document.addEventListener("keydown", handleKeyDown);
        return () => {
            document.removeEventListener("pointerdown", handlePointerDown);
            document.removeEventListener("keydown", handleKeyDown);
        };
    }, [reasoningEffortOpen]);

    React.useEffect(() => {
        if (!reasoningEffortVisible) setReasoningEffortOpen(false);
    }, [reasoningEffortVisible]);
    const specCommandPresets = React.useMemo<CommandPresetSummary[]>(() => ([
        { name: "spec new", summary: t("web.composer.spec.new"), specCommandAction: "new" },
        { name: "spec continue", summary: t("web.composer.spec.continue"), specCommandAction: "continue" },
        { name: "spec list", summary: t("web.composer.spec.list"), specCommandAction: "list" },
        { name: "spec approve", summary: t("web.composer.spec.approve"), specCommandAction: "approve" },
        { name: "spec clarify", summary: t("web.composer.spec.clarify"), specCommandAction: "clarify" },
        { name: "spec analyze", summary: t("web.composer.spec.analyze"), specCommandAction: "analyze" },
        { name: "spec annex", summary: t("web.composer.spec.annex"), specCommandAction: "annex" },
    ]), [t]);
    const memoryCommand = React.useMemo<CommandPresetSummary>(() => ({
        name: "memory",
        summary: t("web.composer.memory.summary"),
        memoryAction: "session_extraction",
    }), [t]);
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

    const inlineQuery = React.useMemo(
        () => resolveComposerInlineQuery(input, selectionRange.end, Boolean(selectedCommandPreset), composerReferences),
        [composerReferences, input, selectedCommandPreset, selectionRange.end],
    );
    const slashQuery = inlineQuery?.kind === "command" ? inlineQuery.query.trim() : "";
    const skillQuery = inlineQuery?.kind === "mention" ? inlineQuery.query.trim() : "";
    const isCommandPickerOpen = inlineQuery?.kind === "command";
    const isSkillPickerOpen = inlineQuery?.kind === "mention";
    const filteredCommandPresets = React.useMemo(() => {
        const allCommands = [memoryCommand, ...(contextUsageCommand ? [contextUsageCommand] : []), ...specCommandPresets, ...commandPresets];
        if (!slashQuery) {
            return allCommands;
        }
        const keyword = slashQuery.toLowerCase();
        return allCommands.filter((preset) =>
            preset.name.toLowerCase().includes(keyword)
            || String(preset.summary || "").toLowerCase().includes(keyword)
            || String(preset.filename || "").toLowerCase().includes(keyword)
        );
    }, [commandPresets, contextUsageCommand, memoryCommand, slashQuery, specCommandPresets]);
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

    const reconcileSelectedReferences = React.useCallback((nextValue: string) => {
        if (selectedCommandPreset && !composerTextContainsReference(nextValue, {
            kind: "command",
            id: `command:${selectedCommandPreset.name}`,
            label: selectedCommandPreset.name,
        })) {
            setSelectedCommandPreset(null);
        }
        setSelectedSkills((current) => current.filter((skill) => composerTextContainsReference(nextValue, {
            kind: "skill",
            id: `skill:${skill.path || skill.name}`,
            label: skill.name || skill.path || "skill",
        })));
        setSelectedSubagentFamilies((current) => current.filter((family) => composerTextContainsReference(nextValue, {
            kind: "subagent_family",
            id: `subagent_family:${family.familyId}`,
            label: family.displayName || family.familyId,
        })));
        setSelectedPlugins((current) => current.filter((plugin) => composerTextContainsReference(nextValue, {
            kind: "plugin",
            id: `plugin:${plugin.pluginId}`,
            label: plugin.displayName || plugin.pluginId,
        })));
    }, [selectedCommandPreset]);

    const setInputAndCaret = React.useCallback((nextValue: string, caret: number) => {
        updateInputValue(nextValue);
        const nextCaret = Math.max(0, Math.min(nextValue.length, caret));
        setSelectionRange({ start: nextCaret, end: nextCaret });
        if (typeof window !== "undefined") {
            window.requestAnimationFrame(() => {
                textareaRef.current?.focus();
                textareaRef.current?.setSelectionRange(nextCaret, nextCaret);
            });
        }
    }, [updateInputValue]);

    const handleComposerInputChange = React.useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
        const nextValue = event.target.value;
        const start = event.target.selectionStart ?? nextValue.length;
        const end = event.target.selectionEnd ?? start;
        handleInputChange(event);
        setSelectionRange({ start, end });
        reconcileSelectedReferences(nextValue);
    }, [handleInputChange, reconcileSelectedReferences]);

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
                throw new Error(typeof pluginPayload?.error === "string" ? pluginPayload.error : t("web.composer.pluginLoadFailed"));
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
            setReasoningEffortOpen(false);
        }
    }, [reasoningEffort, reasoningEffortLevels, reasoningEffortVisible]);

    const selectCommandPreset = React.useCallback((preset: CommandPresetSummary) => {
        if (!inlineQuery || inlineQuery.kind !== "command") return;
        if (preset.readOnlyKind === "context_usage") {
            setInputAndCaret(`${input.slice(0, inlineQuery.start)}${input.slice(inlineQuery.end)}`, inlineQuery.start);
            dismissInlineNotice();
            return;
        }
        const reference: ComposerInlineReference = {
            kind: "command",
            id: `command:${preset.name}`,
            label: preset.name,
        };
        const inserted = insertComposerReference(input, inlineQuery, reference);
        setSelectedCommandPreset(preset);
        setInputAndCaret(inserted.text, inserted.caret);
        dismissInlineNotice();
    }, [dismissInlineNotice, inlineQuery, input, setInputAndCaret]);

    const selectSkillReference = React.useCallback((skill: SkillReferenceSummary) => {
        if (!inlineQuery || inlineQuery.kind !== "mention") return;
        const reference: ComposerInlineReference = {
            kind: "skill",
            id: `skill:${skill.path || skill.name}`,
            label: skill.name || skill.path || "skill",
        };
        const inserted = insertComposerReference(input, inlineQuery, reference);
        setSelectedSkills((current) => {
            const alreadySelected = current.some((item) => item.name === skill.name && (item.path || "") === (skill.path || ""));
            if (alreadySelected) {
                return current;
            }
            return [...current, skill];
        });
        setInputAndCaret(inserted.text, inserted.caret);
        dismissInlineNotice();
    }, [dismissInlineNotice, inlineQuery, input, setInputAndCaret]);

    const selectSubagentFamilyReference = React.useCallback((family: SubagentFamilySummary) => {
        if (!inlineQuery || inlineQuery.kind !== "mention") return;
        const reference: ComposerInlineReference = {
            kind: "subagent_family",
            id: `subagent_family:${family.familyId}`,
            label: family.displayName || family.familyId,
        };
        const inserted = insertComposerReference(input, inlineQuery, reference);
        setSelectedSubagentFamilies((current) => {
            const alreadySelected = current.some((item) => item.familyId === family.familyId);
            if (alreadySelected) {
                return current;
            }
            return [...current, family];
        });
        setInputAndCaret(inserted.text, inserted.caret);
        dismissInlineNotice();
    }, [dismissInlineNotice, inlineQuery, input, setInputAndCaret]);

    const selectPluginReference = React.useCallback((plugin: PluginReferenceSummary) => {
        if (!inlineQuery || inlineQuery.kind !== "mention") return;
        const reference: ComposerInlineReference = {
            kind: "plugin",
            id: `plugin:${plugin.pluginId}`,
            label: plugin.displayName || plugin.pluginId,
        };
        const inserted = insertComposerReference(input, inlineQuery, reference);
        setSelectedPlugins((current) => current.some((item) => item.pluginId === plugin.pluginId)
            ? current
            : [...current, { ...plugin, grantScope: "task" }]);
        setInputAndCaret(inserted.text, inserted.caret);
        dismissInlineNotice();
    }, [dismissInlineNotice, inlineQuery, input, setInputAndCaret]);

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

    const hasReferenceTokens = contextSessionRefs.length > 0;

    const removeLastReferenceToken = React.useCallback(() => {
        const lastContextRef = contextSessionRefs[contextSessionRefs.length - 1];
        if (lastContextRef && onRemoveContextSessionRef) {
            onRemoveContextSessionRef(lastContextRef.sessionId);
            return true;
        }
        return false;
    }, [contextSessionRefs, onRemoveContextSessionRef]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Backspace") {
            const deletion = removeComposerReferenceAtBackspace(
                input,
                composerReferences,
                e.currentTarget.selectionStart,
                e.currentTarget.selectionEnd,
            );
            if (deletion) {
                e.preventDefault();
                reconcileSelectedReferences(deletion.text);
                setInputAndCaret(deletion.text, deletion.caret);
                return;
            }
            if (input.length === 0 && !isCommandPickerOpen && !isSkillPickerOpen && removeLastReferenceToken()) {
                e.preventDefault();
                return;
            }
        }
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
            if (uploading) {
                showInlineNotice("info", t("web.chat.attachments.uploading"));
                return;
            }
            if (form && !isLoading) form.requestSubmit();
        }
    };

    const uploadFiles = React.useCallback(async (newFiles: File[]) => {
        if (newFiles.length === 0 || uploading) return;
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
                appendUploadScope(formData, uploadScope, "web_upload");
                const res = await fetch(`/api/upload`, { method: 'POST', body: formData });
                if (!res.ok) throw new Error("Failed to upload file");
                return await res.json() as UploadedSourceDescriptor;
            });
            const uploaded = await Promise.all(uploadPromises);
            setUploadedSources((prev) => [...prev, ...uploaded]);
        } catch (error) {
            console.error('Upload failed:', error);
            showInlineNotice("error", t("web.generated.a0608c519e"));
        } finally {
            setUploading(false);
        }
    }, [files.length, showInlineNotice, t, uploadScope, uploading]);

    const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFiles = Array.from(event.target.files || []);
        event.target.value = "";
        void uploadFiles(selectedFiles);
    };

    const hasDraggedFiles = React.useCallback((event: React.DragEvent<HTMLFormElement>) => (
        Array.from(event.dataTransfer.types || []).includes("Files")
    ), []);

    const resetFileDragState = React.useCallback(() => {
        fileDragDepthRef.current = 0;
        setIsFileDragActive(false);
    }, []);

    const handleFileDragEnter = (event: React.DragEvent<HTMLFormElement>) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        fileDragDepthRef.current += 1;
        event.dataTransfer.dropEffect = "copy";
        setIsFileDragActive(true);
    };

    const handleFileDragOver = (event: React.DragEvent<HTMLFormElement>) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "copy";
    };

    const handleFileDragLeave = (event: React.DragEvent<HTMLFormElement>) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        fileDragDepthRef.current = Math.max(0, fileDragDepthRef.current - 1);
        if (fileDragDepthRef.current === 0) setIsFileDragActive(false);
    };

    const handleFileDrop = (event: React.DragEvent<HTMLFormElement>) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        const droppedFiles = Array.from(event.dataTransfer.files || []);
        resetFileDragState();
        void uploadFiles(droppedFiles);
    };

    const removeFile = (index: number) => {
        setFiles((prev) => prev.filter((_, i) => i !== index));
        setUploadedSources((prev) => prev.filter((_, i) => i !== index));
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
        appendUploadScope(formData, uploadScope, "web_voice");
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
                id: payload?.id,
                sourceId: payload?.sourceId || payload?.id,
                sourceKind: payload?.sourceKind || "web_voice",
                resourceRole: "source",
                url,
                publicUrl: String(payload?.publicUrl || url),
                previewUrl: String(payload?.previewUrl || payload?.publicUrl || url),
                name: String(payload?.name || file.name),
                mimeType: String(payload?.type || file.type),
                size: Number(payload?.size || file.size),
                mediaKind: "audio",
                workspacePath: payload?.workspacePath,
                workspaceRelativePath: payload?.workspaceRelativePath,
                resourceRef: payload?.resourceRef,
                source: "os_web_voice_upload",
            }],
            supervisorRuntimeMode,
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
    }, [onVoiceAudioMessage, safetyApprovalMode, showInlineNotice, supervisorRuntimeMode, t, uploadScope]);

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
    const runActive = sessionRunning || isLoading;
    const canQueueWhileRunning = runActive && canSubmit;
    const canStopActiveRun = runActive && !canQueueWhileRunning && canStopRun && Boolean(onStop);
    const showRunBusy = runActive && !canQueueWhileRunning && !canStopActiveRun;

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
            data-file-drop-active={isFileDragActive ? "true" : "false"}
            onDragEnter={handleFileDragEnter}
            onDragOver={handleFileDragOver}
            onDragLeave={handleFileDragLeave}
            onDrop={handleFileDrop}
            onDragEnd={resetFileDragState}
            onSubmit={async (e) => {
                if (uploading) {
                    e.preventDefault();
                    showInlineNotice("info", t("web.chat.attachments.uploading"));
                    return;
                }
                if (selectedCommandPreset?.memoryAction === "session_extraction") {
                    e.preventDefault();
                    if (!onManualMemory) {
                        showInlineNotice("error", t("web.composer.memory.failed"));
                        return;
                    }
                    const result = await onManualMemory();
                    showInlineNotice(result.accepted ? "success" : "error", result.message);
                    if (!result.accepted) return;
                    updateInputValue("");
                    setSelectionRange({ start: 0, end: 0 });
                    setSelectedCommandPreset(null);
                    return;
                }
                const nextData: Record<string, unknown> = {};
                const pendingSpecMode = specModeEnabled;
                nextData.supervisorRuntimeMode = supervisorRuntimeMode;
                nextData.safetyApprovalMode = safetyApprovalMode;
                if (contextSessionRefs.length > 0) {
                    nextData.contextSessionRefs = contextSessionRefs;
                }
                if (uploadedSources.length > 0) {
                    nextData.fileUrls = uploadedSources
                        .map((item) => String(item.url || item.publicUrl || "").trim())
                        .filter(Boolean);
                    nextData.attachments = uploadedSources.map((item, index) => ({
                        id: item.id,
                        sourceId: item.sourceId || item.id,
                        sourceKind: item.sourceKind || "web_upload",
                        resourceRole: "source",
                        url: item.url || item.publicUrl,
                        publicUrl: item.publicUrl || item.url,
                        previewUrl: item.previewUrl || item.publicUrl || item.url,
                        name: item.name || files[index]?.name || undefined,
                        mimeType: item.type || files[index]?.type || undefined,
                        mediaKind: item.type?.split("/", 1)[0] || undefined,
                        size: item.size ?? (typeof files[index]?.size === "number" ? files[index]?.size : undefined),
                        workspacePath: item.workspacePath,
                        workspaceRelativePath: item.workspaceRelativePath,
                        resourceRef: item.resourceRef,
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
                if (pendingSpecMode) {
                    nextData.specMode = true;
                }
                if (composerReferences.length > 0) {
                    const plainMessage = stripComposerReferences(input, composerReferences);
                    nextData.messageOverride = plainMessage || input.trim();
                    nextData.composerPresentation = {
                        text: input,
                        references: composerReferences,
                    };
                }

                const accepted = await handleSubmit(e, { data: nextData });
                if (accepted === false) return;
                updateInputValue("");
                setSelectionRange({ start: 0, end: 0 });
                setFiles([]);
                setUploadedSources([]);
                setSelectedCommandPreset(null);
                setSelectedSkills([]);
                setSelectedSubagentFamilies([]);
                setSelectedPlugins([]);
                if (pendingSpecMode) {
                    setSpecModeEnabled(false);
                }
            }}
            className={cn(
                "relative mx-auto flex w-full flex-col overflow-visible rounded-[1.25rem] border shadow-sm transition-all duration-500",
                shellClassName ?? "max-w-4xl",
                isFocused 
                    ? "bg-stone-50/95 dark:bg-stone-900/90 shadow-[0_8px_32px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_32px_rgba(0,0,0,0.3)] border-orange-500/30 dark:border-amber-500/30" 
                    : "bg-stone-50/60 dark:bg-stone-900/50 backdrop-blur-xl hover:bg-stone-50/80 hover:dark:bg-stone-900/70 border-stone-200/60 dark:border-stone-800/60",
                specModeEnabled && "border-red-500/55 shadow-[0_0_0_1px_rgba(239,68,68,0.26),0_0_28px_rgba(248,113,113,0.24)] animate-pulse",
                isFileDragActive && "border-primary/70 bg-primary/[0.04] ring-2 ring-primary/20",
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
                <div className="flex min-h-[44px] w-full flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2">
                        {contextSessionRefs.map((reference) => (
                            <div
                                key={reference.sessionId}
                                className="group inline-flex h-6 max-w-full items-center gap-1 text-[14px] font-semibold leading-6 text-cyan-700 dark:text-cyan-300 sm:text-[15px]"
                                title={reference.sessionId}
                            >
                                <CornerDownRight className="h-4 w-4 shrink-0" />
                                <span className="truncate">{t("web.chat.contextSessionRef")} · {reference.sessionId.slice(0, 12)}</span>
                                <button
                                    type="button"
                                    onClick={() => onRemoveContextSessionRef?.(reference.sessionId)}
                                    className="grid h-4 w-4 shrink-0 place-items-center rounded text-current/60 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                                    aria-label={t("web.chat.removeContextSessionRef")}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                    <div className={cn("relative min-h-[26px] min-w-[10rem] flex-[1_1_12rem]", !hasReferenceTokens && "min-h-[32px]")}>
                        <div
                            ref={inputMirrorRef}
                            aria-hidden="true"
                            className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words text-[14px] leading-6 text-foreground sm:text-[15px]"
                        >
                            {composerSegments.map((segment, index) => (
                                <span
                                    key={`${segment.start}:${segment.end}:${index}`}
                                    className={segment.type === "reference"
                                        ? segment.reference?.kind === "command"
                                            ? "text-violet-600 dark:text-violet-300"
                                            : "text-orange-600 dark:text-orange-300"
                                        : undefined}
                                >
                                    {segment.text}
                                </span>
                            ))}
                            {input.endsWith("\n") ? <br /> : null}
                        </div>
                        <Textarea
                            data-v8os-chat-composer="true"
                            ref={textareaRef}
                            value={input}
                            onChange={handleComposerInputChange}
                            onSelect={(event) => setSelectionRange({
                                start: event.currentTarget.selectionStart,
                                end: event.currentTarget.selectionEnd,
                            })}
                            onScroll={(event) => {
                                if (inputMirrorRef.current) inputMirrorRef.current.scrollTop = event.currentTarget.scrollTop;
                            }}
                            onKeyDown={handleKeyDown}
                            onFocus={() => setIsFocused(true)}
                            onBlur={() => setIsFocused(false)}
                            placeholder={selectedAgentName ? t("web.generated.04cdce87c7", { value0: selectedAgentName }) : t("web.generated.a706426e02")}
                            spellCheck={false}
                            className="custom-scrollbar relative z-10 min-h-[26px] max-h-[172px] w-full resize-none overflow-y-auto border-none bg-transparent px-0 py-0 text-[14px] leading-6 text-transparent caret-foreground placeholder:text-muted-foreground/50 shadow-none selection:bg-violet-300/30 focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 sm:text-[15px]"
                        />
                    </div>
                </div>

                {isCommandPickerOpen && (
                    <div role="listbox" aria-label="Commands" className="absolute inset-x-2 bottom-full z-[80] mb-2 overflow-hidden rounded-xl border border-border/70 bg-popover/98 p-1.5 shadow-[0_18px_48px_rgba(15,23,42,0.18)] backdrop-blur-xl dark:shadow-[0_18px_48px_rgba(0,0,0,0.45)]">
                        <div className="custom-scrollbar max-h-56 overflow-y-auto">
                            {commandsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t("web.generated.8a3ecc1a85")}</div>
                            ) : filteredCommandPresets.length > 0 ? (
                                filteredCommandPresets.map((preset, index) => (
                                    <button
                                        key={preset.name}
                                        type="button"
                                        role="option"
                                        aria-selected={index === 0}
                                        onMouseDown={(event) => event.preventDefault()}
                                        onClick={() => selectCommandPreset(preset)}
                                        className={cn(
                                            "grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-lg px-2.5 py-2 text-left",
                                            index === 0 ? "bg-muted/80" : "hover:bg-muted/55"
                                        )}
                                    >
                                        {preset.readOnlyKind === "context_usage" && typeof preset.usagePercent === "number"
                                            ? <ContextUsageRing percent={preset.usagePercent} />
                                            : <Command className="h-4 w-4 shrink-0 text-primary" />}
                                        <span className="flex min-w-0 items-baseline gap-2">
                                            <span className="max-w-[48%] shrink-0 truncate text-sm font-semibold text-foreground">{preset.name}</span>
                                            {preset.summary ? <span className="truncate text-xs text-muted-foreground">{preset.summary}</span> : null}
                                        </span>
                                        <span className="text-[10px] font-medium text-muted-foreground">
                                            {preset.readOnlyKind === "context_usage" && typeof preset.usagePercent === "number" ? `${preset.usagePercent}%` : "Command"}
                                        </span>
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
                    <div role="listbox" aria-label="Mentions" className="absolute inset-x-2 bottom-full z-[80] mb-2 overflow-hidden rounded-xl border border-border/70 bg-popover/98 p-1.5 shadow-[0_18px_48px_rgba(15,23,42,0.18)] backdrop-blur-xl dark:shadow-[0_18px_48px_rgba(0,0,0,0.45)]">
                        <div className="custom-scrollbar max-h-56 overflow-y-auto">
                            {skillsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t("web.generated.2aeb46969c")}</div>
                            ) : filteredMentionItems.length > 0 ? (
                                filteredMentionItems.map((item, index) => {
                                    const title = item.kind === "skill" ? item.skill.name : item.kind === "subagent_family" ? (item.family.displayName || item.family.familyId) : item.plugin.displayName;
                                    const summary = item.kind === "skill" ? item.skill.description : item.kind === "subagent_family" ? item.family.description : item.plugin.description;
                                    const meta = item.kind === "skill"
                                        ? "Skill"
                                        : item.kind === "subagent_family"
                                            ? "Agent"
                                            : `Plugin · ${item.plugin.status === "ready" ? "Ready" : "Setup"}`;
                                    return (
                                        <button
                                            key={item.key}
                                            type="button"
                                            role="option"
                                            aria-selected={index === 0}
                                            onMouseDown={(event) => event.preventDefault()}
                                            onClick={() => selectMentionItem(item)}
                                            className={cn(
                                                "grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-lg px-2.5 py-2 text-left",
                                                index === 0 ? "bg-muted/80" : "hover:bg-muted/55"
                                            )}
                                        >
                                            {item.kind === "plugin" ? (
                                                <span className={cn("size-2 shrink-0 rounded-full", item.plugin.status === "ready" ? "bg-emerald-500" : "bg-amber-500")} />
                                            ) : (
                                                <AtSign className="h-4 w-4 shrink-0 text-primary" />
                                            )}
                                            <span className="flex min-w-0 items-baseline gap-2">
                                                <span className="max-w-[48%] shrink-0 truncate text-sm font-semibold text-foreground">{title}</span>
                                                {summary ? <span className="truncate text-xs text-muted-foreground">{summary}</span> : null}
                                            </span>
                                            <span className="text-[10px] font-medium text-muted-foreground">{meta}</span>
                                        </button>
                                    );
                                })
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
                        <DropdownMenu open={supervisorRuntimeModeOpen} onOpenChange={setSupervisorRuntimeModeOpen}>
                            <DropdownMenuTrigger asChild>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    aria-label={activeSupervisorRuntimeModeOption.title}
                                    title={activeSupervisorRuntimeModeOption.title}
                                    className={cn(
                                        "h-[28px] w-[28px] rounded-lg transition-colors",
                                        supervisorRuntimeModeOpen || supervisorRuntimeMode !== "auto"
                                            ? "bg-violet-500/12 text-violet-700 shadow-[0_0_16px_rgba(139,92,246,0.2)] hover:bg-violet-500/18 dark:bg-violet-500/15 dark:text-violet-200"
                                            : "text-muted-foreground hover:bg-zinc-100/50 hover:text-foreground dark:hover:bg-zinc-800/50"
                                    )}
                                >
                                    <ActiveSupervisorRuntimeModeIcon className="h-4 w-4" />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                side="top"
                                align="start"
                                sideOffset={8}
                                collisionPadding={12}
                                className="z-[90] max-h-[min(65vh,420px)] w-72 overflow-y-auto rounded-2xl border-zinc-200/70 bg-white/95 p-1.5 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-zinc-700/70 dark:bg-zinc-950/95"
                            >
                                <DropdownMenuRadioGroup
                                    value={supervisorRuntimeMode}
                                    onValueChange={(value) => void onSupervisorRuntimeModeChange?.(value as SupervisorRuntimeMode)}
                                >
                                    {supervisorRuntimeModeOptions.map((option) => {
                                        const active = option.mode === supervisorRuntimeMode;
                                        const RuntimeModeIcon = option.icon;
                                        return (
                                            <DropdownMenuRadioItem
                                                key={option.mode}
                                                value={option.mode}
                                                className={cn(
                                                    "cursor-pointer items-start gap-2 rounded-xl py-2 pl-8 pr-2.5 text-left",
                                                    active
                                                        ? "bg-violet-500/10 text-violet-800 focus:bg-violet-500/15 dark:bg-violet-400/12 dark:text-violet-100"
                                                        : "text-zinc-600 focus:bg-zinc-100/80 dark:text-zinc-300 dark:focus:bg-zinc-800/80"
                                                )}
                                            >
                                                <RuntimeModeIcon className={cn("mt-0.5 h-4 w-4 shrink-0", active ? "text-violet-600 dark:text-violet-300" : "text-muted-foreground")} />
                                                <span className="min-w-0 flex-1">
                                                    <span className="block text-[12px] font-semibold">{option.title}</span>
                                                    <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">{option.description}</span>
                                                </span>
                                            </DropdownMenuRadioItem>
                                        );
                                    })}
                                </DropdownMenuRadioGroup>
                            </DropdownMenuContent>
                        </DropdownMenu>
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
                            <div ref={reasoningEffortMenuRef} className="relative">
                                <button
                                    type="button"
                                    onClick={() => setReasoningEffortOpen((current) => !current)}
                                    className={cn(
                                        "group inline-flex h-[28px] w-[28px] items-center justify-center rounded-full border p-0 transition-[color,background-color,border-color,transform,box-shadow] duration-160 active:scale-[0.92]",
                                        reasoningEffort === "auto"
                                            ? "border-zinc-200/70 bg-white/55 text-zinc-500 hover:border-violet-300/70 hover:text-zinc-800 dark:border-zinc-700/60 dark:bg-zinc-900/40 dark:text-zinc-400 dark:hover:border-violet-400/45 dark:hover:text-zinc-100"
                                            : "border-violet-400/70 bg-violet-100/70 text-violet-800 shadow-[0_4px_18px_rgba(124,58,237,0.16)] hover:bg-violet-100 dark:border-violet-400/45 dark:bg-violet-500/14 dark:text-violet-100"
                                    )}
                                    title={t("web.generated.9d1e18be56")}
                                    aria-label={t("web.generated.9d1e18be56")}
                                    aria-expanded={reasoningEffortOpen}
                                >
                                    <Gauge className="h-4 w-4" aria-hidden="true" />
                                </button>
                                {reasoningEffortOpen ? (
                                    <div className="absolute bottom-full left-0 z-50 mb-2 w-[min(316px,calc(100vw-24px))]">
                                        <ReasoningEffortControl
                                            levels={reasoningEffortLevels}
                                            value={reasoningEffort}
                                            label={t("web.chat.reasoningEffort.label")}
                                            helpLabel={t("web.generated.9d1e18be56")}
                                            ariaLabel={t("web.generated.9d1e18be56")}
                                            labelFormatter={(level) => String(level || "auto").trim().toLowerCase()}
                                            onValueCommit={(level) => onReasoningEffortChange?.(level as ReasoningEffortLevel)}
                                        />
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
                        type={canStopActiveRun || showRunBusy ? "button" : "submit"}
                        size="icon"
                        className={cn(
                            "h-[32px] w-[32px] rounded-xl shadow-sm transition-all duration-300",
                            runActive && !canQueueWhileRunning
                                ? "bg-stone-200 text-stone-400 dark:bg-stone-800 dark:text-stone-500"
                                : (canSubmit
                                    ? "bg-gradient-to-br from-orange-400 to-amber-600 dark:from-orange-500 dark:to-amber-700 text-white shadow-[0_0_12px_rgba(249,115,22,0.4)] hover:shadow-[0_0_16px_rgba(249,115,22,0.6)] hover:scale-105 border border-orange-300/50 dark:border-amber-500/50"
                                    : "bg-stone-100 text-stone-300 dark:bg-stone-800/60 dark:text-stone-600 cursor-not-allowed")
                        )}
                        disabled={uploading || showRunBusy || (!runActive && !canSubmit)}
                        onClick={(e) => {
                            if (canStopActiveRun && onStop) {
                                e.preventDefault();
                                onStop();
                            }
                        }}
                    >
                        {runActive && !canQueueWhileRunning ? (
                            <div className="relative flex items-center justify-center w-full h-full group">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                {canStopActiveRun ? (
                                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-100 dark:bg-zinc-800 rounded-xl">
                                        <Square className="h-3 w-3 fill-destructive text-destructive" />
                                    </div>
                                ) : null}
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
