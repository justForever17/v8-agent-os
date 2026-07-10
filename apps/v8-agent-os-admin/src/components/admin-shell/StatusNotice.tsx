import { AlertCircle, CheckCircle2, Info } from "lucide-react";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useResolveText } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

const toneMap = {
    info: {
        icon: Info,
        box: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-500/35 dark:bg-sky-500/12 dark:text-sky-200",
    },
    success: {
        icon: CheckCircle2,
        box: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/35 dark:bg-emerald-500/12 dark:text-emerald-200",
    },
    warning: {
        icon: AlertCircle,
        box: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/35 dark:bg-amber-500/12 dark:text-amber-200",
    },
};

export function StatusNotice({
    title,
    description,
    tone = "info",
}: {
    title: string;
    description?: string;
    tone?: keyof typeof toneMap;
}) {
    const resolveText = useResolveText();
    const Icon = toneMap[tone].icon;

    return (
        <div className={cn("rounded-2xl border px-4 py-3", toneMap[tone].box)}>
            <div className="flex items-start gap-3">
                <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                <AdminHoverInfo content={description ? resolveText(description) : undefined} panelClassName="text-xs leading-5">
                    <span className="text-sm font-medium">{resolveText(title)}</span>
                </AdminHoverInfo>
            </div>
        </div>
    );
}
