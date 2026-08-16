import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("desktop pet settings use the authenticated config registry contract", () => {
  const pageSource = fs.readFileSync(
    path.join(adminRoot, "src", "app", "admin", "(dashboard)", "desktop-pet", "page.tsx"),
    "utf8",
  );
  const routeSource = fs.readFileSync(
    path.join(adminRoot, "src", "app", "api", "config-registry", "[domain]", "route.ts"),
    "utf8",
  );
  const shellControlsSource = fs.readFileSync(
    path.join(adminRoot, "src", "components", "layout", "ShellWindowControls.tsx"),
    "utf8",
  );
  const zhLocale = fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "zh-CN.json"), "utf8");
  const enLocale = fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "en.json"), "utf8");

  assert.match(pageSource, /const API_PATH = "\/api\/config-registry\/desktop-pet"/);
  assert.match(pageSource, /method: "POST"/);
  assert.doesNotMatch(pageSource, /\/api\/admin\/config\/desktop-pet/);
  assert.match(pageSource, /DESKTOP_PET_EVENT_CATALOG/);
  assert.match(pageSource, /desktopPetEventLabel/);
  assert.match(pageSource, /getDesktopPetState/);
  assert.match(pageSource, /setDesktopPetEnabled/);
  assert.match(pageSource, /runtimeState\.reasonCode === "linux_desktop_pet_input_passthrough_unreliable"/);
  assert.match(pageSource, /runtimeUnavailable && !runtimeState\.enabled/);
  assert.match(pageSource, /runtimeLinuxUnavailableRunning/);
  assert.match(pageSource, /<Switch/);
  assert.match(shellControlsSource, /available: boolean/);
  assert.match(shellControlsSource, /reasonCode\?: string \| null/);
  assert.match(shellControlsSource, /"unavailable"/);
  assert.match(zhLocale, /当前 Linux 桌宠运行时尚不能为全屏交互窗口可靠保证鼠标点击穿透/);
  assert.match(enLocale, /current Linux companion runtime cannot reliably guarantee mouse click-through/);
  assert.doesNotMatch(pageSource, /<Input value=\{row\.match\}/);
  assert.match(routeSource, /export async function GET/);
  assert.match(routeSource, /export async function POST/);
  assert.match(routeSource, /requireAdminIdentity/);
});
