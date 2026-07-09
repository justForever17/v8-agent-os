import { getRuntimeFeaturePackState, triggerFeaturePackInstall } from "@/lib/server/runtime-feature-packs";

export async function getRuntimeInstallState() {
    const state = await getRuntimeFeaturePackState();
    return {
        deprecated: true,
        replacement: "/api/runtime-feature-packs",
        installProfile: "feature_packs",
        installPlatform: process.platform === "win32" ? "windows" : process.platform === "darwin" ? "macos" : "linux",
        installedRuntimeFamilies: state.packs.flatMap((pack) => pack.status === "installed" ? pack.runtimeFamilies : []),
        bootstrapManaged: false,
        lastUpgradeAt: null,
        engineAvailable: state.engineAvailable,
        canInstallDesktop: state.packs.some((pack) => pack.id === "computer_use_desktop" && pack.status !== "installed"),
        canAutoRestart: false,
        featurePacks: state.packs,
        featurePackSummary: state.summary,
    };
}

export async function triggerDesktopInstall() {
    return {
        deprecated: true,
        replacement: "/api/runtime-feature-packs",
        ...(await triggerFeaturePackInstall("computer_use_desktop")),
    };
}
