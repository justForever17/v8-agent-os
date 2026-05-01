import type { SessionRuntimeId } from "./contract.js";

export type RuntimeRegistryLocale = "zh-CN" | "en";

export type RuntimeRegistryEntry = {
  id: SessionRuntimeId;
  label: { zh: string; en: string };
  shortLabel: { zh: string; en: string };
  description: { zh: string; en: string };
};

export const SESSION_RUNTIME_REGISTRY: Record<SessionRuntimeId, RuntimeRegistryEntry> = {
  chat: {
    id: "chat",
    label: { zh: "对话运行", en: "Conversation runtime" },
    shortLabel: { zh: "对话", en: "Chat" },
    description: {
      zh: "承接主理人的对话主链和任务编排。",
      en: "Carries the supervisor conversation and orchestration flow.",
    },
  },
  planner_lane: {
    id: "planner_lane",
    label: { zh: "规划编排", en: "Planner lane" },
    shortLabel: { zh: "规划", en: "Plan" },
    description: {
      zh: "承接结构化任务切片、task brief、依赖与验收契约。",
      en: "Carries structured planning, task briefs, dependencies, and acceptance contracts.",
    },
  },
  engineering: {
    id: "engineering",
    label: { zh: "工程运行", en: "Engineering runtime" },
    shortLabel: { zh: "工程", en: "Eng" },
    description: {
      zh: "承接工程 ContextPack、写集治理、Proof Ledger、工作区观测与代码任务诊断。",
      en: "Carries engineering ContextPacks, workset governance, Proof Ledger, workspace observation, and code-task diagnostics.",
    },
  },
  engineering_lane: {
    id: "engineering_lane",
    label: { zh: "工程运行（兼容）", en: "Engineering runtime (legacy)" },
    shortLabel: { zh: "工程", en: "Eng" },
    description: {
      zh: "兼容旧 engineering_lane 事件；运行时归入 Engineering Runtime。",
      en: "Compatibility entry for legacy engineering_lane events; projected into Engineering Runtime.",
    },
  },
  memory: {
    id: "memory",
    label: { zh: "记忆运行", en: "Memory runtime" },
    shortLabel: { zh: "记忆", en: "Memory" },
    description: {
      zh: "负责记忆召回、知识补充与上下文维护。",
      en: "Handles memory retrieval and context upkeep.",
    },
  },
  automation: {
    id: "automation",
    label: { zh: "自动流程", en: "Automation" },
    shortLabel: { zh: "自动化", en: "Auto" },
    description: {
      zh: "处理 Hook、Cron 与系统自动流程。",
      en: "Handles hooks, cron jobs, and automation flows.",
    },
  },
  extensions: {
    id: "extensions",
    label: { zh: "扩展运行", en: "Extensions runtime" },
    shortLabel: { zh: "扩展", en: "Ext" },
    description: {
      zh: "负责 Skills 与 MCP 的候选暴露、读取与执行。",
      en: "Handles skill and MCP exposure, loading, and execution.",
    },
  },
  creative_media: {
    id: "creative_media",
    label: { zh: "创意媒体", en: "Creative media" },
    shortLabel: { zh: "媒体", en: "Media" },
    description: {
      zh: "承接图片、视频、语音、音乐 recipe、任务、资产与交付状态。",
      en: "Tracks creative media recipes, jobs, assets, and delivery state.",
    },
  },
  network_supervisor: {
    id: "network_supervisor",
    label: { zh: "网络监督", en: "Network supervisor" },
    shortLabel: { zh: "网络", en: "Network" },
    description: {
      zh: "处理网络监督、抓取与外部环境观测。",
      en: "Handles network supervision, fetch flows, and external observation.",
    },
  },
  plugin_host_tool: {
    id: "plugin_host_tool",
    label: { zh: "插件工具", en: "Plugin-host tools" },
    shortLabel: { zh: "插件", en: "Plugin" },
    description: {
      zh: "承接受管插件工具和网关工具调用。",
      en: "Handles managed plugin-host and gateway-backed tool execution.",
    },
  },
  plugin_host_channel: {
    id: "plugin_host_channel",
    label: { zh: "外部通讯", en: "External channels" },
    shortLabel: { zh: "通讯", en: "Channels" },
    description: {
      zh: "承接 OpenClaw channels 等外部通讯，会话归入历史层。",
      en: "Handles OpenClaw channels and external communication history.",
    },
  },
  subagent_swarm: {
    id: "subagent_swarm",
    label: { zh: "子代理蜂群", en: "Subagent swarm" },
    shortLabel: { zh: "子代理", en: "Agents" },
    description: {
      zh: "承接子代理任务分组、紧凑转录、自检、产物引用与汇聚状态。",
      en: "Tracks subagent task groups, compact transcripts, self-checks, artifact refs, and aggregation status.",
    },
  },
  computer_use: {
    id: "computer_use",
    label: { zh: "桌面操作", en: "Computer use" },
    shortLabel: { zh: "桌面", en: "Desktop" },
    description: {
      zh: "执行桌面观察、点击、输入与 GUI 操作。",
      en: "Performs desktop observation, clicks, typing, and GUI actions.",
    },
  },
  rpa: {
    id: "rpa",
    label: { zh: "自动流程执行", en: "RPA runtime" },
    shortLabel: { zh: "RPA", en: "RPA" },
    description: {
      zh: "复用和执行 RPA 草稿与流程链。",
      en: "Runs and reuses RPA drafts and execution chains.",
    },
  },
  desktop_live: {
    id: "desktop_live",
    label: { zh: "桌面直播", en: "Desktop live" },
    shortLabel: { zh: "直播", en: "Live" },
    description: {
      zh: "承接桌面预览、流媒体与远端观察。",
      en: "Handles desktop preview, streaming, and remote observation.",
    },
  },
};

export const SESSION_RUNTIME_ORDER: SessionRuntimeId[] = [
  "chat",
  "planner_lane",
  "engineering",
  "extensions",
  "creative_media",
  "computer_use",
  "rpa",
  "network_supervisor",
  "plugin_host_tool",
  "subagent_swarm",
  "automation",
  "memory",
  "plugin_host_channel",
  "desktop_live",
];

export const VISIBLE_SESSION_RUNTIME_ORDER: SessionRuntimeId[] = SESSION_RUNTIME_ORDER.filter(
  (runtimeId) => runtimeId !== "plugin_host_channel" && runtimeId !== "desktop_live",
);

export function isHistoryOnlyRuntimeId(runtimeId: SessionRuntimeId) {
  return runtimeId === "plugin_host_channel";
}

export function isExcludedRealtimeRuntimeId(runtimeId: SessionRuntimeId) {
  return runtimeId === "desktop_live";
}

export function isRealtimeSurfaceRuntimeId(runtimeId: SessionRuntimeId) {
  return !isHistoryOnlyRuntimeId(runtimeId) && !isExcludedRealtimeRuntimeId(runtimeId);
}

function normalizeRuntimeString(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
}

export function normalizeRuntimeId(raw?: string | null): SessionRuntimeId | null {
  if (!raw) return null;
  const normalized = normalizeRuntimeString(raw);
  if (!normalized) return null;

  if (
    normalized === "planner_lane"
    || normalized.includes("planner")
    || normalized.includes("plan_lane")
    || normalized.includes("task_planning")
    || normalized.includes("task_plan")
  ) {
    return "planner_lane";
  }
  if (
    normalized === "engineering_lane"
    || normalized === "engineering"
    || normalized.includes("project_coding")
    || normalized.includes("engineering")
    || normalized.includes("workset")
    || normalized.includes("proof_ledger")
    || normalized.includes("proofledger")
    || normalized.includes("coding_planner")
  ) {
    return "engineering";
  }
  if (
    normalized === "subagent_swarm"
    || normalized.includes("subagent")
    || normalized.includes("agent_swarm")
    || normalized.includes("swarm")
    || normalized.includes("delegation")
    || normalized.includes("delegate")
    || normalized.includes("parallel_delegate")
  ) {
    return "subagent_swarm";
  }
  if (
    normalized === "creative_media"
    || normalized.includes("creative_media")
    || normalized.includes("creative_runtime")
    || normalized.includes("media_runtime")
  ) {
    return "creative_media";
  }
  if (
    normalized === "plugin_host_channel"
    || normalized.includes("channel")
    || normalized.includes("openclaw")
    || normalized.includes("wechat")
    || normalized.includes("feishu")
    || normalized.includes("gateway_message")
    || normalized.includes("push")
    || normalized.includes("inbound")
    || normalized.includes("dispatch")
    || normalized.includes("tts")
    || normalized.includes("audio_delivery")
    || normalized.includes("media_delivery")
  ) {
    return "plugin_host_channel";
  }
  if (
    normalized === "plugin_host_tool"
    || normalized.startsWith("gateway_")
    || normalized.startsWith("gateway.")
    || normalized.includes("plugin_tool")
    || normalized.includes("tool_registry")
  ) {
    return "plugin_host_tool";
  }
  if (normalized.includes("extension") || normalized.includes("skill") || normalized.includes("mcp")) {
    return "extensions";
  }
  if (normalized.includes("desktop_live") || normalized.includes("desktoplive") || normalized.includes("webrtc") || normalized.includes("viewer") || normalized.includes("stream")) {
    return "desktop_live";
  }
  if (normalized.includes("computer_use") || normalized.includes("computeruse") || normalized.includes("observe") || normalized.includes("gui")) {
    return "computer_use";
  }
  if (normalized.includes("network") || normalized.includes("crawler") || normalized.includes("fetch") || normalized.includes("browser") || normalized.includes("http")) {
    return "network_supervisor";
  }
  if (normalized.includes("plugin") || normalized.includes("host") || normalized.includes("gateway")) {
    return "plugin_host_tool";
  }
  if (normalized.includes("automation") || normalized.includes("cron") || normalized.includes("hook") || normalized.includes("scheduler")) {
    return "automation";
  }
  if (normalized.includes("memory") || normalized.includes("recall") || normalized.includes("knowledge")) {
    return "memory";
  }
  if (normalized.includes("rpa") || normalized.includes("robot")) {
    return "rpa";
  }
  if (normalized.includes("chat") || normalized.includes("supervisor") || normalized.includes("conversation") || normalized.includes("assistant") || normalized.includes("run")) {
    return "chat";
  }
  return null;
}

export function getRuntimeRegistryEntry(runtimeId: SessionRuntimeId, locale: RuntimeRegistryLocale = "zh-CN") {
  const descriptor = SESSION_RUNTIME_REGISTRY[runtimeId];
  const english = locale === "en";
  return {
    id: descriptor.id,
    label: english ? descriptor.label.en : descriptor.label.zh,
    shortLabel: english ? descriptor.shortLabel.en : descriptor.shortLabel.zh,
    description: english ? descriptor.description.en : descriptor.description.zh,
  };
}
