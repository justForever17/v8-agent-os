import { AlertCircle, CheckCircle2, Info } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { LocalizedText } from "@/lib/locale";
import { cn } from "@/lib/utils";

const toneMap = {
    info: {
        icon: Info,
        box: "border-sky-200 bg-sky-50 text-sky-800",
    },
    success: {
        icon: CheckCircle2,
        box: "border-emerald-200 bg-emerald-50 text-emerald-800",
    },
    warning: {
        icon: AlertCircle,
        box: "border-amber-200 bg-amber-50 text-amber-800",
    },
};

export function StatusNotice({
    title,
    description,
    tone = "info",
}: {
    title: LocalizedText | string;
    description?: LocalizedText | string;
    tone?: keyof typeof toneMap;
}) {
    const t = useT();
    const Icon = toneMap[tone].icon;

    return (
        <div className={cn("rounded-2xl border px-4 py-3", toneMap[tone].box)}>
            <div className="flex items-start gap-3">
                <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-1">
                    <div className="text-sm font-medium">{t(title)}</div>
                    {description ? <div className="text-xs leading-5 opacity-90">{t(description)}</div> : null}
                </div>
            </div>
        </div>
    );
}
