"use client";

import { ArrowRight, Bot, MessageCircleMore } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { lt } from "@/lib/locale";

type AskUserCardProps = {
    question: string;
    status?: string;
};

export function AskUserCard({ question, status }: AskUserCardProps) {
    const t = useT();

    return (
        <div className="my-1.5 overflow-hidden rounded-2xl border border-sky-200/80 bg-sky-50/90 p-3 shadow-sm dark:border-sky-500/30 dark:bg-sky-500/10">
            <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-sky-500/12 text-sky-600 dark:text-sky-300">
                    <MessageCircleMore className="h-4.5 w-4.5" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <div className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-sky-700/80 dark:text-sky-200/80">
                            <Bot className="h-3.5 w-3.5" />
                            {t(lt("等待你的输入", "Waiting for your answer"))}
                        </div>
                        {status ? <Badge variant="outline">{status}</Badge> : null}
                    </div>
                    <div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-900 dark:text-slate-100">
                        {question}
                    </div>
                    <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-sky-700/80 dark:text-sky-200/80">
                        <ArrowRight className="h-3.5 w-3.5" />
                        {t(lt("请回答这个问题，当前运行会在收到回答后继续。", "Answer this question and the current run will continue once it receives your response."))}
                    </div>
                </div>
            </div>
        </div>
    );
}
