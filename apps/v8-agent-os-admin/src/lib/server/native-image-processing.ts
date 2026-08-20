import fs from "node:fs";

export const NATIVE_IMAGE_PROCESSING_UNAVAILABLE_MESSAGE =
    "当前 CPU 不支持图片处理所需的 SSE4.2 指令集。Admin、Web 和视频背景仍可正常使用。";

export type NativeImageProcessingAvailability = {
    available: boolean;
    reasonCode: null | "linux_x64_cpu_features_unavailable" | "linux_x64_sse4_2_required";
    evidence: "not_required" | "proc_cpuinfo" | "unavailable";
};

type AvailabilityOptions = {
    platform?: NodeJS.Platform;
    arch?: string;
    cpuInfo?: string | null;
};

function parseCpuFeatureSets(cpuInfo: string) {
    return Array.from(cpuInfo.matchAll(/^(?:flags|features)\s*:\s*(.+)$/gim), (match) => (
        new Set(match[1].trim().toLowerCase().split(/\s+/).filter(Boolean))
    ));
}

export function evaluateNativeImageProcessingAvailability(
    options: AvailabilityOptions = {},
): NativeImageProcessingAvailability {
    const platform = options.platform ?? process.platform;
    const arch = options.arch ?? process.arch;
    if (platform !== "linux" || arch !== "x64") {
        return { available: true, reasonCode: null, evidence: "not_required" };
    }

    let cpuInfo = options.cpuInfo;
    if (cpuInfo === undefined) {
        try {
            cpuInfo = fs.readFileSync("/proc/cpuinfo", "utf8");
        } catch {
            cpuInfo = null;
        }
    }
    if (!cpuInfo) {
        return {
            available: false,
            reasonCode: "linux_x64_cpu_features_unavailable",
            evidence: "unavailable",
        };
    }

    const featureSets = parseCpuFeatureSets(cpuInfo);
    if (featureSets.length === 0) {
        return {
            available: false,
            reasonCode: "linux_x64_cpu_features_unavailable",
            evidence: "unavailable",
        };
    }
    if (!featureSets.every((features) => features.has("sse4_2"))) {
        return {
            available: false,
            reasonCode: "linux_x64_sse4_2_required",
            evidence: "proc_cpuinfo",
        };
    }
    return { available: true, reasonCode: null, evidence: "proc_cpuinfo" };
}

let cachedAvailability: NativeImageProcessingAvailability | null = null;

export function getNativeImageProcessingAvailability() {
    cachedAvailability ??= evaluateNativeImageProcessingAvailability();
    return cachedAvailability;
}
