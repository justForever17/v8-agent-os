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

  assert.match(pageSource, /const API_PATH = "\/api\/config-registry\/desktop-pet"/);
  assert.match(pageSource, /method: "POST"/);
  assert.doesNotMatch(pageSource, /\/api\/admin\/config\/desktop-pet/);
  assert.match(pageSource, /DESKTOP_PET_EVENT_CATALOG/);
  assert.match(pageSource, /desktopPetEventLabel/);
  assert.match(pageSource, /getDesktopPetState/);
  assert.match(pageSource, /setDesktopPetEnabled/);
  assert.match(pageSource, /<Switch/);
  assert.doesNotMatch(pageSource, /<Input value=\{row\.match\}/);
  assert.match(routeSource, /export async function GET/);
  assert.match(routeSource, /export async function POST/);
  assert.match(routeSource, /requireAdminIdentity/);
});
