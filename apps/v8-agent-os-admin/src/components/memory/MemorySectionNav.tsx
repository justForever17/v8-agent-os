"use client";

import Link from "next/link";

import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { cn } from "@/lib/utils";

export type MemorySectionKey =
    | "preferences"
    | "projects"
    | "knowledge"
    | "artifacts"
    | "graph"
    | "agent"
    | "audit"
    | "upload"
    | "config"
    | "runtime";

const MEMORY_SECTION_ITEMS: Array<{ key: MemorySectionKey; href: string; label: ReturnType<typeof lt> | string }> = [
    { key: "preferences", href: "/admin/memory?tab=preferences", label: lt("偏好管理", "Preferences") },
    { key: "projects", href: "/admin/memory?tab=projects", label: lt("项目注册表", "Project registry") },
    { key: "knowledge", href: "/admin/memory?tab=knowledge", label: lt("知识库", "Knowledge") },
    { key: "artifacts", href: "/admin/memory?tab=artifacts", label: "Artifacts" },
    { key: "graph", href: "/admin/memory?tab=graph", label: lt("知识图谱", "Graph") },
    { key: "agent", href: "/admin/memory?tab=agent", label: lt("记忆助手", "Memory agent") },
    { key: "audit", href: "/admin/memory?tab=audit", label: lt("系统日志", "Logs") },
    { key: "upload", href: "/admin/memory?tab=upload", label: lt("文档上传", "Upload") },
    { key: "config", href: "/admin/memory?tab=config", label: lt("配置", "Config") },
    { key: "runtime", href: "/admin/memory?tab=runtime", label: lt("运行诊断", "Runtime diagnostics") },
];

export default function MemorySectionNav({ activeKey }: { activeKey: MemorySectionKey }) {
    const t = useT();

    return (
        <div className="flex flex-wrap items-center gap-2">
            {MEMORY_SECTION_ITEMS.map((item) => {
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
