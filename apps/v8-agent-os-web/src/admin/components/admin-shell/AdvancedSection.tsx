"use client";

import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";

import { useResolveText } from "@admin/components/providers/LocaleProvider";
import { Button } from "@admin/components/ui/button";
import { AdminHoverInfo } from "@admin/components/admin-shell/AdminHoverInfo";
import { cn } from "@admin/lib/utils";

export function AdvancedSection({
    title = "shared.advancedSection.moreOptions",
    description,
    defaultOpen = false,
    children,
    keepMounted = true,
}: {
    title?: string;
    description?: string;
    defaultOpen?: boolean;
    children: React.ReactNode;
    keepMounted?: boolean;
}) {
    const [open, setOpen] = useState(defaultOpen);
    const [visited, setVisited] = useState(defaultOpen);
    const contentId = useId();
    const resolveText = useResolveText();

    return (
        <div className="space-y-4">
            <Button
                type="button"
                variant="outline"
                className="h-auto min-h-[44px] w-full justify-between rounded-lg border-border bg-card px-4 py-3 text-left text-card-foreground shadow-none hover:bg-muted/50"
                aria-expanded={open}
                aria-controls={contentId}
                onClick={() => { setVisited(true); setOpen((current) => !current); }}
            >
                <span className="text-sm font-medium text-foreground">{resolveText(title)}</span>
                <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", open ? "rotate-180" : "")} />
            </Button>
            {description ? <AdminHoverInfo content={resolveText(description)}><span className="sr-only">{resolveText(title)}</span></AdminHoverInfo> : null}
            <div id={contentId} hidden={!open}>{open || (keepMounted && visited) ? children : null}</div>
        </div>
    );
}
