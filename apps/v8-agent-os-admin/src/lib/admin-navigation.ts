import type { LucideIcon } from "lucide-react";
import {
    Blocks,
    Bot,
    Brain,
    Building2,
    FolderTree,
    Gauge,
    Globe2,
    LayoutDashboard,
    MessageSquare,
    Mic,
    ShieldCheck,
    SlidersHorizontal,
    Sparkles,
    Users,
    Workflow,
    Wrench,
} from "lucide-react";
import { LocalizedText, lt } from "@/lib/locale";

export type AdminNavBadge = {
    label: LocalizedText;
    tone: "beta" | "dev";
};

export type AdminNavItem = {
    title: LocalizedText;
    href: string;
    icon: LucideIcon;
    description: LocalizedText;
    badge?: AdminNavBadge;
};

export type AdminNavGroup = {
    id: string;
    title: LocalizedText;
    items: AdminNavItem[];
};

export const ADMIN_NAV_GROUPS: AdminNavGroup[] = [
    {
        id: "overview",
        title: lt("OVERVIEW", "OVERVIEW"),
        items: [
            {
                title: lt("DASHBOARD", "DASHBOARD"),
                href: "/admin",
                icon: LayoutDashboard,
                description: lt("系统总览、消息入口与关键状态。", "System overview, message entry, and key status."),
            },
            {
                title: lt("MODELS", "MODELS"),
                href: "/admin/model-hub",
                icon: Sparkles,
                description: lt("供应商、模型目录与连接健康。", "Providers, model catalogues, and connection health."),
            },
            {
                title: lt("OPERATIONS", "OPERATIONS"),
                href: "/admin/operations-center",
                icon: Gauge,
                description: lt("运行状态、异常与恢复入口。", "Runtime status, incidents, and recovery entry."),
            },
            {
                title: lt("USERS", "USERS"),
                href: "/admin/users",
                icon: Users,
                description: lt("后台用户与访问权限。", "Admin users and access control."),
            },
        ],
    },
    {
        id: "runtimes",
        title: lt("RUNTIMES", "RUNTIMES"),
        items: [
            {
                title: lt("CHAT RUNTIME", "CHAT RUNTIME"),
                href: "/admin/chat-runtime",
                icon: MessageSquare,
                description: lt("主理人、子 Agent 与聊天编排。", "Lead settings, subagents, and chat orchestration."),
            },
            {
                title: lt("MEMORY RUNTIME", "MEMORY RUNTIME"),
                href: "/admin/memory",
                icon: Brain,
                description: lt("长期记忆、知识与图谱。", "Long-term memory, knowledge, and graph."),
            },
            {
                title: lt("AUTOMATION RUNTIME", "AUTOMATION RUNTIME"),
                href: "/admin/automation",
                icon: Workflow,
                description: lt("Hooks、Cron 与自动化触发。", "Hooks, cron, and automation triggers."),
            },
            {
                title: lt("EXTENSIONS", "EXTENSIONS"),
                href: "/admin/extensions",
                icon: Blocks,
                description: lt("Skills、MCP 与扩展生态。", "Skills, MCP, and extension ecosystem."),
            },
            {
                title: lt("PLUGIN HOST RUNTIME", "PLUGIN HOST RUNTIME"),
                href: "/admin/plugin-host",
                icon: Blocks,
                description: lt("OpenClaw 桥接、渠道与外部工具。", "OpenClaw bridge, channels, and external tools."),
                badge: { label: lt("beta", "beta"), tone: "beta" },
            },
            {
                title: lt("COMPUTER USE RUNTIME", "COMPUTER USE RUNTIME"),
                href: "/admin/desktop-automation",
                icon: Wrench,
                description: lt("桌面控制、视觉与环境感知。", "Desktop control, vision, and environment sensing."),
                badge: { label: lt("beta", "beta"), tone: "beta" },
            },
            {
                title: lt("RPA RUNTIME", "RPA RUNTIME"),
                href: "/admin/rpa",
                icon: Bot,
                description: lt("流程发现、执行与回退。", "Process discovery, execution, and rollback."),
                badge: { label: lt("beta", "beta"), tone: "beta" },
            },
            {
                title: lt("NETWORK SUPERVISOR RUNTIME", "NETWORK SUPERVISOR RUNTIME"),
                href: "/admin/network-supervisor-runtime",
                icon: Globe2,
                description: lt("局域网/广域网组网协作入口。", "LAN/WAN supervisor collaboration entry."),
                badge: { label: lt("beta", "beta"), tone: "beta" },
            },
        ],
    },
    {
        id: "capabilities",
        title: lt("CAPABILITIES", "CAPABILITIES"),
        items: [
            {
                title: lt("CONTEXT", "CONTEXT"),
                href: "/admin/context",
                icon: FolderTree,
                description: lt("上下文治理、引用与 RAG 注入策略。", "Context governance, references, and RAG injection policy."),
            },
            {
                title: lt("AUDIO", "AUDIO"),
                href: "/admin/audio",
                icon: Mic,
                description: lt("语音识别、语音合成与音频配置。", "Speech recognition, synthesis, and audio config."),
            },
        ],
    },
    {
        id: "platform",
        title: lt("PLATFORM", "PLATFORM"),
        items: [
            {
                title: lt("PROJECTS & WORKSPACES", "PROJECTS & WORKSPACES"),
                href: "/admin/projects-workspaces",
                icon: Building2,
                description: lt("项目注册表与工作区绑定。", "Project registry and workspace bindings."),
            },
            {
                title: lt("SAFETY", "SAFETY"),
                href: "/admin/safety-control",
                icon: ShieldCheck,
                description: lt("安全护栏、人工确认与风险策略。", "Safeguards, approvals, and risk policies."),
            },
            {
                title: lt("SYSTEM BASE", "SYSTEM BASE"),
                href: "/admin/system-base",
                icon: SlidersHorizontal,
                description: lt("服务地址、密钥与基础依赖。", "Service endpoints, secrets, and base dependencies."),
            },
        ],
    },
];

export const ADMIN_REDIRECT_MAP: Record<string, string> = {
    "/admin/models": "/admin/model-hub",
    "/admin/settings": "/admin/system-base",
    "/admin/safety": "/admin/safety-control",
    "/admin/stability": "/admin/operations-center",
    "/admin/projects": "/admin/projects-workspaces",
    "/admin/system-misc": "/admin/system-base",
    "/admin/agents": "/admin/subagents",
};

const ALL_ITEMS = ADMIN_NAV_GROUPS.flatMap((group) => group.items);

export function getAdminNavItem(pathname: string | null | undefined) {
    const normalized = pathname || "/admin";
    const exactMatch = ALL_ITEMS.find((item) => item.href === normalized);
    if (exactMatch) return exactMatch;

    return (
        [...ALL_ITEMS]
            .filter((item) => normalized.startsWith(item.href) && item.href !== "/admin")
            .sort((a, b) => b.href.length - a.href.length)[0] || ALL_ITEMS[0]
    );
}
