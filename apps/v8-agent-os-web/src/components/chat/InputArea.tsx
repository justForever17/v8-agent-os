"use client";
/* eslint-disable @next/next/no-img-element */

import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Paperclip, Send, Mic, Loader2, Square, X, PlayCircle, AlertCircle, CheckCircle2, Info, Command, ListTodo, AtSign } from "lucide-react";
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
    isLoading: boolean;
    onStop?: () => void;
    selectedAgentName?: string;
    shellClassName?: string;
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

function isSkillReferenceSummaryCandidate(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function InputArea({
    input,
    handleInputChange,
    handleSubmit,
    onVoiceTranscript,
    isLoading,
    onStop,
    selectedAgentName,
    shellClassName,
}: InputAreaProps) {
    const t = useT();
    const [commandPresets, setCommandPresets] = React.useState<CommandPresetSummary[]>([]);
    const [commandsLoaded, setCommandsLoaded] = React.useState(false);
    const [commandsLoading, setCommandsLoading] = React.useState(false);
    const [selectedCommandPreset, setSelectedCommandPreset] = React.useState<CommandPresetSummary | null>(null);
    const [skills, setSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [skillsLoaded, setSkillsLoaded] = React.useState(false);
    const [skillsLoading, setSkillsLoading] = React.useState(false);
    const [selectedSkills, setSelectedSkills] = React.useState<SkillReferenceSummary[]>([]);
    const [taskPlanningMode, setTaskPlanningMode] = React.useState(false);
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
    const filteredSkills = React.useMemo(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}::${skill.path || ""}`));
        const base = skills.filter((skill) => !selectedKeys.has(`${skill.name}::${skill.path || ""}`));
        if (!skillQuery) {
            return base;
        }
        const keyword = skillQuery.toLowerCase();
        return base.filter((skill) =>
            skill.name.toLowerCase().includes(keyword)
            || String(skill.description || "").toLowerCase().includes(keyword)
            || String(skill.path || "").toLowerCase().includes(keyword)
        );
    }, [selectedSkills, skillQuery, skills]);

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
                if (filteredSkills.length > 0) {
                    selectSkillReference(filteredSkills[0]);
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

    const transcribeAudio = React.useCallback(async (blob: Blob) => {
        try {
            setIsTranscribing(true);
            showInlineNotice("info", t(lt("正在转写语音...", "Transcribing voice...")), 0);
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
            showInlineNotice("success", t(lt("语音已转成文字并填入输入框。", "Voice was transcribed and inserted into the input.")));
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("语音转写失败", "Voice transcription failed"));
            showInlineNotice("error", t(lt(`语音暂不可用：${message}`, `Voice is unavailable: ${message}`)));
        } finally {
            setIsTranscribing(false);
        }
    }, [onVoiceTranscript, showInlineNotice, t]);

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
        || selectedSkills.length > 0;

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
                if (uploadedUrls.length > 0) {
                    nextData.fileUrls = uploadedUrls;
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
                if (taskPlanningMode) {
                    nextData.taskPlanningMode = true;
                }

                await handleSubmit(e, { data: nextData });
                setFiles([]);
                setUploadedUrls([]);
                setSelectedCommandPreset(null);
                setSelectedSkills([]);
            }}
            className={cn(
                "relative mx-auto flex w-full flex-col overflow-hidden rounded-[1.25rem] border shadow-sm transition-all duration-500",
                shellClassName ?? "max-w-4xl",
                isFocused 
                    ? "bg-stone-50/95 dark:bg-stone-900/90 shadow-[0_8px_32px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_32px_rgba(0,0,0,0.3)] border-orange-500/30 dark:border-amber-500/30" 
                    : "bg-stone-50/60 dark:bg-stone-900/50 backdrop-blur-xl hover:bg-stone-50/80 hover:dark:bg-stone-900/70 border-stone-200/60 dark:border-stone-800/60"
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
                {(selectedCommandPreset || taskPlanningMode || selectedSkills.length > 0) && (
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
                        {taskPlanningMode && (
                            <div className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200 sm:text-[11px]">
                                <ListTodo className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" />
                                <span>{t(lt("任务模式", "Task mode"))}</span>
                            </div>
                        )}
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
                            {t(lt("输入 ", "Type "))}<span className="font-medium text-fuchsia-600">@</span>{t(lt(" 后选择一个或多个 Skill，发送时会把名称、说明和真实路径作为结构化上下文交给 Supervisor。", " to pick one or more skills. Their name, description, and real path will be sent to Supervisor as structured context."))}
                        </div>
                        <div className="max-h-32 overflow-y-auto px-1 py-1 sm:max-h-40">
                            {skillsLoading ? (
                                <div className="px-3 py-3 text-sm text-muted-foreground">{t(lt("正在读取技能列表...", "Loading skills..."))}</div>
                            ) : filteredSkills.length > 0 ? (
                                filteredSkills.map((skill) => (
                                    <button
                                        key={`${skill.name}:${skill.path || ""}`}
                                        type="button"
                                        onClick={() => selectSkillReference(skill)}
                                        className="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition hover:bg-fuchsia-50/70 dark:hover:bg-fuchsia-500/10"
                                    >
                                        <AtSign className="mt-0.5 h-4 w-4 shrink-0 text-fuchsia-500" />
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-sm font-medium text-foreground">
                                                {skill.name}
                                            </div>
                                            {skill.description && (
                                                <div className="line-clamp-1 text-[11px] leading-4.5 text-muted-foreground sm:line-clamp-2">
                                                    {skill.description}
                                                </div>
                                            )}
                                            {skill.path && (
                                                <div className="truncate text-[10px] text-fuchsia-700/80 dark:text-fuchsia-300/80">
                                                    {skill.path}
                                                </div>
                                            )}
                                        </div>
                                    </button>
                                ))
                            ) : (
                                <div className="px-3 py-3 text-sm text-muted-foreground">
                                    {skillsLoaded
                                        ? t(lt("当前没有匹配的 Skill。", "No matching skills were found."))
                                        : t(lt("技能列表暂时不可用。", "The skill list is currently unavailable."))}
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
                                    ? "bg-emerald-500/12 text-emerald-700 hover:bg-emerald-500/18 dark:bg-emerald-500/15 dark:text-emerald-200"
                                    : "text-muted-foreground hover:bg-zinc-100/50 hover:text-foreground dark:hover:bg-zinc-800/50"
                            )}
                            title={t(lt("开启任务模式后，Supervisor 会优先规划步骤和待办。", "Task mode asks Supervisor to plan steps and todos first."))}
                        >
                            <ListTodo className="mr-1 h-3.5 w-3.5" />
                            {t(lt("任务模式", "Task mode"))}
                        </Button>
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
