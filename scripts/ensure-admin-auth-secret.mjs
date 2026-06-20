import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "").trim() : "";
}

const adminDir = path.resolve(argumentValue("--admin-dir") || "apps/v8-agent-os-admin");
const stateRoot = path.resolve(
  process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os"),
);
const secretsDir = path.join(stateRoot, "secrets");
const secretFile = path.join(secretsDir, "admin-auth-secret");
const envFile = path.join(adminDir, ".env.local");

function readExistingEnvSecret(content) {
  const match = content.match(/^(?:NEXTAUTH_SECRET|AUTH_SECRET)=(?:"([^"]+)"|'([^']+)'|([^\r\n]+))$/m);
  const value = String(match?.[1] || match?.[2] || match?.[3] || "").trim();
  return value && !value.includes("replace-this-with") ? value : "";
}

fs.mkdirSync(secretsDir, { recursive: true });
const existingEnv = fs.existsSync(envFile) ? fs.readFileSync(envFile, "utf8") : "";
let secret = fs.existsSync(secretFile) ? fs.readFileSync(secretFile, "utf8").trim() : "";
let secretSource = secret ? "managed_secret_file" : "";
if (!secret) {
  const envSecret = readExistingEnvSecret(existingEnv);
  secret = envSecret || crypto.randomBytes(48).toString("base64url");
  secretSource = envSecret ? "migrated_env" : "generated";
  fs.writeFileSync(secretFile, `${secret}\n`, { encoding: "utf8", mode: 0o600 });
}
if (process.platform !== "win32") {
  fs.chmodSync(secretFile, 0o600);
}

const managed = new Map([
  ["NEXTAUTH_URL", "http://127.0.0.1:9528"],
  ["AUTH_TRUST_HOST", "true"],
  ["AUTH_SECRET", secret],
  ["NEXTAUTH_SECRET", secret],
  ["NEXT_PUBLIC_APP_VERSION", "1.0.0"],
]);
const retained = existingEnv
  .split(/\r?\n/)
  .filter((line) => {
    const key = line.match(/^([A-Z][A-Z0-9_]*)=/)?.[1];
    return !key || !managed.has(key);
  })
  .filter((line, index, all) => line || (index > 0 && all[index - 1]));
const generated = [
  "# Managed by V8 Agent OS. The canonical secret is stored outside the repository.",
  ...Array.from(managed, ([key, value]) => `${key}=${value}`),
  ...retained,
].join("\n").trimEnd();
fs.writeFileSync(envFile, `${generated}\n`, "utf8");

console.log(JSON.stringify({
  ok: true,
  envFile,
  secretFile,
  secretSource,
}));
