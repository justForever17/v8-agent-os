/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web spatial surfaces preserve symmetric motion and direct resize tracking without unmounting the canvas", () => {
  const sidebar = readText("apps/v8-agent-os-web/src/components/layout/Sidebar.tsx");
  const workbench = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");

  assert.match(sidebar, /AnimatePresence, motion, useReducedMotion/);
  assert.match(sidebar, /translateX\(-18px\)/);
  assert.match(sidebar, /transformOrigin: "top left"/);
  assert.match(sidebar, /inert=\{isCollapsed\}/);
  assert.match(workbench, /data-workbench-motion-shell/);
  assert.match(workbench, /aria-hidden=\{!shouldShow\}/);
  assert.match(workbench, /inert=\{!shouldShow\}/);
  assert.match(workbench, /!shouldShow && "pointer-events-none"/);
  assert.doesNotMatch(workbench, /!shouldShow && "invisible pointer-events-none"/);
  assert.match(workbench, /const animatedShellWidth = effectiveMode === "focus" \? "100%" : shouldShow \? panelWidth \+ 6 : 0/);
  assert.match(workbench, /animate=\{\{\s*width: animatedShellWidth/);
  assert.match(workbench, /width: \{ duration: isResizing \? 0/);
  assert.match(workbench, /const canvasTab = useMemo/);
  assert.match(workbench, /document=\{canvasTab\.document\}/);
});

test("Phone drawers defer Modal unmount until their symmetric exit completes", () => {
  const hook = readText("apps/v8-agent-os-phone/src/hooks/use-deferred-modal-motion.ts");
  const history = readText("apps/v8-agent-os-phone/src/components/layout/HistoryDrawer.tsx");
  const overview = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");

  assert.match(hook, /runOnJS\(finishClose\)/);
  assert.match(hook, /duration: reduceMotion \? 100 : exitDuration/);
  assert.match(history, /visible=\{rendered\}/);
  assert.match(history, /-18 \* \(1 - progress\.value\)/);
  assert.match(overview, /visible=\{rendered\}/);
  assert.match(overview, /panelWidth \* \(1 - progress\.value\)/);
});

test("Admin sidebar keeps its content inert while the shell transition completes", () => {
  const sidebar = readText("apps/v8-agent-os-admin/src/components/layout/Sidebar.tsx");

  assert.match(sidebar, /\[transition-duration:220ms\]/);
  assert.match(sidebar, /transition-\[opacity,transform\]/);
  assert.match(sidebar, /inert=\{isCollapsed\}/);
});

test("Desktop Pet menu closes through its own origin-aware transition before unmount", () => {
  const pet = readText("apps/v8-agent-os-desktop-pet/src/components/CyberPet.tsx");

  assert.match(pet, /menuMounted && createPortal/);
  assert.match(pet, /setMenuMotionVisible\(false\)/);
  assert.match(pet, /}, 180\)/);
  assert.match(pet, /data-motion-state=\{menuMotionVisible \? 'open' : 'closed'\}/);
  assert.match(readText("apps/v8-agent-os-desktop-pet/src/index.css"), /cyber-pet-menu-motion\[data-motion-side="right"\]/);
});
