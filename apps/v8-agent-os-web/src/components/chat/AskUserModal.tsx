import { useMemo, useState } from "react";
import { ArrowRight, Check, MessageCircleMore, X } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
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

function normalizeQuestions(question: string, request?: AskUserRequest | null): AskUserQuestion[] {
    const source = Array.isArray(request?.questions) ? request?.questions : [];
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
    return [{
        id: "answer",
        title: asText(request?.question) || asText(request?.prompt) || question,
        detail: asText(request?.details),
        options: [],
    }];
}

function normalizeMedia(request?: AskUserRequest | null): AskUserMedia[] {
    const merged = [
        ...(Array.isArray(request?.media) ? request?.media : []),
        ...(Array.isArray(request?.artifacts) ? request?.artifacts : []),
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

export function AskUserModal({ isOpen, question, request, toolCallId, onSubmit, onCancel }: AskUserModalProps) {
    const t = useT();
    const questions = useMemo(() => normalizeQuestions(question, request), [question, request]);
    const mediaItems = useMemo(() => normalizeMedia(request), [request]);
    const mediaSelectionMode = asText(request?.selectionMode).toLowerCase() === "multiple" ? "multiple" : "single";
    const [textAnswer, setTextAnswer] = useState("");
    const [selectedOptions, setSelectedOptions] = useState<Record<string, string[]>>({});
    const [selectedMedia, setSelectedMedia] = useState<string[]>([]);
    const [isSubmitting, setIsSubmitting] = useState(false);

    const resetForm = () => {
        setTextAnswer("");
        setSelectedOptions({});
        setSelectedMedia([]);
        setIsSubmitting(false);
    };

    const toggleOption = (questionItem: AskUserQuestion, option: AskUserOption, index: number) => {
        const qid = asText(questionItem.id) || asText(questionItem.title) || "answer";
        const key = optionKey(option, index);
        const multi = Boolean(questionItem.multiSelect || questionItem.multiple);
        setSelectedOptions((current) => {
            const currentValues = current[qid] || [];
            const nextValues = currentValues.includes(key)
                ? currentValues.filter((item) => item !== key)
                : multi
                    ? [...currentValues, key]
                    : [key];
            return { ...current, [qid]: nextValues };
        });
    };

    const toggleMedia = (item: AskUserMedia, index: number) => {
        const key = mediaKey(item, index);
        setSelectedMedia((current) => {
            if (current.includes(key)) return current.filter((value) => value !== key);
            return mediaSelectionMode === "multiple" ? [...current, key] : [key];
        });
    };

    const buildAnswer = () => {
        const lines: string[] = [];
        for (const q of questions) {
            const qid = asText(q.id) || asText(q.title) || "answer";
            const chosen = (selectedOptions[qid] || [])
                .map((key) => {
                    const option = (q.options || []).find((candidate, index) => optionKey(candidate, index) === key);
                    return asText(option?.title) || asText(option?.label) || asText(option?.value) || key;
                })
                .filter(Boolean);
            if (chosen.length) {
                lines.push(`${asText(q.title) || asText(q.question) || qid}: ${chosen.join("、")}`);
            }
        }
        if (selectedMedia.length) {
            lines.push(`选择的参考产物: ${selectedMedia.join("、")}`);
        }
        if (textAnswer.trim()) {
            lines.push(textAnswer.trim());
        }
        return lines.join("\n").trim();
    };

    const hasOptionQuestion = questions.some((item) => (item.options || []).length > 0);
    const canSubmit = Boolean(buildAnswer());

    const handleSubmit = (approve: boolean) => {
        const answer = buildAnswer();
        if (approve && !answer) return;
        setIsSubmitting(true);
        resetForm();
        onSubmit(toolCallId, answer, approve);
    };

    const handleOpenChange = (open: boolean) => {
        if (open) return;
        resetForm();
        onCancel?.();
    };

    return (
        <TooltipProvider delayDuration={180}>
            <Dialog open={isOpen} onOpenChange={handleOpenChange}>
                <DialogContent className="flex max-h-[88vh] w-[min(94vw,640px)] flex-col overflow-hidden border border-border/70 bg-background/95 p-0 shadow-2xl backdrop-blur-2xl sm:rounded-3xl">
                    <DialogHeader className="border-b border-border/50 px-4 py-3 sm:px-5">
                        <div className="flex items-center gap-3">
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                                <MessageCircleMore className="h-4.5 w-4.5" />
                            </div>
                            <div className="min-w-0">
                                <DialogTitle className="truncate text-base font-semibold tracking-tight text-foreground">
                                    {asText(request?.question) || question || t(lt("需要你的输入", "Input needed"))}
                                </DialogTitle>
                                {asText(request?.details) ? (
                                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{asText(request?.details)}</p>
                                ) : null}
                            </div>
                        </div>
                    </DialogHeader>

                    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-5">
                        {mediaItems.length ? (
                            <div className="mb-3 flex gap-2 overflow-x-auto pb-1">
                                {mediaItems.map((item, index) => {
                                    const key = mediaKey(item, index);
                                    const selected = selectedMedia.includes(key);
                                    const url = mediaUrl(item);
                                    const kind = mediaKind(item);
                                    const title = asText(item.title) || asText(item.name) || key;
                                    return (
                                        <button
                                            key={key}
                                            type="button"
                                            onClick={() => toggleMedia(item, index)}
                                            className={`group relative h-24 w-32 shrink-0 overflow-hidden rounded-2xl border text-left transition ${selected ? "border-primary ring-2 ring-primary/25" : "border-border/70 hover:border-primary/50"}`}
                                        >
                                            {kind === "image" && url ? (
                                                // eslint-disable-next-line @next/next/no-img-element
                                                <img src={url} alt={title} className="h-full w-full object-cover" />
                                            ) : (
                                                <div className="flex h-full w-full items-center justify-center bg-muted text-xs font-semibold uppercase text-muted-foreground">
                                                    {kind}
                                                </div>
                                            )}
                                            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-5 text-[11px] font-medium text-white">
                                                <span className="line-clamp-1">{title}</span>
                                            </div>
                                            {selected ? (
                                                <span className="absolute right-2 top-2 rounded-full bg-primary p-1 text-primary-foreground">
                                                    <Check className="h-3 w-3" />
                                                </span>
                                            ) : null}
                                        </button>
                                    );
                                })}
                            </div>
                        ) : null}

                        <div className="space-y-2.5">
                            {questions.map((q, qIndex) => {
                                const qid = asText(q.id) || `q${qIndex + 1}`;
                                const options = q.options || [];
                                const title = asText(q.title) || asText(q.question) || `${qIndex + 1}`;
                                const detail = asText(q.detail) || asText(q.description);
                                return (
                                    <section key={qid} className="rounded-2xl bg-muted/25 p-2.5">
                                        <div className="mb-2 flex items-center justify-between gap-2">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <div className="min-w-0 cursor-help truncate text-sm font-semibold text-foreground">
                                                        {title}
                                                    </div>
                                                </TooltipTrigger>
                                                {detail ? (
                                                    <TooltipContent side="top" className="max-w-xs text-xs leading-5">
                                                        {detail}
                                                    </TooltipContent>
                                                ) : null}
                                            </Tooltip>
                                            {q.multiSelect || q.multiple ? (
                                                <span className="shrink-0 rounded-full bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                                                    {t(lt("多选", "Multi"))}
                                                </span>
                                            ) : null}
                                        </div>
                                        {options.length ? (
                                            <div className="grid gap-2 sm:grid-cols-2">
                                                {options.map((option, index) => {
                                                    const key = optionKey(option, index);
                                                    const selected = (selectedOptions[qid] || []).includes(key);
                                                    const label = asText(option.title) || asText(option.label) || asText(option.value) || key;
                                                    const optionDetail = asText(option.detail) || asText(option.description);
                                                    return (
                                                        <Tooltip key={key}>
                                                            <TooltipTrigger asChild>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => toggleOption(q, option, index)}
                                                                    className={`flex min-h-10 items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left text-sm transition ${selected ? "border-primary bg-primary/10 text-primary" : "border-border/70 bg-background hover:border-primary/40"}`}
                                                                >
                                                                    <span className="line-clamp-2">{label}</span>
                                                                    {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                                                                </button>
                                                            </TooltipTrigger>
                                                            {optionDetail ? (
                                                                <TooltipContent side="top" className="max-w-xs text-xs leading-5">
                                                                    {optionDetail}
                                                                </TooltipContent>
                                                            ) : null}
                                                        </Tooltip>
                                                    );
                                                })}
                                            </div>
                                        ) : null}
                                    </section>
                                );
                            })}
                        </div>

                        <Textarea
                            value={textAnswer}
                            onChange={(event) => setTextAnswer(event.target.value)}
                            placeholder={hasOptionQuestion ? t(lt("补充说明，可留空", "Optional note")) : t(lt("输入你的回答", "Type your answer"))}
                            className="mt-3 min-h-[88px] resize-none rounded-2xl border-border/70 bg-background/90 text-sm leading-6 focus-visible:ring-primary/35"
                            autoFocus={!hasOptionQuestion}
                        />
                    </div>

                    <DialogFooter className="border-t border-border/50 bg-background/98 px-4 py-3 sm:px-5">
                        <div className="flex w-full justify-end gap-2">
                            <Button variant="ghost" onClick={onCancel} disabled={isSubmitting} className="rounded-xl">
                                <X className="mr-1.5 h-4 w-4" />
                                {t(lt("稍后", "Later"))}
                            </Button>
                            <Button onClick={() => handleSubmit(true)} disabled={isSubmitting || !canSubmit} className="rounded-xl">
                                <ArrowRight className="mr-2 h-4 w-4" />
                                {isSubmitting ? t(lt("发送中", "Sending")) : t(lt("发送", "Send"))}
                            </Button>
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </TooltipProvider>
    );
}
