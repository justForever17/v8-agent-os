import { LocalizedText, lt } from "@/lib/locale";

export const CORE_RUNTIME_KINDS = ["chat", "memory", "automation", "extensions", "network_supervisor"] as const;
export const CANONICAL_RUNTIME_KINDS = [
    "chat",
    "memory",
    "automation",
    "extensions",
    "network_supervisor",
    "plugin_host",
    "computer_use",
    "rpa",
    "desktop_live",
] as const;
export const LOCKED_RUNTIME_KINDS = ["chat", "memory", "automation", "extensions"] as const;

const CORE_RUNTIME_KIND_SET = new Set<string>(CORE_RUNTIME_KINDS);
const CANONICAL_RUNTIME_KIND_SET = new Set<string>(CANONICAL_RUNTIME_KINDS);
const LOCKED_RUNTIME_KIND_SET = new Set<string>(LOCKED_RUNTIME_KINDS);
const RUNTIME_DISPLAY_NAME: Record<string, LocalizedText> = {
    chat: lt("聊天运行时", "Chat Runtime"),
    memory: lt("记忆运行时", "Memory Runtime"),
    automation: lt("自动化运行时", "Automation Runtime"),
    extensions: lt("扩展运行时", "Extensions Runtime"),
    plugin_host: lt("插件宿主运行时", "Plugin Host Runtime"),
    computer_use: lt("桌面执行运行时", "Computer Use Runtime"),
    rpa: lt("RPA 运行时", "RPA Runtime"),
    network_supervisor: lt("网络主理人运行时", "Network Supervisor Runtime"),
    desktop_live: lt("桌面直播运行时", "Desktop Live Runtime"),
    workflow: lt("工作流运行时", "Workflow Runtime"),
};

export const RUNTIME_CONTROL_HREF: Record<string, string> = {
    chat: "/admin/chat-runtime",
    memory: "/admin/memory",
    extensions: "/admin/extensions",
    automation: "/admin/automation",
    plugin_host: "/admin/plugin-host",
    rpa: "/admin/rpa",
    computer_use: "/admin/desktop-automation",
    desktop_live: "/admin/system-base",
    workflow: "/admin/memory?tab=projects",
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
    return fallback ? fallback.en : normalizedKind;
}

export function getRuntimeDisplayText(kind: string): LocalizedText | string {
    const normalizedKind = String(kind || "").trim();
    return RUNTIME_DISPLAY_NAME[normalizedKind] || normalizedKind;
}
