export type ProductVocabularyEntry = {
    canonicalId: string;
    zh: string;
    en: string;
    descriptionZh: string;
    descriptionEn: string;
};

export const PRODUCT_VOCABULARY = {
    chat: {
        canonicalId: "chat",
        zh: "主理人中枢",
        en: "Supervisor Hub",
        descriptionZh: "主理人与子代理协作的聊天编排入口。",
        descriptionEn: "Chat orchestration for the Supervisor and subagents.",
    },
    memory: {
        canonicalId: "memory",
        zh: "记忆系统",
        en: "Memory System",
        descriptionZh: "长期记忆、知识与项目上下文。",
        descriptionEn: "Long-term memory, knowledge, and project context.",
    },
    automation: {
        canonicalId: "automation",
        zh: "定时与触发",
        en: "Automation",
        descriptionZh: "定时任务、Hook 与自动触发。",
        descriptionEn: "Schedules, hooks, and automatic triggers.",
    },
    engineering: {
        canonicalId: "engineering",
        zh: "编程模式",
        en: "Engineering Mode",
        descriptionZh: "工程上下文、文件改动、验证与 proof。",
        descriptionEn: "Engineering context, code changes, verification, and proof.",
    },
    extensions: {
        canonicalId: "extensions",
        zh: "扩展生态",
        en: "Extensions",
        descriptionZh: "Skills、MCP 与扩展工具生态。",
        descriptionEn: "Skills, MCP, and extension tooling.",
    },
    research: {
        canonicalId: "research",
        zh: "深度调研",
        en: "Deep Research",
        descriptionZh: "多源调研、证据包与答案卷宗。",
        descriptionEn: "Multi-source research, evidence packs, and answer dossiers.",
    },
    pluginManager: {
        canonicalId: "plugin_manager",
        zh: "插件管理中心",
        en: "Plugin Center",
        descriptionZh: "精选官方 CLI、Skill、MCP、UI 适配与任务授权。",
        descriptionEn: "Curated official CLI, Skill, MCP, UI adapters, and task grants.",
    },
    computerUse: {
        canonicalId: "computer_use",
        zh: "computer use",
        en: "computer use",
        descriptionZh: "computer use 观察、点击、输入与应用操作。",
        descriptionEn: "computer use observation, clicking, typing, and app control.",
    },
    rpa: {
        canonicalId: "rpa",
        zh: "RPA 自动化",
        en: "RPA automation",
        descriptionZh: "RPA 自动化流程发现、执行与回放。",
        descriptionEn: "RPA automation discovery, execution, and replay.",
    },
    network: {
        canonicalId: "network_supervisor",
        zh: "网络连接",
        en: "Network Links",
        descriptionZh: "局域网、远程连接与设备发现。",
        descriptionEn: "LAN, remote links, and device discovery.",
    },
    creativeMedia: {
        canonicalId: "creative_media",
        zh: "多媒体生成",
        en: "Media Generation",
        descriptionZh: "图片、视频、语音、音乐和 3D 素材生成。",
        descriptionEn: "Image, video, voice, music, and 3D asset generation.",
    },
    runtimeGovernance: {
        canonicalId: "runtime_governance",
        zh: "运行治理",
        en: "Execution Governance",
        descriptionZh: "能力开关、权限与运行状态治理。",
        descriptionEn: "Capability switches, permissions, and execution governance.",
    },
    desktopPet: {
        canonicalId: "desktop_pet",
        zh: "桌宠设置",
        en: "Desktop Companion",
        descriptionZh: "桌宠事件播报、动作映射与光效设置。",
        descriptionEn: "Desktop companion voice, action mapping, and effects.",
    },
} as const;

export type ProductVocabularyKey = keyof typeof PRODUCT_VOCABULARY;

export function getProductVocabularyEntry(key: ProductVocabularyKey): ProductVocabularyEntry {
    return PRODUCT_VOCABULARY[key];
}
