"use client";

import { useT } from "@/components/providers/LocaleProvider";

export function ChatPageFallback() {
    const t = useT();

    return (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t("web.generated.bd8c41c802")}
        </div>
    );
}
