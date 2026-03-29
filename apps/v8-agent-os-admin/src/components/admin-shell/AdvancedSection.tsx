"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { LocalizedText } from "@/lib/locale";
import { cn } from "@/lib/utils";

export function AdvancedSection({
    title = "更多选项",
    description,
    defaultOpen = false,
    children,
}: {
    title?: LocalizedText | string;
    description?: LocalizedText | string;
    defaultOpen?: boolean;
    children: React.ReactNode;
}) {
    const [open, setOpen] = useState(defaultOpen);
    const t = useT();

    return (
        <div className="space-y-4">
            <Button
                type="button"
                variant="outline"
                className="h-auto w-full justify-between rounded-2xl border-slate-200 bg-white px-4 py-4 text-left shadow-sm"
                onClick={() => setOpen((current) => !current)}
            >
                <div className="space-y-1">
                    <div className="text-sm font-medium text-slate-900">{t(title)}</div>
                    {description ? <div className="text-xs leading-5 text-slate-500">{t(description)}</div> : null}
                </div>
                <ChevronDown className={cn("h-4 w-4 text-slate-500 transition-transform", open ? "rotate-180" : "")} />
            </Button>
            {open ? children : null}
        </div>
    );
}
