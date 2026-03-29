import { ADMIN_NAV_GROUPS, type AdminNavItem } from "@/lib/admin-navigation";
import { lt, type LocalizedText } from "@/lib/locale";
import { RUNTIME_CONTROL_HREF } from "@/lib/runtime-admin";

export type AdminTopbarSearchEntry = {
    id: string;
    title: LocalizedText;
    subtitle?: LocalizedText;
    href: string;
    aliases?: string[];
    priority?: number;
};

export type AdminTopbarSearchResult = AdminTopbarSearchEntry & {
    matchMode: "default" | "exact" | "fuzzy";
    score: number;
};

function flattenNavItems() {
    return ADMIN_NAV_GROUPS.flatMap((group) =>
        group.items.map((item) => ({
            ...item,
            groupTitle: group.title,
        })),
    );
}

const NAV_ENTRIES: AdminTopbarSearchEntry[] = flattenNavItems().map((item, index) => ({
    id: item.href,
    title: item.title,
    subtitle: item.groupTitle,
    href: item.href,
    aliases: collectNavAliases(item),
    priority: index,
}));

const MEMORY_TAB_ENTRIES: AdminTopbarSearchEntry[] = [
    { id: "memory-preferences", title: lt("偏好项", "Preferences"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=preferences", aliases: ["memory preferences", "prefs", "preference"] },
    { id: "memory-projects", title: lt("项目注册表", "Project registry"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=projects", aliases: ["project registry", "projects"] },
    { id: "memory-knowledge", title: lt("知识库", "Knowledge"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=knowledge", aliases: ["knowledge base", "knowledge items"] },
    { id: "memory-artifacts", title: lt("Artifacts", "Artifacts"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=artifacts", aliases: ["artifact explorer", "artifacts"] },
    { id: "memory-graph", title: lt("知识图谱", "Graph"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=graph", aliases: ["knowledge graph", "graph", "entities"] },
    { id: "memory-agent", title: lt("记忆助手", "Assistant"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=agent", aliases: ["memory assistant", "assistant"] },
    { id: "memory-audit", title: lt("系统日志", "Logs"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=audit", aliases: ["audit", "logs", "system logs"] },
    { id: "memory-upload", title: lt("文档上传", "Upload"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=upload", aliases: ["upload", "documents"] },
    { id: "memory-config", title: lt("配置", "Config"), subtitle: lt("记忆管理", "Memory"), href: "/admin/memory?tab=config", aliases: ["memory config", "config"] },
];

const RUNTIME_ENTRIES: AdminTopbarSearchEntry[] = Object.entries(RUNTIME_CONTROL_HREF).map(([kind, href], index) => ({
    id: `runtime-${kind}`,
    title: lt(kind, kind),
    subtitle: lt("运行时", "Runtime"),
    href,
    aliases: [kind, kind.replace(/_/g, " "), `${kind} runtime`],
    priority: 200 + index,
}));

const MANUAL_ENTRIES: AdminTopbarSearchEntry[] = [
    { id: "plugin-host-bridge", title: lt("桥接状态", "Bridge status"), subtitle: lt("插件宿主", "Plugin Host"), href: "/admin/plugin-host", aliases: ["bridge", "handoff", "inbound", "plugin host bridge"], priority: 240 },
    { id: "runtime-ops-approvals", title: lt("待处理确认", "Pending approvals"), subtitle: lt("运行与问题", "Ops"), href: "/admin/operations-center?tab=approvals", aliases: ["approvals", "pending approvals"] , priority: 260 },
    { id: "runtime-ops-runs", title: lt("最近运行", "Recent runs"), subtitle: lt("运行与问题", "Ops"), href: "/admin/operations-center?tab=runs", aliases: ["runs", "recent runs"], priority: 261 },
    { id: "runtime-ops-logs", title: lt("高级日志", "Logs"), subtitle: lt("运行与问题", "Ops"), href: "/admin/operations-center?tab=advanced", aliases: ["ops logs", "runtime logs"], priority: 262 },
];

const SEARCH_ENTRIES: AdminTopbarSearchEntry[] = [...NAV_ENTRIES, ...RUNTIME_ENTRIES, ...MEMORY_TAB_ENTRIES, ...MANUAL_ENTRIES];

function collectNavAliases(item: AdminNavItem & { groupTitle?: LocalizedText }) {
    const aliases = [item.href];
    if (item.href === "/admin/plugin-host") {
        aliases.push("plugin host", "bridge", "handoff", "openclaw");
    }
    if (item.href === "/admin/desktop-automation") {
        aliases.push("computer_use", "computer use", "desktop automation");
    }
    if (item.href === "/admin/operations-center") {
        aliases.push("ops", "operations", "runtime health");
    }
    if (item.href === "/admin/model-hub") {
        aliases.push("models", "providers", "model control plane");
    }
    return aliases;
}

function normalize(value: string) {
    return value
        .trim()
        .toLowerCase()
        .replace(/[?=&/]+/g, " ")
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ");
}

function compact(value: string) {
    return normalize(value).replace(/\s+/g, "");
}

function collectTerms(entry: AdminTopbarSearchEntry) {
    return [
        entry.href,
        entry.title["zh-CN"],
        entry.title.en,
        entry.subtitle?.["zh-CN"] || "",
        entry.subtitle?.en || "",
        ...(entry.aliases || []),
    ].filter(Boolean);
}

function computeMatch(entry: AdminTopbarSearchEntry, query: string) {
    const normalizedQuery = normalize(query);
    const compactQuery = compact(query);
    const terms = collectTerms(entry);
    let score = Number.POSITIVE_INFINITY;
    let matchMode: "exact" | "fuzzy" | null = null;

    for (const term of terms) {
        const normalizedTerm = normalize(term);
        const compactTerm = compact(term);
        if (!normalizedTerm) continue;

        if (
            normalizedTerm === normalizedQuery
            || compactTerm === compactQuery
            || normalizedTerm.startsWith(normalizedQuery)
            || compactTerm.startsWith(compactQuery)
        ) {
            const nextScore = normalizedTerm === normalizedQuery || compactTerm === compactQuery ? 0 : 1;
            if (nextScore < score) {
                score = nextScore;
                matchMode = "exact";
            }
            continue;
        }

        const includeIndex = normalizedTerm.indexOf(normalizedQuery);
        const compactIndex = compactTerm.indexOf(compactQuery);
        if (includeIndex >= 0 || compactIndex >= 0) {
            const nextScore = Math.min(
                includeIndex >= 0 ? includeIndex + 10 : Number.POSITIVE_INFINITY,
                compactIndex >= 0 ? compactIndex + 12 : Number.POSITIVE_INFINITY,
            );
            if (nextScore < score) {
                score = nextScore;
                matchMode = "fuzzy";
            }
        }
    }

    return matchMode ? { matchMode, score } : null;
}

export function searchAdminTopbarEntries(query: string, limit = 8): AdminTopbarSearchResult[] {
    const normalizedQuery = normalize(query);
    if (!normalizedQuery) {
        return SEARCH_ENTRIES
            .slice()
            .sort((left, right) => (left.priority ?? 999) - (right.priority ?? 999))
            .slice(0, limit)
            .map((entry) => ({
                ...entry,
                matchMode: "default",
                score: entry.priority ?? 999,
            }));
    }

    const results: AdminTopbarSearchResult[] = [];
    SEARCH_ENTRIES.forEach((entry) => {
        const match = computeMatch(entry, normalizedQuery);
        if (!match) {
            return;
        }
        results.push({ ...entry, ...match });
    });

    return results
        .sort((left, right) => {
            const modeOrder = { exact: 0, fuzzy: 1, default: 2 };
            if (modeOrder[left.matchMode] !== modeOrder[right.matchMode]) {
                return modeOrder[left.matchMode] - modeOrder[right.matchMode];
            }
            if (left.score !== right.score) {
                return left.score - right.score;
            }
            return (left.priority ?? 999) - (right.priority ?? 999);
        })
        .slice(0, limit);
}
