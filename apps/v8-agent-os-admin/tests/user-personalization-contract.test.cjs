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

  assert.match(users, /lightBackgroundImage\?: string/);
  assert.match(users, /lightBackgroundEnabled\?: boolean/);
  assert.match(users, /rawImage\.startsWith\("\/user-assets\/background\/"\)/);
  assert.doesNotMatch(users, /url\.protocol === "https:"/);
  assert.match(profile, /appearance: user\.appearance \|\| \{\}/);
  assert.match(profile, /removeManagedUserMedia\(previousBackground, "background"\)/);
});
