import type { LucideIcon } from "lucide-react";
import {
    Activity,
    Blocks,
    Bot,
    Brain,
    Building2,
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
export type AdminNavBadge = {
    label: string;
    tone: "beta" | "dev";
};

export type AdminNavItem = {
    title: string;
    href: string;
    icon: LucideIcon;
    description: string;
    badge?: AdminNavBadge;
};

export type AdminNavGroup = {
    id: string;
    title: string;
    items: AdminNavItem[];
};

export const ADMIN_NAV_GROUPS: AdminNavGroup[] = [
    {
        id: "overview",
        title: "lib.admin.navigation.k44e34d5c",
        items: [
            {
                title: "lib.admin.navigation.kec921d94",
                href: "/admin",
                icon: LayoutDashboard,
                description: "lib.admin.navigation.k564ca1a5",
            },
            {
                title: "lib.admin.navigation.k3642fc46",
                href: "/admin/model-hub",
                icon: Sparkles,
                description: "lib.admin.navigation.ke5b09aa7",
            },
            {
                title: "lib.admin.navigation.k6f64cd4f",
                href: "/admin/users",
                icon: Users,
                description: "lib.admin.navigation.k4f4d831f",
            },
        ],
    },
    {
        id: "runtimes",
        title: "lib.admin.navigation.k4d4cc3a3",
        items: [
            {
                title: "lib.admin.navigation.ka46f7182",
                href: "/admin/chat-runtime",
                icon: MessageSquare,
                description: "lib.admin.navigation.k38ee6957",
            },
            {
                title: "lib.admin.navigation.k12e7fe1d",
                href: "/admin/memory",
                icon: Brain,
                description: "lib.admin.navigation.kc6277f77",
            },
            {
                title: "lib.admin.navigation.k924ec203",
                href: "/admin/automation",
                icon: Workflow,
                description: "lib.admin.navigation.k3eb7c0bc",
            },
            {
                title: "lib.admin.navigation.k4a6c7a20",
                href: "/admin/extensions",
                icon: Blocks,
                description: "lib.admin.navigation.kc841e62a",
            },
            {
                title: "lib.admin.navigation.k64a90628",
                href: "/admin/plugin-host",
                icon: Blocks,
                description: "lib.admin.navigation.k3f9710f0",
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.kc32f16d1",
                href: "/admin/desktop-automation",
                icon: Wrench,
                description: "lib.admin.navigation.k76403821",
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.kc278e600",
                href: "/admin/rpa",
                icon: Bot,
                description: "lib.admin.navigation.kede68047",
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.k45a604ec",
                href: "/admin/network-supervisor-runtime",
                icon: Globe2,
                description: "lib.admin.navigation.k63312a13",
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
        ],
    },
    {
        id: "capabilities",
        title: "lib.admin.navigation.k7e688826",
        items: [
            {
                title: "lib.admin.navigation.runtimeGovernanceTitle",
                href: "/admin/runtime-governance",
                icon: Activity,
                description: "lib.admin.navigation.runtimeGovernanceDescription",
            },
            {
                title: "lib.admin.navigation.k4f3e92b5",
                href: "/admin/operations-center",
                icon: Gauge,
                description: "lib.admin.navigation.kc0233cc5",
            },
            {
                title: "lib.admin.navigation.k80c93722",
                href: "/admin/safety-control",
                icon: ShieldCheck,
                description: "lib.admin.navigation.ka3de25ee",
            },
            {
                title: "lib.admin.navigation.kcef5c2ee",
                href: "/admin/audio",
                icon: Mic,
                description: "lib.admin.navigation.k9e990c4d",
            },
        ],
    },
    {
        id: "platform",
        title: "lib.admin.navigation.k3ff43c59",
        items: [
            {
                title: "lib.admin.navigation.ka03c6e04",
                href: "/admin/projects-workspaces",
                icon: Building2,
                description: "lib.admin.navigation.kcf00915a",
            },
            {
                title: "lib.admin.navigation.kdb0e699b",
                href: "/admin/system-base",
                icon: SlidersHorizontal,
                description: "lib.admin.navigation.k75b9814d",
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
