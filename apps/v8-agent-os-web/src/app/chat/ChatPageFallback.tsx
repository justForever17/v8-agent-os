"use client";

import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function ChatPageFallback() {
    const t = useT();

    return (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t(lt("正在载入对话...", "Loading chat..."))}
        </div>
    );
}
