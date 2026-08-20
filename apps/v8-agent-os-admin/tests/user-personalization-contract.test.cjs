const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const appRoot = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(appRoot, relativePath), "utf8");

test("user media survives Next rebuilds in canonical V8OS storage", () => {
  const media = read("src/lib/user-media.ts");
  const avatar = read("src/app/api/client/user-avatar-upload/route.ts");
  const background = read("src/app/api/client/user-background-upload/route.ts");

  assert.match(media, /getBaseDir\(\), "assets", "user-media", kind/);
  assert.match(avatar, /ensureUserMediaDirectory\("avatar"\)/);
  assert.match(background, /ensureUserMediaDirectory\("background"\)/);
  assert.doesNotMatch(avatar, /process\.cwd\(\).*public/);
  assert.doesNotMatch(background, /process\.cwd\(\).*public/);
});

test("profile truth carries the light background appearance contract", () => {
  const users = read("src/lib/users.ts");
  const profile = read("src/app/api/auth/profile/route.ts");

  assert.match(users, /lightBackgroundMedia\?: string/);
  assert.match(users, /lightBackgroundMediaType\?: "image" \| "video"/);
  assert.match(users, /lightBackgroundImage\?: string/);
  assert.match(users, /lightBackgroundEnabled\?: boolean/);
  assert.match(users, /webp\|mp4/);
  assert.doesNotMatch(users, /url\.protocol === "https:"/);
  assert.match(profile, /appearance: user\.appearance \|\| \{\}/);
  assert.match(profile, /removeManagedUserMedia\(previousBackground, "background"\)/);
});

test("MP4 backgrounds are validated, stored atomically, and served with byte ranges", () => {
  const upload = read("src/app/api/client/user-background-upload/route.ts");
  const mediaRoute = read("src/app/user-assets/[kind]/[filename]/route.ts");

  assert.match(upload, /VIDEO_TYPES = new Set\(\["video\/mp4"\]\)/);
  assert.match(upload, /MAX_VIDEO_SIZE_BYTES = 500 \* 1024 \* 1024/);
  assert.match(upload, /MAX_PHONE_BACKGROUND_SIZE_BYTES = \(50 \* 1024 \* 1024\) - 1/);
  assert.match(upload, /req\.headers\.get\("x-v8-client-surface"\) === "phone"/);
  assert.match(upload, /const reader = req\.body\.getReader\(\)/);
  assert.match(upload, /await reader\.read\(\)/);
  assert.match(upload, /subarray\(4, 8\)\.toString\("ascii"\) === "ftyp"/);
  assert.match(upload, /rename\(temporaryPath, targetPath\)/);
  assert.match(mediaRoute, /"Accept-Ranges": "bytes"/);
  assert.match(mediaRoute, /status = 206/);
  assert.match(mediaRoute, /Content-Range/);
});

test("native image processing is lazy and old Linux x64 CPUs fail closed", async () => {
  const capability = await import("../src/lib/server/native-image-processing.ts");
  const routes = [
    read("src/app/api/avatar-upload/route.ts"),
    read("src/app/api/client/user-avatar-upload/route.ts"),
    read("src/app/api/client/user-background-upload/route.ts"),
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
