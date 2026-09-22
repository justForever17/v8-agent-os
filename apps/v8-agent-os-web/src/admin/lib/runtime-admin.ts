export const CORE_RUNTIME_KINDS = ["chat", "memory", "automation", "extensions", "network_supervisor"] as const;
export const CANONICAL_RUNTIME_KINDS = [
    "chat",
    "memory",
    "automation",
    "extensions",
    "network_supervisor",
    "engineering",
    "creative_media",
    "research",
    "plugin_manager",
    "computer_use",
    "rpa",
    "desktop_live",
] as const;
export const LOCKED_RUNTIME_KINDS = ["chat", "memory", "automation", "extensions"] as const;

const CORE_RUNTIME_KIND_SET = new Set<string>(CORE_RUNTIME_KINDS);
const CANONICAL_RUNTIME_KIND_SET = new Set<string>(CANONICAL_RUNTIME_KINDS);
const LOCKED_RUNTIME_KIND_SET = new Set<string>(LOCKED_RUNTIME_KINDS);
const RUNTIME_DISPLAY_NAME: Record<string, string> = {
    chat: "lib.runtime.admin.kf8dd96d4",
    memory: "lib.runtime.admin.k7fde78de",
    automation: "lib.runtime.admin.k30efe9db",
    extensions: "lib.runtime.admin.ke9f6bac4",
    engineering: "lib.runtime.admin.kengineeringRuntime",
    creative_media: "lib.runtime.admin.kcreativeMedia",
    research: "lib.runtime.admin.researchRuntime",
    plugin_manager: "lib.admin.navigation.k64a90628",
    computer_use: "lib.runtime.admin.ka5c7ec2e",
    rpa: "lib.runtime.admin.kaf3d0014",
    network_supervisor: "lib.runtime.admin.kfc56e3a6",
    desktop_live: "lib.runtime.admin.k2781e4c1",
    workflow: "lib.runtime.admin.kafb574aa",
    runtime_broker: "lib.runtime.admin.runtimeBroker",
};

export const RUNTIME_CONTROL_HREF: Record<string, string> = {
    chat: "/admin/chat-runtime",
    memory: "/admin/memory",
    extensions: "/admin/extensions",
    automation: "/admin/automation",
    engineering: "/admin/engineering-lane",
    plugin_manager: "/admin/plugins",
    creative_media: "/admin/creative-media",
    research: "/admin/research-runtime",
    rpa: "/admin/rpa",
    computer_use: "/admin/desktop-automation",
    desktop_live: "/admin/system-base",
    workflow: "/admin/memory?tab=workflows",
    network_supervisor: "/admin/network-supervisor-runtime",
};

export function isCoreRuntimeKind(kind: string) {
    return CORE_RUNTIME_KIND_SET.has(String(kind || "").trim());
}

export function isCanonicalRuntimeKind(kind: string) {
    return CANONICAL_RUNTIME_KIND_SET.has(String(kind || "").trim());
}

export function isLockedRuntimeKind(kind: string) {
    return LOCKED_RUNTIME_KIND_SET.has(String(kind || "").trim());
}

export function getRuntimeControlHref(kind: string) {
    const normalized = String(kind || "").trim();
    return RUNTIME_CONTROL_HREF[normalized] || `/admin/runtime-governance?kind=${encodeURIComponent(normalized)}`;
}

export function getRuntimeDisplayName(value: { kind: string; displayName?: string | null }) {
    const displayName = String(value.displayName || "").trim();
    if (displayName) return displayName;
    const normalizedKind = String(value.kind || "").trim();
    const fallback = RUNTIME_DISPLAY_NAME[normalizedKind];
    return fallback || normalizedKind;
}

export function getRuntimeDisplayText(kind: string): string {
    const normalizedKind = String(kind || "").trim();
    return RUNTIME_DISPLAY_NAME[normalizedKind] || normalizedKind;
}
