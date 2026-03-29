"use client";

import { CheckCircle2, Loader2 } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { LocalizedText } from "@/lib/locale";

export function InlineSaveState({
    saving,
    saved,
    label = "保存状态",
}: {
    saving: boolean;
    saved: boolean;
    label?: LocalizedText | string;
}) {
    const t = useT();
    const localizedLabel = t(label);

    return (
        <div className="flex items-center gap-2 text-xs text-slate-500">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {!saving && saved ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : null}
            <span>
                {saving ? `${localizedLabel}：${t("保存中")}` : saved ? `${localizedLabel}：${t("已保存")}` : `${localizedLabel}：${t("未变更")}`}
            </span>
        </div>
    );
}
