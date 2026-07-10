"use client";

import { CheckCircle2, Loader2 } from "lucide-react";
import { useResolveText, useT } from "@/components/providers/LocaleProvider";

export function InlineSaveState({
    saving,
    saved,
    label = "shared.inlineSaveState.label",
}: {
    saving: boolean;
    saved: boolean;
    label?: string;
}) {
    const t = useT();
    const resolveText = useResolveText();
    const localizedLabel = resolveText(label);

    return (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {!saving && saved ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : null}
            <span>
                {saving
                    ? `${localizedLabel}：${t("shared.inlineSaveState.saving")}`
                    : saved
                        ? `${localizedLabel}：${t("shared.inlineSaveState.saved")}`
                        : `${localizedLabel}：${t("shared.inlineSaveState.idle")}`}
            </span>
        </div>
    );
}
