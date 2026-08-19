const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(
  path.join(__dirname, "..", "src", "lib", "server", "bridge-config.ts"),
  "utf8",
);
const rootLayoutSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "app", "layout.tsx"),
  "utf8",
);
const sessionProviderSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "components", "providers", "SessionProvider.tsx"),
  "utf8",
);

test("Web bridge config follows the governed V8OS home", () => {
  assert.match(source, /environment\.V8_AGENT_OS_HOME/);
  assert.match(source, /explicitHome\s*\?\s*path\.resolve\(explicitHome\)/);
  assert.match(source, /readJsonConfig\(resolveCanonicalConfigPath\(\)\)/);
  assert.doesNotMatch(source, /const\s+CANONICAL_CONFIG_PATH\s*=/);
});

test("Web auth provider receives an explicit server session, including anonymous null", () => {
  assert.match(rootLayoutSource, /const initialSession = await auth\(\)/);
  assert.match(rootLayoutSource, /<SessionProvider session=\{initialSession\}>/);
  assert.match(sessionProviderSource, /session: Session \| null/);
  assert.match(sessionProviderSource, /<NextAuthSessionProvider session=\{session\}>/);
});
