import { useMemo, useRef, useState } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, Pause, Play, X } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { lt } from "@/lib/locale";

type AskUserOption = {
    id?: string;
    value?: string;
    title?: string;
    label?: string;
    detail?: string;
    description?: string;
};

type AskUserQuestion = {
    id?: string;
    title?: string;
    label?: string;
    question?: string;
    detail?: string;
    description?: string;
    multiSelect?: boolean;
    multiple?: boolean;
    options?: AskUserOption[];
};

type AskUserMedia = {
    id?: string;
    artifactId?: string;
    title?: string;
    name?: string;
    type?: string;
    kind?: string;
    mimeType?: string;
    url?: string;
    href?: string;
    previewUrl?: string;
    thumbnailUrl?: string;
    contentUrl?: string;
};

type AskUserRequest = Record<string, unknown> & {
    question?: string;
    prompt?: string;
    details?: string;
    questions?: AskUserQuestion[];
    media?: AskUserMedia[];
    artifacts?: AskUserMedia[];
    selectionMode?: string;
};

interface AskUserModalProps {
    isOpen: boolean;
    question: string;
    request?: AskUserRequest | null;
    toolCallId: string;
    onSubmit: (toolCallId: string, answer: string, approve: boolean) => void;
    onCancel?: () => void;
}

function asText(value: unknown) {
    return typeof value === "string" ? value.trim() : "";
}

function optionKey(option: AskUserOption, index: number) {
    return asText(option.id) || asText(option.value) || asText(option.title) || asText(option.label) || `option-${index}`;
}

function mediaKey(item: AskUserMedia, index: number) {
    return asText(item.id) || asText(item.artifactId) || asText(item.url) || asText(item.previewUrl) || `media-${index}`;
}

function questionKey(questionItem: AskUserQuestion, index: number) {
    return asText(questionItem.id) || asText(questionItem.title) || asText(questionItem.question) || `q${index + 1}`;
}

function normalizeQuestions(question: string, request?: AskUserRequest | null): AskUserQuestion[] {
    const source = Array.isArray(request?.questions) ? request.questions : [];
    const normalized = source
        .filter((item) => item && typeof item === "object")
        .map((item, index) => ({
            ...item,
            id: asText(item.id) || `q${index + 1}`,
            title: asText(item.title) || asText(item.label) || asText(item.question) || `${index + 1}`,
            detail: asText(item.detail) || asText(item.description),
            options: Array.isArray(item.options) ? item.options.filter((option) => option && typeof option === "object") : [],
        }));
    if (normalized.length) {
        return normalized;
    }
    return [
        {
            id: "answer",
            title: asText(request?.question) || asText(request?.prompt) || question,
            detail: asText(request?.details),
            options: [],
        },
    ];
}

function normalizeMedia(request?: AskUserRequest | null): AskUserMedia[] {
    const merged = [
        ...(Array.isArray(request?.media) ? request.media : []),
        ...(Array.isArray(request?.artifacts) ? request.artifacts : []),
    ];
    const seen = new Set<string>();
    return merged.filter((item, index) => {
        if (!item || typeof item !== "object") return false;
        const key = mediaKey(item, index);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function mediaKind(item: AskUserMedia) {
    const text = `${asText(item.type)} ${asText(item.kind)} ${asText(item.mimeType)}`.toLowerCase();
    if (text.includes("video")) return "video";
    if (text.includes("audio")) return "audio";
    return "image";
}

function mediaUrl(item: AskUserMedia) {
    return asText(item.previewUrl) || asText(item.thumbnailUrl) || asText(item.url) || asText(item.contentUrl) || asText(item.href);
}

function mediaPlaybackUrl(item: AskUserMedia) {
    const direct = asText(item.contentUrl) || asText(item.url) || asText(item.href) || asText(item.previewUrl);
    if (direct) return direct;
    const artifactId = asText(item.artifactId) || asText(item.id);
    return artifactId ? `/api/client/artifacts/${encodeURIComponent(artifactId)}/content` : "";
}

function mediaLabel(item: AskUserMedia, index: number) {
    return asText(item.title) || asText(item.name) || asText(item.artifactId) || `media-${index + 1}`;
}

function optionLabel(option: AskUserOption, index: number) {
    return asText(option.title) || asText(option.label) || asText(option.value) || `option-${index + 1}`;
}

function optionDetail(option: AskUserOption) {
    return asText(option.detail) || asText(option.description);
}

function questionTitle(questionItem: AskUserQuestion, index: number) {
    return asText(questionItem.title) || asText(questionItem.question) || `${index + 1}`;
}

function questionDetail(questionItem: AskUserQuestion) {
    return asText(questionItem.detail) || asText(questionItem.description);
}

function AskUserMediaCard({
    item,
    index,
    selected,
    onToggle,
}: {
    item: AskUserMedia;
    index: number;
    selected: boolean;
    onToggle: () => void;
}) {
    const t = useT();
    const kind = mediaKind(item);
    const previewUrl = mediaUrl(item);
    const playbackUrl = mediaPlaybackUrl(item);
    const label = mediaLabel(item, index);
    const mediaRef = useRef<HTMLAudioElement | HTMLVideoElement | null>(null);
    const [playing, setPlaying] = useState(false);

    const canPlay = Boolean(playbackUrl && (kind === "audio" || kind === "video"));

    const togglePlayback = async (event: React.MouseEvent<HTMLButtonElement>) => {
        event.preventDefault();
        event.stopPropagation();
        const media = mediaRef.current;
        if (!media) return;
        if (playing) {
            media.pause();
            setPlaying(false);
            return;
        }
        try {
            await media.play();
            setPlaying(true);
        } catch {
            setPlaying(false);
        }
    };

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <div
                    role="button"
                    tabIndex={0}
                    onClick={onToggle}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onToggle();
                        }
                    }}
                    className={`group relative h-14 w-24 shrink-0 cursor-pointer overflow-hidden rounded-xl text-left transition ${selected ? "ring-2 ring-primary/30" : "ring-1 ring-border/40 hover:ring-primary/35"}`}
                    aria-label={label}
                    aria-pressed={selected}
                >
                    {kind === "image" && previewUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={previewUrl} alt={label} className="h-full w-full object-cover" />
                    ) : kind === "video" && playbackUrl ? (
                        <video
                            ref={(node) => {
                                mediaRef.current = node;
                            }}
                            src={playbackUrl}
                            className="h-full w-full bg-black object-cover"
                            preload="metadata"
                            playsInline
                            onEnded={() => setPlaying(false)}
                            onPause={() => setPlaying(false)}
                            onPlay={() => setPlaying(true)}
                        />
                    ) : kind === "audio" && playbackUrl ? (
                        <div className="flex h-full w-full items-center justify-center bg-muted text-[11px] font-semibold uppercase text-muted-foreground">
                            <audio
                                ref={(node) => {
                                    mediaRef.current = node;
                                }}
                                src={playbackUrl}
                                preload="metadata"
                                onEnded={() => setPlaying(false)}
                                onPause={() => setPlaying(false)}
                                onPlay={() => setPlaying(true)}
                            />
                            audio
                        </div>
                    ) : (
                        <div className="flex h-full w-full items-center justify-center bg-muted text-[11px] font-semibold uppercase text-muted-foreground">
                            {kind}
                        </div>
                    )}
                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1 pt-5 text-[10px] font-medium text-white">
                        <span className="block truncate">{label}</span>
                    </div>
                    {canPlay ? (
                        <button
                            type="button"
                            onClick={togglePlayback}
                            className="absolute left-1.5 top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white shadow-sm backdrop-blur transition hover:bg-black/70"
                            aria-label={playing ? t(lt("暂停", "Pause")) : t(lt("播放", "Play"))}
                        >
                            {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 translate-x-px" />}
                        </button>
                    ) : null}
                    {selected ? (
                        <span className="absolute right-1.5 top-1.5 rounded-full bg-primary p-0.5 text-primary-foreground">
                            <CheckCircle2 className="h-3 w-3" />
                        </span>
                    ) : null}
                </div>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs text-xs leading-5">
                {label}
            </TooltipContent>
        </Tooltip>
    );
}

export function AskUserModal({ isOpen, question, request, toolCallId, onSubmit, onCancel }: AskUserModalProps) {
    const t = useT();
    const questions = useMemo(() => normalizeQuestions(question, request), [question, request]);
    const mediaItems = useMemo(() => normalizeMedia(request), [request]);
    const mediaSelectionMode = asText(request?.selectionMode).toLowerCase() === "multiple" ? "multiple" : "single";
    const [pageIndex, setPageIndex] = useState(0);
    const [selectedOptions, setSelectedOptions] = useState<Record<string, string[]>>({});
    const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
    const [selectedMedia, setSelectedMedia] = useState<string[]>([]);
    const [isSubmitting, setIsSubmitting] = useState(false);

    const currentQuestion = questions[Math.min(pageIndex, Math.max(questions.length - 1, 0))];
    const currentQuestionKey = currentQuestion ? questionKey(currentQuestion, pageIndex) : "answer";
    const currentQuestionOptions = currentQuestion?.options || [];
    const currentQuestionMulti = Boolean(currentQuestion?.multiSelect || currentQuestion?.multiple);

    const resetForm = () => {
        setPageIndex(0);
        setSelectedOptions({});
        setCustomAnswers({});
        setSelectedMedia([]);
        setIsSubmitting(false);
    };

    const goToPage = (nextIndex: number) => {
        setPageIndex(Math.min(Math.max(nextIndex, 0), Math.max(questions.length - 1, 0)));
    };

    const toggleOption = (questionItem: AskUserQuestion, qIndex: number, option: AskUserOption, optionIndex: number) => {
        const qid = questionKey(questionItem, qIndex);
        const key = optionKey(option, optionIndex);
        const multi = Boolean(questionItem.multiSelect || questionItem.multiple);
        const alreadySelected = (selectedOptions[qid] || []).includes(key);
        const selectedAfterClick = !alreadySelected;
        setSelectedOptions((current) => {
            const currentValues = current[qid] || [];
            const nextValues = alreadySelected
                ? currentValues.filter((item) => item !== key)
                : multi
                    ? [...currentValues, key]
                    : [key];
            return { ...current, [qid]: nextValues };
        });
        if (!multi && selectedAfterClick && qIndex < questions.length - 1) {
            window.setTimeout(() => goToPage(qIndex + 1), 180);
        }
    };

    const toggleMedia = (item: AskUserMedia, index: number) => {
        const key = mediaKey(item, index);
        setSelectedMedia((current) => {
            if (current.includes(key)) return current.filter((value) => value !== key);
            return mediaSelectionMode === "multiple" ? [...current, key] : [key];
        });
    };

    const isQuestionAnswered = (questionItem: AskUserQuestion, qIndex: number) => {
        const qid = questionKey(questionItem, qIndex);
        return Boolean((selectedOptions[qid] || []).length || asText(customAnswers[qid]));
    };

    const buildAnswer = () => {
        const lines: string[] = [];
        for (const [index, q] of questions.entries()) {
            const qid = questionKey(q, index);
            const chosen = (selectedOptions[qid] || [])
                .map((key) => {
                    const optionIndex = (q.options || []).findIndex((candidate, index) => optionKey(candidate, index) === key);
                    const option = optionIndex >= 0 ? (q.options || [])[optionIndex] : null;
                    return option ? optionLabel(option, optionIndex) : key;
                })
                .filter(Boolean);
            const custom = asText(customAnswers[qid]);
            const parts = [...chosen];
            if (custom) parts.push(custom);
            if (parts.length) {
                lines.push(`${index + 1}. ${questionTitle(q, index)}: ${parts.join("；")}`);
            }
        }
        if (selectedMedia.length) {
            const labels = selectedMedia.map((key) => {
                const foundIndex = mediaItems.findIndex((item, index) => mediaKey(item, index) === key);
                return foundIndex >= 0 ? mediaLabel(mediaItems[foundIndex], foundIndex) : key;
            });
            lines.unshift(`参考产物: ${labels.join("、")}`);
        }
        return lines.join("\n").trim();
    };

    const allQuestionsAnswered = questions.length ? questions.every(isQuestionAnswered) : true;
    const canSubmit = Boolean(buildAnswer()) && allQuestionsAnswered;

    const handleSubmit = () => {
        const answer = buildAnswer();
        if (!answer || !allQuestionsAnswered) return;
        setIsSubmitting(true);
        resetForm();
        onSubmit(toolCallId, answer, true);
    };

    const handleCancel = () => {
        onCancel?.();
    };

    const handleNext = () => {
        if (pageIndex < questions.length - 1) {
            goToPage(pageIndex + 1);
            return;
        }
        handleSubmit();
    };

    if (!isOpen) {
        return null;
    }

    const title = asText(request?.question) || question || t(lt("需要你的输入", "Input needed"));
    const details = asText(request?.details);
    const currentAnswered = currentQuestion ? isQuestionAnswered(currentQuestion, pageIndex) : true;
    const isLastPage = pageIndex >= questions.length - 1;

    return (
        <TooltipProvider delayDuration={180}>
            <div className="w-full animate-in fade-in slide-in-from-bottom-1 duration-150">
                <section
                    className="w-full overflow-hidden rounded-2xl border border-border/45 bg-background/96 shadow-sm backdrop-blur"
                    role="dialog"
                    aria-modal="false"
                    aria-label={title}
                >
                    <div className="flex items-center gap-3 border-b border-border/45 px-3 py-2 sm:px-4">
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                                <span className="h-2 w-2 shrink-0 rounded-full bg-primary" />
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
                                    </TooltipTrigger>
                                    <TooltipContent side="top" className="max-w-sm text-xs leading-5">
                                        {details || title}
                                    </TooltipContent>
                                </Tooltip>
                            </div>
                            {details ? <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{details}</p> : null}
                        </div>
                        <div className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
                            <button
                                type="button"
                                onClick={() => goToPage(pageIndex - 1)}
                                disabled={pageIndex <= 0}
                                className="rounded-lg p-1 text-muted-foreground transition hover:bg-muted disabled:opacity-30"
                                aria-label={t(lt("返回", "Back"))}
                            >
                                <ChevronLeft className="h-4 w-4" />
                            </button>
                            <span>{pageIndex + 1}/{Math.max(questions.length, 1)}</span>
                            <button
                                type="button"
                                onClick={() => goToPage(pageIndex + 1)}
                                disabled={pageIndex >= questions.length - 1}
                                className="rounded-lg p-1 text-muted-foreground transition hover:bg-muted disabled:opacity-30"
                                aria-label="NEXT"
                            >
                                <ChevronRight className="h-4 w-4" />
                            </button>
                            <button
                                type="button"
                                onClick={handleCancel}
                                className="ml-1 rounded-lg p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                                aria-label={t(lt("关闭", "Close"))}
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                    </div>

                    <div className="max-h-[min(42vh,360px)] overflow-y-auto px-3 py-2.5 sm:px-4">
                        {mediaItems.length ? (
                            <div className="mb-2.5 flex gap-2 overflow-x-auto pb-1">
                                {mediaItems.map((item, index) => {
                                    const key = mediaKey(item, index);
                                    return (
                                        <AskUserMediaCard
                                            key={key}
                                            item={item}
                                            index={index}
                                            selected={selectedMedia.includes(key)}
                                            onToggle={() => toggleMedia(item, index)}
                                        />
                                    );
                                })}
                            </div>
                        ) : null}

                        {currentQuestion ? (
                            <div className="p-1">
                                <div className="mb-2 flex items-center justify-between gap-2">
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <h3 className="min-w-0 truncate text-xs font-semibold text-foreground">
                                                {questionTitle(currentQuestion, pageIndex)}
                                            </h3>
                                        </TooltipTrigger>
                                        {questionDetail(currentQuestion) ? (
                                            <TooltipContent side="top" className="max-w-sm text-xs leading-5">
                                                {questionDetail(currentQuestion)}
                                            </TooltipContent>
                                        ) : null}
                                    </Tooltip>
                                    {currentQuestionMulti ? (
                                            <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] text-muted-foreground">
                                            {t(lt("多选", "Multi"))}
                                        </span>
                                    ) : null}
                                </div>

                                {currentQuestionOptions.length ? (
                                    <div className="grid gap-1.5 sm:grid-cols-3">
                                        {currentQuestionOptions.map((option, index) => {
                                            const key = optionKey(option, index);
                                            const selected = (selectedOptions[currentQuestionKey] || []).includes(key);
                                            const label = optionLabel(option, index);
                                            const detail = optionDetail(option);
                                            return (
                                                <Tooltip key={key}>
                                                    <TooltipTrigger asChild>
                                                        <button
                                                            type="button"
                                                            onClick={() => toggleOption(currentQuestion, pageIndex, option, index)}
                                                            className={`flex h-8 min-w-0 items-center justify-between gap-1 rounded-lg px-2 text-left text-xs transition ${selected ? "bg-primary/8 text-primary" : "hover:bg-muted/60"}`}
                                                        >
                                                            <span className="min-w-0 truncate">{label}</span>
                                                            {selected ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : null}
                                                        </button>
                                                    </TooltipTrigger>
                                                    {(detail || label) ? (
                                                        <TooltipContent side="top" className="max-w-sm text-xs leading-5">
                                                            {detail || label}
                                                        </TooltipContent>
                                                    ) : null}
                                                </Tooltip>
                                            );
                                        })}
                                    </div>
                                ) : null}

                                <input
                                    value={customAnswers[currentQuestionKey] || ""}
                                    onChange={(event) => setCustomAnswers((current) => ({ ...current, [currentQuestionKey]: event.target.value }))}
                                    placeholder={currentQuestionOptions.length ? t(lt("其他 / 补充说明", "Other / note")) : t(lt("输入你的回答", "Type your answer"))}
                                    className="mt-2 h-8 w-full rounded-lg border border-border/50 bg-background px-2 text-xs outline-none transition placeholder:text-muted-foreground focus:border-primary/60 focus:ring-2 focus:ring-primary/10"
                                    autoFocus={!currentQuestionOptions.length}
                                />
                            </div>
                        ) : null}
                    </div>

                    <div className="flex items-center justify-between gap-2 border-t border-border/45 px-3 py-2 sm:px-4">
                        <button
                            type="button"
                            onClick={handleCancel}
                            disabled={isSubmitting}
                            className="rounded-lg px-2 py-1.5 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
                        >
                            {t(lt("稍后", "Later"))}
                        </button>
                        <div className="flex items-center gap-1.5">
                            <button
                                type="button"
                                onClick={() => goToPage(pageIndex - 1)}
                                disabled={pageIndex <= 0 || isSubmitting}
                                className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-40"
                            >
                                ← {t(lt("返回", "Back"))}
                            </button>
                            <button
                                type="button"
                                onClick={handleNext}
                                disabled={isSubmitting || (!isLastPage && !currentAnswered) || (isLastPage && !canSubmit)}
                                className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-primary transition hover:bg-primary/8 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {isLastPage ? (isSubmitting ? t(lt("发送中", "Sending")) : t(lt("继续", "Continue"))) : t(lt("→ 下一个", "→ NEXT"))}
                            </button>
                        </div>
                    </div>
                </section>
            </div>
        </TooltipProvider>
    );
}
