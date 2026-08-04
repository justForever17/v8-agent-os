import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("model hub bootstrap reuses the governed models envelope", () => {
  const source = fs.readFileSync(path.join(root, "src/app/api/model-hub/bootstrap/route.ts"), "utf8");

  assert.doesNotMatch(source, /models\/public/);
  assert.doesNotMatch(source, /config-registry\/supervisor/);
  assert.match(source, /proxyEngineJson\("\/config-registry\/models"\)/);
  assert.match(source, /hubEnvelope\.data\?\.config/);
  assert.match(source, /routesData\.roles\?\.default/);
  assert.match(source, /hubEnvelope:\s*hubResult\.data/);
});
