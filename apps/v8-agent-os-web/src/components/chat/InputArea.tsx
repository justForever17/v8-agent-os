"use client";
/* eslint-disable @next/next/no-img-element */

import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Paperclip, Send, Mic, Loader2, Square, X, PlayCircle, AlertCircle, CheckCircle2, Info, Command, FileText, AtSign, Gauge } from "lucide-react";
import { ChangeEvent, FormEvent } from "react";
import { MediaViewerLightbox, MediaItem } from "./MediaViewerLightbox";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

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
    handleSubmit: (e: FormEvent<HTMLFormElement>, options?: { data?: any }) => void | Promise<void>;
    onVoiceTranscript?: (text: string) => void;
    onVoiceAudioMessage?: (data: VoiceAudioMessageData) => void | Promise<void>;
    isLoading: boolean;
    onStop?: () => void;
    selectedAgentName?: string;
    shellClassName?: string;
    reasoningEffortControl?: ReasoningEffortControl | null;
}

type ReasoningEffortLevel = "auto" | "low" | "medium" | "high";

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
    | { kind: "subagent_family"; key: string; family: SubagentFamilySummary };

function isSkillReferenceSummaryCandidate(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isSubagentFamilyCandidate(value: unknown): value is Record<string, unknown> {
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
}: InputAreaProps) {
    const t = useT();
    const [commandPresets, setCommandPresets] = React.useState<CommandPresetSummary[]>([]);
    const [commandsLoaded, setCommandsLoaded] = React.useState(false);
    const [commandsLoading, setCommandsLoading] = React.useState(false);
    const [selectedCommandPreset, setSelectedCommandPreset] = React.useState<CommandPresetSummary | null>(null);
    const [skills, setSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [subagentFamilies, setSubagentFamilies] = React.useState<SubagentFamilySummary[]>([]);
    const [skillsLoaded, setSkillsLoaded] = React.useState(false);
    const [skillsLoading, setSkillsLoading] = React.useState(false);
    const [selectedSkills, setSelectedSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [selectedSubagentFamilies, setSelectedSubagentFamilies] = React.useState<SubagentFamilySummary[]>([]);
    const [taskPlanningMode, setTaskPlanningMode] = React.useState(false);
    const [reasoningEffort, setReasoningEffort] = React.useState<ReasoningEffortLevel>("auto");
    const [reasoningEffortOpen, setReasoningEffortOpen] = React.useState(false);
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
            auto: t(lt("自", "Auto")),
            low: t(lt("低", "Low")),
            medium: t(lt("中", "Med")),
            high: t(lt("高", "High")),
        };
        return labels[reasoningEffort] || labels.auto;
    }, [reasoningEffort, t]);

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
        if (!slashQuery) {
            return commandPresets;
        }
        const keyword = slashQuery.toLowerCase();
        return commandPresets.filter((preset) =>
            preset.name.toLowerCase().includes(keyword)
            || String(preset.summary || "").toLowerCase().includes(keyword)
            || String(preset.filename || "").toLowerCase().includes(keyword)
        );
    }, [commandPresets, slashQuery]);
    const filteredMentionItems = React.useMemo<MentionPickerItem[]>(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}::${skill.path || ""}`));
        const selectedFamilyIds = new Set(selectedSubagentFamilies.map((family) => family.familyId));
        const base: MentionPickerItem[] = [
            ...skills
                .filter((skill) => !selectedKeys.has(`${skill.name}::${skill.path || ""}`))
                .map((skill) => ({ kind: "skill" as const, key: `skill:${skill.name}:${skill.path || ""}`, skill })),
            ...subagentFamilies
                .filter((family) => family.familyId && !selectedFamilyIds.has(family.familyId))
                .map((family) => ({ kind: "subagent_family" as const, key: `family:${family.familyId}`, family })),
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
                : (
                    item.family.familyId.toLowerCase().includes(keyword)
                    || String(item.family.displayName || "").toLowerCase().includes(keyword)
                    || String(item.family.description || "").toLowerCase().includes(keyword)
                    || (item.family.aliases || []).some((alias) => String(alias || "").toLowerCase().includes(keyword))
                )
        );
    }, [selectedSkills, selectedSubagentFamilies, skillQuery, skills, subagentFamilies]);

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
                        : t(lt("命令预设列表加载失败", "Failed to load command presets"))
                );
            }
            setCommandPresets(Array.isArray(payload?.items) ? payload.items : []);
            setCommandsLoaded(true);
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("命令预设列表加载失败", "Failed to load command presets"));
            showInlineNotice("error", message);
        } finally {
            setCommandsLoading(false);
        }
    }, [commandsLoaded, commandsLoading, showInlineNotice, t]);

    const loadSkills = React.useCallback(async () => {
        if (skillsLoaded || skillsLoading) return;
        setSkillsLoading(true);
        try {
            const res = await fetch("/api/skills/list", { cache: "no-store" });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    typeof payload?.error === "string"
                        ? payload.error
                        : t(lt("技能列表加载失败", "Failed to load skill list"))
                );
            }
            const nextSkills: unknown[] = Array.isArray(payload?.skills) ? payload.skills : [];
            const nextFamilies: unknown[] = Array.isArray(payload?.subagentFamilies) ? payload.subagentFamilies : [];
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
            setSkillsLoaded(true);
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("技能列表加载失败", "Failed to load skill list"));
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

    const selectMentionItem = React.useCallback((item: MentionPickerItem) => {
        if (item.kind === "skill") {
            selectSkillReference(item.skill);
            return;
        }
        selectSubagentFamilyReference(item.family);
    }, [selectSkillReference, selectSubagentFamilyReference]);

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
                showInlineNotice("error", t(lt("最多只能上传 14 个文件。", "You can upload up to 14 files.")));
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
                showInlineNotice("error", t(lt("文件上传失败，请稍后重试。", "Upload failed. Please try again.")));
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
                    : t(lt("语音输入状态检查失败", "Failed to check voice input status"))
            );
        }
        return payload;
    }, [t]);

    const uploadAndSendVoiceAudio = React.useCallback(async (blob: Blob) => {
        if (!onVoiceAudioMessage) {
            throw new Error(t(lt("当前页面不支持直接发送语音文件。", "This page cannot send voice files directly.")));
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
                    : t(lt("语音文件上传失败", "Voice file upload failed"))
            );
        }
        const url = String(payload?.url || payload?.publicUrl || "").trim();
        if (!url) {
            throw new Error(t(lt("语音文件上传后没有返回可用链接。", "Voice upload did not return a usable URL.")));
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
        };
        try {
            const result = onVoiceAudioMessage(messageData);
            void Promise.resolve(result).catch((error) => {
                const message = error instanceof Error ? error.message : t(lt("语音发送失败", "Voice send failed"));
                showInlineNotice("error", t(lt(`语音暂不可用：${message}`, `Voice is unavailable: ${message}`)));
            });
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("语音发送失败", "Voice send failed"));
            showInlineNotice("error", t(lt(`语音暂不可用：${message}`, `Voice is unavailable: ${message}`)));
        }
    }, [onVoiceAudioMessage, showInlineNotice, t]);

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
                    || t(lt("未配置可用 STT，也没有可读取音频的视觉模型。", "No usable STT or audio-capable vision model is configured."))
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
                            : t(lt("语音转写失败", "Voice transcription failed"))
                );
            }

            const text = typeof payload?.text === "string" ? payload.text.trim() : "";
            if (!text) {
                throw new Error(t(lt("未识别到语音内容", "No speech was detected")));
            }

            onVoiceTranscript?.(text);
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("语音转写失败", "Voice transcription failed"));
            showInlineNotice("error", t(lt(`语音暂不可用：${message}`, `Voice is unavailable: ${message}`)));
        } finally {
            setIsTranscribing(false);
        }
    }, [getAudioInputStatus, onVoiceTranscript, showInlineNotice, t, uploadAndSendVoiceAudio]);

    const startRecording = React.useCallback(async () => {
        if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
            showInlineNotice("error", t(lt("当前浏览器不支持录音，请更换支持麦克风采集的浏览器。", "This browser cannot record audio. Please switch to one that supports microphone capture.")));
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const AudioContextCtor =
                window.AudioContext ||
                (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
            if (!AudioContextCtor) {
                stream.getTracks().forEach((track) => track.stop());
                showInlineNotice("error", t(lt("当前浏览器不支持 Web Audio，无法进行语音采集。", "This browser does not support Web Audio, so voice capture is unavailable.")));
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
            showInlineNotice("info", t(lt("录音中，再点一次麦克风结束并转写。", "Recording. Tap the mic again to stop and transcribe.")), 0);
        } catch (error) {
            stopRecordingTracks();
            const message = error instanceof Error ? error.message : t(lt("无法访问麦克风", "Microphone access is unavailable"));
            showInlineNotice("error", t(lt(`无法开始录音：${message}`, `Could not start recording: ${message}`)));
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
            showInlineNotice("error", t(lt("未采集到有效音频。", "No valid audio was captured.")));
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
        || selectedSubagentFamilies.length > 0;

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
                const pendingTaskPlanningMode = taskPlanningMode;
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
                    nextData.commandPreset = { name: selectedCommandPreset.name };
                }
                if (selectedSkills.length > 0) {
                    nextData.skillReferences = selectedSkills.map((skill) => ({
                        name: skill.name,
                        description: skill.description || "",
                        path: skill.path || "",
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
                if (pendingTaskPlanningMode) {
                    nextData.plannerMode = "off";
                    nextData.specMode = true;
                }
                if (pendingTaskPlanningMode) {
                    setTaskPlanningMode(false);
                }

                await handleSubmit(e, { data: nextData });
                setFiles([]);
                setUploadedUrls([]);
                setSelectedCommandPreset(null);
                setSelectedSkills([]);
                setSelectedSubagentFamilies([]);
            }}
            className={cn(
                "relative mx-auto flex w-full flex-col overflow-hidden rounded-[1.25rem] border shadow-sm transition-all duration-500",
                shellClassName ?? "max-w-4xl",
                isFocused 
                    ? "bg-stone-50/95 dark:bg-stone-900/90 shadow-[0_8px_32px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_32px_rgba(0,0,0,0.3)] border-orange-500/30 dark:border-amber-500/30" 
                    : "bg-stone-50/60 dark:bg-stone-900/50 backdrop-blur-xl hover:bg-stone-50/80 hover:dark:bg-stone-900/70 border-stone-200/60 dark:border-stone-800/60",
                taskPlanningMode && "border-red-500/55 shadow-[0_0_0_1px_rgba(239,68,68,0.26),0_0_28px_rgba(248,113,113,0.24)] animate-pulse"
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
                {(selectedCommandPreset || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0) && (
                    <div className="flex min-h-[28px] flex-wrap items-center gap-1 px-2.5 pt-1.5">
                        {selectedCommandPreset && (
                            <div className="inline-flex max-w-full items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[10px] font-medium text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200 sm:text-[11px]">
                                <Command className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span className="truncate">{t(lt("命令", "Preset"))}：{selectedCommandPreset.name}</span>
                                <button
                                    type="button"
                                    onClick={() => setSelectedCommandPreset(null)}
                                    className="rounded-full text-current/70 transition hover:text-current"
                                    aria-label={t(lt("移除命令预设", "Remove preset"))}
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
                                    aria-label={t(lt("移除技能引用", "Remove skill reference"))}
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
                                    aria-label={t(lt("移除专家族引用", "Remove subagent family reference"))}
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
                    placeholder={selectedAgentName ? t(lt(`给 ${selectedAgentName} 发消息...`, `Message ${selectedAgentName}...`)) : t(lt("发送一条消息...", "Send a message..."))}
                    className="custom-scrollbar min-h-[44px] max-h-[172px] w-full resize-none overflow-y-auto border-none bg-transparent px-3 py-2 text-[14px] leading-relaxed placeholder:text-muted-foreground/50 shadow-none focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 sm:text-[15px]"
                />

                {isCommandPickerOpen && (
                    <div className="mx-3 mb-2 rounded-2xl border border-border/60 bg-background/95 shadow-lg backdrop-blur-xl">
                        <div className="border-b border-border/50 px-3 py-2 text-[11px] text-muted-foreground">
                            {t(lt("输入 ", "Type "))}<span className="font-medium text-foreground">/</span>{t(lt(" 后选择一个命令预设，发送时会作为结构化数据交给 Engine。", " to pick a command preset. It will be sent to Engine as structured input."))}
                        </div>
                        <div className="max-h-32 sm:max-h-40 overflow-y-auto px-1 py-1">
                            {commandsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t(lt("正在读取命令预设...", "Loading command presets..."))}</div>
                            ) : filteredCommandPresets.length > 0 ? (
                                filteredCommandPresets.map((preset) => (
                                    <button
                                        key={preset.name}
                                        type="button"
                                        onClick={() => selectCommandPreset(preset)}
                                        className="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition hover:bg-accent/50"
                                    >
                                        <Command className="mt-0.5 h-4 w-4 shrink-0 text-orange-500" />
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
                                        ? t(lt("当前没有可用命令预设，请把 .md 文件放进 ~/.v8-agent-os/commands。", "No command presets are available yet. Put .md files into ~/.v8-agent-os/commands."))
                                        : t(lt("没有匹配的命令预设。", "No matching command presets."))}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {isSkillPickerOpen && (
                    <div className="mx-3 mb-2 rounded-2xl border border-fuchsia-200/60 bg-background/95 shadow-lg backdrop-blur-xl dark:border-fuchsia-500/20">
                        <div className="border-b border-fuchsia-200/40 px-3 py-2 text-[11px] text-muted-foreground dark:border-fuchsia-500/10">
                            {t(lt("输入 ", "Type "))}<span className="font-medium text-fuchsia-600">@</span>{t(lt(" 后选择 Skill 或 Subagent 家族；家族只会显式展开本轮 Supervisor 可见成员。", " to pick Skills or Subagent Families. Families reveal members only for this supervisor turn."))}
                        </div>
                        <div className="max-h-32 overflow-y-auto px-1 py-1 sm:max-h-40">
                            {skillsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t(lt("正在读取技能列表...", "Loading skills..."))}</div>
                            ) : filteredMentionItems.length > 0 ? (
                                filteredMentionItems.map((item, index) => (
                                    <button
                                        key={item.key}
                                        type="button"
                                        onClick={() => selectMentionItem(item)}
                                        className={cn(
                                            "flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition",
                                            item.kind === "skill"
                                                ? "hover:bg-fuchsia-50/70 dark:hover:bg-fuchsia-500/10"
                                                : "hover:bg-sky-50/70 dark:hover:bg-sky-500/10",
                                            index > 0 && filteredMentionItems[index - 1]?.kind !== item.kind ? "mt-1 border-t border-border/50 pt-3" : ""
                                        )}
                                    >
                                        <AtSign className={cn(
                                            "mt-0.5 h-4 w-4 shrink-0",
                                            item.kind === "skill" ? "text-fuchsia-500" : "text-sky-500"
                                        )} />
                                        <div className="min-w-0 flex-1">
                                            <div className="flex items-center gap-2">
                                                <span className="truncate text-sm font-medium text-foreground">
                                                    {item.kind === "skill" ? item.skill.name : (item.family.displayName || item.family.familyId)}
                                                </span>
                                                <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                                    {item.kind === "skill" ? "Skill" : "Family"}
                                                </span>
                                            </div>
                                            {(item.kind === "skill" ? item.skill.description : item.family.description) && (
                                                <div className="line-clamp-1 text-[11px] leading-4.5 text-muted-foreground sm:line-clamp-2">
                                                    {item.kind === "skill" ? item.skill.description : item.family.description}
                                                </div>
                                            )}
                                            {item.kind === "skill" && item.skill.path && (
                                                <div className="truncate text-[10px] text-fuchsia-700/80 dark:text-fuchsia-300/80">
                                                    {item.skill.path}
                                                </div>
                                            )}
                                            {item.kind === "subagent_family" && item.family.memberCount ? (
                                                <div className="truncate text-[10px] text-sky-700/80 dark:text-sky-300/80">
                                                    {t(lt(`${item.family.memberCount} 个成员`, `${item.family.memberCount} members`))}
                                                </div>
                                            ) : null}
                                        </div>
                                    </button>
                                ))
                            ) : (
                                <div className="px-3 py-3 text-sm text-muted-foreground">
                                    {skillsLoaded
                                        ? t(lt("当前没有匹配的 Skill 或 Subagent 家族。", "No matching Skills or Subagent Families were found."))
                                        : t(lt("Skill 与 Subagent 家族列表暂时不可用。", "The Skill and Subagent Family list is currently unavailable."))}
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
                            aria-label={t(lt("关闭提示", "Dismiss notice"))}
                        >
                            <X className="h-3.5 w-3.5" />
                        </button>
                    </div>
                )}

                <div className="flex items-center justify-between px-3 pb-2 pt-0">
                    <div className="flex items-center gap-1.5">
                        <Button
                            type="button"
                            variant={taskPlanningMode ? "secondary" : "ghost"}
                            size="sm"
                            onClick={() => setTaskPlanningMode((current) => !current)}
                            className={cn(
                                "h-[28px] rounded-lg px-2.5 text-[11px] font-medium",
                                taskPlanningMode
                                    ? "bg-red-500/12 text-red-700 hover:bg-red-500/18 dark:bg-red-500/15 dark:text-red-200"
                                    : "text-muted-foreground hover:bg-zinc-100/50 hover:text-foreground dark:hover:bg-zinc-800/50"
                            )}
                            title={t(lt("开启 Spec Mode 后，Supervisor 会先生成可审批的需求、设计和任务合约。", "Spec Mode asks Supervisor to draft approvable requirements, design, and task contracts first."))}
                        >
                            <FileText className="mr-1 h-3.5 w-3.5" />
                            Spec
                        </Button>
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
                                    title={t(lt("临时调节本轮 Supervisor 推理强度", "Temporarily adjust Supervisor reasoning effort for this turn"))}
                                >
                                    <Gauge className="h-3.5 w-3.5" />
                                    <span>{t(lt("推", "Think"))}·{reasoningEffortLabel}</span>
                                </button>
                                {reasoningEffortOpen ? (
                                    <div className="absolute bottom-full left-0 z-30 mb-2 flex overflow-hidden rounded-2xl border border-zinc-200/70 bg-white/95 p-1 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-zinc-700/70 dark:bg-zinc-950/95">
                                        {reasoningEffortLevels.map((level) => {
                                            const active = level === reasoningEffort;
                                            const labels: Record<ReasoningEffortLevel, string> = {
                                                auto: t(lt("自动", "Auto")),
                                                low: t(lt("低", "Low")),
                                                medium: t(lt("中", "Medium")),
                                                high: t(lt("高", "High")),
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
                            title={t(lt("上传文件", "Upload files"))}
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
                            title={isRecording ? t(lt("停止录音并转写", "Stop and transcribe")) : isTranscribing ? t(lt("正在转写语音", "Transcribing voice")) : t(lt("录音转文字", "Voice to text"))}
                        >
                            {isTranscribing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
                        </Button>
                    </div>

                    <Button
                        type={isLoading ? "button" : "submit"}
                        size="icon"
                        className={cn(
                            "h-[32px] w-[32px] rounded-xl shadow-sm transition-all duration-300",
                            isLoading
                                ? "bg-stone-200 text-stone-400 dark:bg-stone-800 dark:text-stone-500"
                                : (canSubmit
                                    ? "bg-gradient-to-br from-orange-400 to-amber-600 dark:from-orange-500 dark:to-amber-700 text-white shadow-[0_0_12px_rgba(249,115,22,0.4)] hover:shadow-[0_0_16px_rgba(249,115,22,0.6)] hover:scale-105 border border-orange-300/50 dark:border-amber-500/50"
                                    : "bg-stone-100 text-stone-300 dark:bg-stone-800/60 dark:text-stone-600 cursor-not-allowed")
                        )}
                        disabled={uploading || (!isLoading && !canSubmit)}
                        onClick={(e) => {
                            if (isLoading && onStop) {
                                e.preventDefault();
                                onStop();
                            }
                        }}
                    >
                        {isLoading ? (
                            <div className="relative flex items-center justify-center w-full h-full group">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-100 dark:bg-zinc-800 rounded-xl">
                                    <Square className="h-3 w-3 fill-destructive text-destructive" />
                                </div>
                            </div>
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
