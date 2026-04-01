import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { ArrowRight, Bot, MessageCircleMore, Sparkles } from "lucide-react";

interface AskUserModalProps {
    isOpen: boolean;
    question: string;
    toolCallId: string;
    onSubmit: (toolCallId: string, answer: string, approve: boolean) => void;
    onCancel?: () => void;
}

export function AskUserModal({ isOpen, question, toolCallId, onSubmit, onCancel }: AskUserModalProps) {
    const t = useT();
    const [answer, setAnswer] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = (approve: boolean) => {
        if (approve && !answer.trim()) return;
        setIsSubmitting(true);
        onSubmit(toolCallId, answer, approve);
    };

    const handleOpenChange = (open: boolean) => {
        if (open) {
            return;
        }
        setAnswer("");
        setIsSubmitting(false);
        onCancel?.();
    };

    return (
        <Dialog open={isOpen} onOpenChange={handleOpenChange}>
            <DialogContent className="max-h-[min(88vh,640px)] w-[min(94vw,540px)] overflow-hidden border border-primary/15 bg-background/96 p-0 shadow-2xl backdrop-blur-2xl sm:rounded-3xl">
                <DialogHeader className="border-b border-border/60 px-4 pb-3 pt-4 sm:px-5">
                    <div className="flex items-start gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                            <MessageCircleMore className="h-5 w-5" />
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] text-primary/80">
                                <Bot className="h-3.5 w-3.5" />
                                {t(lt("Supervisor 需要你的输入", "Supervisor needs your input"))}
                            </div>
                            <DialogTitle className="mt-1 text-lg font-semibold tracking-tight text-foreground">
                                {t(lt("继续前需要你确认一件事", "One quick answer before we continue"))}
                            </DialogTitle>
                            <DialogDescription className="mt-1 text-sm leading-6 text-muted-foreground">
                                {t(lt("这里不会打断整段对话，只会把你的回答回填给当前运行。", "This does not break the whole conversation. Your answer is fed back into the current run."))}
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 sm:px-5">
                    <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                        <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            <Sparkles className="h-3.5 w-3.5" />
                            {t(lt("当前问题", "Current question"))}
                        </div>
                        <div className="prose prose-sm max-w-none break-words text-sm leading-6 selection:bg-primary/15 dark:prose-invert">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{question}</ReactMarkdown>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            {t(lt("你的回应", "Your answer"))}
                        </div>
                        <Textarea
                            value={answer}
                            onChange={(e) => setAnswer(e.target.value)}
                            placeholder={t(lt("用一两句话直接回答，或补充继续执行所需的信息。", "Answer briefly, or provide the missing information needed to continue."))}
                            className="min-h-[112px] resize-none rounded-2xl border-border/70 bg-background/90 text-sm leading-6 focus-visible:ring-primary/35"
                            autoFocus
                        />
                        <div className="text-xs text-muted-foreground">
                            {t(lt("我们会把这段回答回填给当前运行，不会把你带离当前页面。", "We send this answer back to the active run without pulling you out of the current page."))}
                        </div>
                    </div>
                </div>

                <DialogFooter className="border-t border-border/60 bg-background/96 px-4 py-3 sm:px-5">
                    <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-xs text-muted-foreground">
                            {t(lt("如果暂时不想继续，可以直接拒绝，本次运行会停在等待输入的位置。", "If you do not want to continue right now, reject it and the run will stay at the waiting-for-input point."))}
                        </div>
                        <div className="flex flex-col-reverse gap-2 sm:flex-row">
                            <Button variant="ghost" onClick={onCancel} disabled={isSubmitting} className="rounded-xl">
                                {t(lt("先放着", "Dismiss"))}
                            </Button>
                            <Button variant="outline" onClick={() => handleSubmit(false)} disabled={isSubmitting} className="rounded-xl">
                                {isSubmitting ? t(lt("处理中...", "Processing...")) : t(lt("拒绝继续", "Reject"))}
                            </Button>
                            <Button onClick={() => handleSubmit(true)} disabled={isSubmitting || !answer.trim()} className="rounded-xl">
                                <ArrowRight className="mr-2 h-4 w-4" />
                                {isSubmitting ? t(lt("发送中...", "Sending...")) : t(lt("发送并继续", "Send and continue"))}
                            </Button>
                        </div>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
