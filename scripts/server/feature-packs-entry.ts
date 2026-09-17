// Compile the existing installer owner. No Next process or second transaction implementation.
import { getRuntimeFeaturePackState, triggerFeaturePackInstall } from "../../apps/v8-agent-os-admin/src/lib/server/runtime-feature-packs";
const [action = "list", packId, ...flags] = process.argv.slice(2);
try {
    if (action === "list") {
        const state = await getRuntimeFeaturePackState({ forceHealthRefresh: true });
        console.log(JSON.stringify(state, null, 2));
        if (!state.engineAvailable) process.exitCode = 1;
    } else if (action === "install" && packId) {
        console.log(JSON.stringify(await triggerFeaturePackInstall(packId, flags.includes("--dry-run"), "en", true), null, 2));
    } else throw new Error("Usage: v8os packs list | install <pack-id> [--dry-run]");
} catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
}
