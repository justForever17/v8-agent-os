"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { useResolveText } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { cn } from "@/lib/utils";

export function AdvancedSection({
    title = "shared.advancedSection.moreOptions",
    description,
    defaultOpen = false,
    children,
}: {
    title?: string;
    description?: string;
    defaultOpen?: boolean;
    children: React.ReactNode;
}) {
    const [open, setOpen] = useState(defaultOpen);
    const resolveText = useResolveText();

    return (
        <div className="space-y-4">
            <Button
                type="button"
                variant="outline"
                className="h-auto w-full justify-between rounded-2xl border-slate-200 bg-white px-4 py-4 text-left shadow-sm"
                onClick={() => setOpen((current) => !current)}
            >
                <AdminHoverInfo content={description ? resolveText(description) : undefined}>
                    <span className="text-sm font-medium text-slate-900">{resolveText(title)}</span>
                </AdminHoverInfo>
                <ChevronDown className={cn("h-4 w-4 text-slate-500 transition-transform", open ? "rotate-180" : "")} />
            </Button>
            {open ? children : null}
        </div>
    );
}
