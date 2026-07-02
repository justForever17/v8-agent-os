import type { LucideIcon } from "lucide-react";
import {
    Activity,
    Blocks,
    Bot,
    Brain,
    Building2,
    Code2,
    Gauge,
    Globe2,
    LayoutDashboard,
    MessageSquare,
    Search,
    ShieldCheck,
    SlidersHorizontal,
    Sparkles,
    Users,
    Workflow,
    Wrench,
} from "lucide-react";
import { PRODUCT_VOCABULARY, type ProductVocabularyKey } from "./product-vocabulary";

export type AdminNavBadge = {
    label: string;
    tone: "beta" | "dev";
};

export type AdminNavItem = {
    title: string;
    href: string;
    icon: LucideIcon;
    description: string;
    productVocabularyId?: ProductVocabularyKey;
    canonicalId?: string;
    badge?: AdminNavBadge;
};

export type AdminNavGroup = {
    id: string;
    title: string;
    canonicalId?: string;
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
        canonicalId: "runtime_group",
        items: [
            {
                title: "lib.admin.navigation.ka46f7182",
                href: "/admin/chat-runtime",
                icon: MessageSquare,
                description: "lib.admin.navigation.k38ee6957",
                productVocabularyId: "chat",
                canonicalId: PRODUCT_VOCABULARY.chat.canonicalId,
            },
            {
                title: "lib.admin.navigation.k12e7fe1d",
                href: "/admin/memory",
                icon: Brain,
                description: "lib.admin.navigation.kc6277f77",
                productVocabularyId: "memory",
                canonicalId: PRODUCT_VOCABULARY.memory.canonicalId,
            },
            {
                title: "lib.admin.navigation.k924ec203",
                href: "/admin/automation",
                icon: Workflow,
                description: "lib.admin.navigation.k3eb7c0bc",
                productVocabularyId: "automation",
                canonicalId: PRODUCT_VOCABULARY.automation.canonicalId,
            },
            {
                title: "lib.admin.navigation.engineeringLaneTitle",
                href: "/admin/engineering-lane",
                icon: Code2,
                description: "lib.admin.navigation.engineeringLaneDescription",
                productVocabularyId: "engineering",
                canonicalId: PRODUCT_VOCABULARY.engineering.canonicalId,
            },
            {
                title: "lib.admin.navigation.k4a6c7a20",
                href: "/admin/extensions",
                icon: Blocks,
                description: "lib.admin.navigation.kc841e62a",
                productVocabularyId: "extensions",
                canonicalId: PRODUCT_VOCABULARY.extensions.canonicalId,
            },
            {
                title: "lib.admin.navigation.researchRuntimeTitle",
                href: "/admin/research-runtime",
                icon: Search,
                description: "lib.admin.navigation.researchRuntimeDescription",
                productVocabularyId: "research",
                canonicalId: PRODUCT_VOCABULARY.research.canonicalId,
            },
            {
                title: "lib.admin.navigation.k64a90628",
                href: "/admin/plugin-host",
                icon: Blocks,
                description: "lib.admin.navigation.k3f9710f0",
                productVocabularyId: "pluginHost",
                canonicalId: PRODUCT_VOCABULARY.pluginHost.canonicalId,
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.kc32f16d1",
                href: "/admin/desktop-automation",
                icon: Wrench,
                description: "lib.admin.navigation.k76403821",
                productVocabularyId: "computerUse",
                canonicalId: PRODUCT_VOCABULARY.computerUse.canonicalId,
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.kc278e600",
                href: "/admin/rpa",
                icon: Bot,
                description: "lib.admin.navigation.kede68047",
                productVocabularyId: "rpa",
                canonicalId: PRODUCT_VOCABULARY.rpa.canonicalId,
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.k45a604ec",
                href: "/admin/network-supervisor-runtime",
                icon: Globe2,
                description: "lib.admin.navigation.k63312a13",
                productVocabularyId: "network",
                canonicalId: PRODUCT_VOCABULARY.network.canonicalId,
                badge: { label: "lib.admin.navigation.kdb4add74", tone: "beta" },
            },
            {
                title: "lib.admin.navigation.creativeMediaTitle",
                href: "/admin/creative-media",
                icon: Sparkles,
                description: "lib.admin.navigation.creativeMediaDescription",
                productVocabularyId: "creativeMedia",
                canonicalId: PRODUCT_VOCABULARY.creativeMedia.canonicalId,
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
                productVocabularyId: "runtimeGovernance",
                canonicalId: PRODUCT_VOCABULARY.runtimeGovernance.canonicalId,
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
    "/admin/engineering-runtime": "/admin/engineering-lane",
    "/admin/research": "/admin/research-runtime",
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
