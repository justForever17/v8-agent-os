import fs from "node:fs";
import path from "node:path";
import { launchDetachedElectron, paths } from "./electron-launcher.mjs";

const serverBundle = path.join(paths.desktopPetDir, "dist", "server.cjs");
if (!fs.existsSync(serverBundle)) {
  throw new Error("Desktop Pet production bundle is missing. Run `npm --prefix apps/v8-agent-os-desktop-pet run build` first.");
}

await launchDetachedElectron(path.join(paths.desktopPetDir, "electron", "main.cjs"), {
  V8_DESKTOP_PET_MANAGED_BY_SHELL: "1",
  V8_ADMIN_BASE_URL: process.env.V8_ADMIN_BASE_URL || "http://127.0.0.1:9528",
  V8_WEB_BASE_URL: process.env.V8_WEB_BASE_URL || "http://127.0.0.1:9527",
});
