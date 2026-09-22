const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const appRoot = path.resolve(__dirname, "../..");
const read = relativePath => fs.readFileSync(path.join(appRoot, relativePath), "utf8");

// Owner persistence and MP4 byte ranges are now exercised against Engine
// tests/client_surface/test_engine_client_assets.py. Admin only proxies uploads.
test("appearance routes keep their Engine streaming adapter", () => {
  for (const name of ["user-avatar-upload", "user-background-upload"]) {
    const route = read(`src/app/api/admin/client/${name}/route.ts`);
    assert.match(route, /proxyClientMedia/);
    assert.doesNotMatch(route, /writeFile|ensureUserMediaDirectory|updateUserRecord/);
  }
});
test("native image processing is lazy and old Linux x64 CPUs fail closed", async () => {
  const capability = await import("../../src/admin/lib/server/native-image-processing.ts");
  const routes = [
    read("src/app/api/admin/avatar-upload/route.ts"),
  ];

  for (const route of routes) {
    assert.doesNotMatch(route, /import sharp from ["']sharp["']/);
    assert.match(route, /await import\(["']sharp["']\)/);
    assert.match(route, /getNativeImageProcessingAvailability\(\)/);
  }

  assert.deepEqual(
    capability.evaluateNativeImageProcessingAvailability({
      platform: "linux",
      arch: "x64",
      cpuInfo: "processor: 0\nflags: fpu sse sse2 sse4a\n",
    }),
    {
      available: false,
      reasonCode: "linux_x64_sse4_2_required",
      evidence: "proc_cpuinfo",
    },
  );
  assert.equal(
    capability.evaluateNativeImageProcessingAvailability({
      platform: "linux",
      arch: "x64",
      cpuInfo: "processor: 0\nflags: fpu sse sse2 sse4_1 sse4_2\n",
    }).available,
    true,
  );
  assert.equal(
    capability.evaluateNativeImageProcessingAvailability({
      platform: "linux",
      arch: "x64",
      cpuInfo: "processor: 0\nflags: sse sse2 sse4_2\nprocessor: 1\nflags: sse sse2 sse4a\n",
    }).available,
    false,
  );
  assert.equal(
    capability.evaluateNativeImageProcessingAvailability({
      platform: "linux",
      arch: "arm64",
      cpuInfo: null,
    }).available,
    true,
  );
  assert.equal(
    capability.evaluateNativeImageProcessingAvailability({
      platform: "win32",
      arch: "x64",
      cpuInfo: null,
    }).available,
    true,
  );
});
