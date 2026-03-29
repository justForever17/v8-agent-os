import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

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
            <DialogContent className="sm:max-w-[600px] backdrop-blur-xl bg-background/95 shadow-2xl border-primary/20">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2 text-xl text-primary">
                        🙋 {t(lt("智能主管需要您的协助", "Supervisor needs your input"))}
                    </DialogTitle>
                    <DialogDescription className="text-base mt-2">
                        {t(lt("我需要您的输入以继续执行任务。", "I need your input to continue this task."))}
                    </DialogDescription>
                </DialogHeader>

                <div className="py-4 space-y-4">
                    <div className="bg-muted/40 p-4 rounded-lg border border-border/50 prose dark:prose-invert max-w-none text-sm break-words selection:bg-primary/20">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{question}</ReactMarkdown>
                    </div>

                    <Textarea
                        value={answer}
                        onChange={(e) => setAnswer(e.target.value)}
                        placeholder={t(lt("在此输入您的回答...", "Type your response here..."))}
                        className="min-h-[120px] text-base resize-none focus-visible:ring-primary/50"
                        autoFocus
                    />
                </div>

                <DialogFooter className="sm:justify-between">
                    <div className="text-xs text-muted-foreground self-center">
                        {t(lt("您的回答将作为继续执行所需的输入发送。", "Your answer will be sent back as required input for the run."))}
                    </div>
                    <div className="flex gap-2">
                        <Button variant="ghost" onClick={onCancel} disabled={isSubmitting}>{t(lt("取消", "Cancel"))}</Button>
                        <Button variant="outline" onClick={() => handleSubmit(false)} disabled={isSubmitting}>
                            {isSubmitting ? t(lt("处理中...", "Processing...")) : t(lt("拒绝", "Reject"))}
                        </Button>
                        <Button onClick={() => handleSubmit(true)} disabled={isSubmitting || !answer.trim()}>
                            {isSubmitting ? t(lt("发送中...", "Sending...")) : t(lt("批准继续", "Approve"))}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
