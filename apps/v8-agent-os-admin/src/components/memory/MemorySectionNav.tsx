"use client";

import Link from "next/link";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import { useDebugMode } from "@/lib/useDebugMode";

export type MemorySectionKey =
    | "context"
    | "preferences"
    | "logs"
    | "knowledge"
    | "workflows"
    | "artifacts"
    | "graph"
    | "agent"
    | "upload"
    | "config"
    | "runtime";

const MEMORY_SECTION_ITEMS: Array<{ key: MemorySectionKey; href: string; label: string }> = [
    { key: "context", href: "/admin/memory?tab=context", label: "components.memory.MemorySectionNav.contextManagement" },
    { key: "preferences", href: "/admin/memory?tab=preferences", label: "components.memory.MemorySectionNav.k79d67bc6" },
    { key: "logs", href: "/admin/memory?tab=logs", label: "components.memory.MemorySectionNav.logsLedger" },
    { key: "knowledge", href: "/admin/memory?tab=knowledge", label: "components.memory.MemorySectionNav.k4a8a8d88" },
    { key: "workflows", href: "/admin/memory?tab=workflows", label: "components.memory.MemorySectionNav.workflowMemory" },
    { key: "artifacts", href: "/admin/memory?tab=artifacts", label: "Artifacts" },
    { key: "graph", href: "/admin/memory?tab=graph", label: "components.memory.MemorySectionNav.k7fe6a3d0" },
    { key: "agent", href: "/admin/memory?tab=agent", label: "components.memory.MemorySectionNav.k24f221bf" },
    { key: "upload", href: "/admin/memory?tab=upload", label: "components.memory.MemorySectionNav.kdad82071" },
    { key: "config", href: "/admin/memory?tab=config", label: "components.memory.MemorySectionNav.k0e1a1cef" },
    { key: "runtime", href: "/admin/memory?tab=runtime", label: "components.memory.MemorySectionNav.kc9691c8b" },
];

export default function MemorySectionNav({ activeKey }: { activeKey: MemorySectionKey }) {
    const t = useT();
    const [debugMode] = useDebugMode();

    const visibleItems = MEMORY_SECTION_ITEMS.filter((item) => {
        if (!debugMode) {
            return !["logs", "runtime", "config"].includes(item.key);
        }
        return true;
    });

    return (
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-center gap-2 text-center">
            {visibleItems.map((item) => {
                const active = item.key === activeKey;
                return (
                    <Link
                        key={item.key}
                        href={item.href}
                        className={cn(
                            "inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                            active
                                ? "bg-background text-foreground shadow"
                                : "text-muted-foreground hover:bg-background/70 hover:text-foreground",
                        )}
                    >
                        {t(item.label)}
                    </Link>
                );
            })}
        </div>
    );
}
