import { ADMIN_NAV_GROUPS, type AdminNavItem } from "@/lib/admin-navigation";
import { getTranslationVariants } from "@/lib/locale";
import { RUNTIME_CONTROL_HREF } from "@/lib/runtime-admin";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
export type AdminTopbarSearchEntry = {
  id: string;
  title: string;
  subtitle?: string;
  href: string;
  aliases?: string[];
  priority?: number;
};
export type AdminTopbarSearchResult = AdminTopbarSearchEntry & {
  matchMode: "default" | "exact" | "fuzzy";
  score: number;
};
function flattenNavItems() {
  return ADMIN_NAV_GROUPS.flatMap(group => group.items.map(item => ({
    ...item,
    groupTitle: group.title
  })));
}
const NAV_ENTRIES: AdminTopbarSearchEntry[] = flattenNavItems().map((item, index) => ({
  id: item.href,
  title: item.title,
  subtitle: item.groupTitle,
  href: item.href,
  aliases: collectNavAliases(item),
  priority: index
}));
const MEMORY_TAB_ENTRIES: AdminTopbarSearchEntry[] = [{
  id: "memory-context",
  title: "components.memory.MemorySectionNav.contextManagement",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=context",
  aliases: ["context", "context management", "rag context", INTERNAL_READABLE.k5d0afe63f2]
}, {
  id: "memory-preferences",
  title: "components.layout.admin.topbar.search.ka5cb9483",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=preferences",
  aliases: ["memory preferences", "prefs", "preference"]
}, {
  id: "memory-logs",
  title: "components.memory.MemorySectionNav.logsLedger",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=logs",
  aliases: ["memory logs", "daily logs", "journal", INTERNAL_READABLE.k6a167136fc, "daily summaries"]
}, {
  id: "memory-workflows",
  title: "components.memory.MemorySectionNav.workflowMemory",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=workflows",
  aliases: ["workflow memory", "behavior memory", "memory workflows", "workflows"]
}, {
  id: "memory-knowledge",
  title: "components.layout.admin.topbar.search.k4a8a8d88",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=knowledge",
  aliases: ["knowledge base", "knowledge items"]
}, {
  id: "memory-artifacts",
  title: "components.layout.admin.topbar.search.k2ed10a6e",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=artifacts",
  aliases: ["artifact explorer", "artifacts"]
}, {
  id: "memory-graph",
  title: "components.layout.admin.topbar.search.k7fe6a3d0",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=graph",
  aliases: ["knowledge graph", "graph", "entities"]
}, {
  id: "memory-agent",
  title: "components.layout.admin.topbar.search.ka46db0a3",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=agent",
  aliases: ["memory assistant", "assistant"]
}, {
  id: "memory-upload",
  title: "components.layout.admin.topbar.search.kdad82071",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=upload",
  aliases: ["upload", "documents"]
}, {
  id: "memory-config",
  title: "components.layout.admin.topbar.search.k0e1a1cef",
  subtitle: "components.layout.admin.topbar.search.kd5b4901a",
  href: "/admin/memory?tab=config",
  aliases: ["memory config", "config"]
}];
const RUNTIME_ENTRIES: AdminTopbarSearchEntry[] = Object.entries(RUNTIME_CONTROL_HREF).map(([kind, href], index) => ({
  id: `runtime-${kind}`,
  title: kind,
  subtitle: "components.layout.admin.topbar.search.kf4928997",
  href,
  aliases: [kind, kind.replace(/_/g, " "), `${kind} runtime`],
  priority: 200 + index
}));
const MANUAL_ENTRIES: AdminTopbarSearchEntry[] = [{
  id: "plugin-host-bridge",
  title: "components.layout.admin.topbar.search.k03c86153",
  subtitle: "components.layout.admin.topbar.search.k4b9cbbff",
  href: "/admin/plugin-host",
  aliases: ["bridge", "handoff", "inbound", "plugin host bridge"],
  priority: 240
}, {
  id: "runtime-ops-approvals",
  title: "components.layout.admin.topbar.search.k61329e7f",
  subtitle: "components.layout.admin.topbar.search.kd71b1ac9",
  href: "/admin/operations-center?tab=approvals",
  aliases: ["approvals", "pending approvals"],
  priority: 260
}, {
  id: "runtime-ops-runs",
  title: "components.layout.admin.topbar.search.k1a586b06",
  subtitle: "components.layout.admin.topbar.search.kd71b1ac9",
  href: "/admin/operations-center?tab=runs",
  aliases: ["runs", "recent runs"],
  priority: 261
}, {
  id: "runtime-ops-logs",
  title: "components.layout.admin.topbar.search.kdce17454",
  subtitle: "components.layout.admin.topbar.search.kd71b1ac9",
  href: "/admin/operations-center?tab=advanced",
  aliases: ["ops logs", "runtime logs"],
  priority: 262
}];
const SEARCH_ENTRIES: AdminTopbarSearchEntry[] = [...NAV_ENTRIES, ...RUNTIME_ENTRIES, ...MEMORY_TAB_ENTRIES, ...MANUAL_ENTRIES];
function collectNavAliases(item: AdminNavItem & {
  groupTitle?: string;
}) {
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
  return value.trim().toLowerCase().replace(/[?=&/]+/g, " ").replace(/[_-]+/g, " ").replace(/\s+/g, " ");
}
function compact(value: string) {
  return normalize(value).replace(/\s+/g, "");
}
function collectTerms(entry: AdminTopbarSearchEntry) {
  return [entry.href, ...getTranslationVariants(entry.title), ...(entry.subtitle ? getTranslationVariants(entry.subtitle) : []), ...(entry.aliases || [])].filter(Boolean);
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
    if (normalizedTerm === normalizedQuery || compactTerm === compactQuery || normalizedTerm.startsWith(normalizedQuery) || compactTerm.startsWith(compactQuery)) {
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
      const nextScore = Math.min(includeIndex >= 0 ? includeIndex + 10 : Number.POSITIVE_INFINITY, compactIndex >= 0 ? compactIndex + 12 : Number.POSITIVE_INFINITY);
      if (nextScore < score) {
        score = nextScore;
        matchMode = "fuzzy";
      }
    }
  }
  return matchMode ? {
    matchMode,
    score
  } : null;
}
export function searchAdminTopbarEntries(query: string, limit = 8): AdminTopbarSearchResult[] {
  const normalizedQuery = normalize(query);
  if (!normalizedQuery) {
    return SEARCH_ENTRIES.slice().sort((left, right) => (left.priority ?? 999) - (right.priority ?? 999)).slice(0, limit).map(entry => ({
      ...entry,
      matchMode: "default",
      score: entry.priority ?? 999
    }));
  }
  const results: AdminTopbarSearchResult[] = [];
  SEARCH_ENTRIES.forEach(entry => {
    const match = computeMatch(entry, normalizedQuery);
    if (!match) {
      return;
    }
    results.push({
      ...entry,
      ...match
    });
  });
  return results.sort((left, right) => {
    const modeOrder = {
      exact: 0,
      fuzzy: 1,
      default: 2
    };
    if (modeOrder[left.matchMode] !== modeOrder[right.matchMode]) {
      return modeOrder[left.matchMode] - modeOrder[right.matchMode];
    }
    if (left.score !== right.score) {
      return left.score - right.score;
    }
    return (left.priority ?? 999) - (right.priority ?? 999);
  }).slice(0, limit);
}
