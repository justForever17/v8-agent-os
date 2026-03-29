export const CORE_RUNTIME_KINDS = ["chat", "memory", "automation", "extensions"] as const;

const CORE_RUNTIME_KIND_SET = new Set<string>(CORE_RUNTIME_KINDS);

export const RUNTIME_CONTROL_HREF: Record<string, string> = {
    chat: "/admin/chat-runtime",
    memory: "/admin/memory",
    extensions: "/admin/extensions",
    automation: "/admin/automation",
    plugin_host: "/admin/plugin-host",
    rpa: "/admin/rpa",
    computer_use: "/admin/desktop-automation",
    workflow: "/admin/workflow-runtime",
    network_supervisor: "/admin/network-supervisor-runtime",
};

export function isCoreRuntimeKind(kind: string) {
    return CORE_RUNTIME_KIND_SET.has(String(kind || "").trim());
}

export function getRuntimeControlHref(kind: string) {
    const normalized = String(kind || "").trim();
    return RUNTIME_CONTROL_HREF[normalized] || `/admin/runtime-governance?kind=${encodeURIComponent(normalized)}`;
}

export function getRuntimeDisplayName(value: { kind: string; displayName?: string | null }) {
    const displayName = String(value.displayName || "").trim();
    if (displayName) return displayName;
    return String(value.kind || "").trim();
}
