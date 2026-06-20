import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "").trim() : "";
}

function readExistingEnvSecret(content) {
  const match = content.match(/^(?:NEXTAUTH_SECRET|AUTH_SECRET)=(?:"([^"]+)"|'([^']+)'|([^\r\n]+))$/m);
  const value = String(match?.[1] || match?.[2] || match?.[3] || "").trim();
  return value && !value.includes("replace-this-with") ? value : "";
}

export function ensureManagedAuthSecret(options = {}) {
  const stateRoot = path.resolve(
    options.stateRoot
      || process.env.V8_AGENT_OS_HOME
      || path.join(os.homedir(), ".v8-agent-os"),
  );
  const secretsDir = path.join(stateRoot, "secrets");
  const secretFile = path.join(secretsDir, "admin-auth-secret");
  const legacyAdminDir = options.adminDir ? path.resolve(options.adminDir) : "";
  const legacyEnvFile = legacyAdminDir ? path.join(legacyAdminDir, ".env.local") : "";
  const legacyEnv = legacyEnvFile && fs.existsSync(legacyEnvFile)
    ? fs.readFileSync(legacyEnvFile, "utf8")
    : "";

  fs.mkdirSync(secretsDir, { recursive: true });
  let secret = fs.existsSync(secretFile) ? fs.readFileSync(secretFile, "utf8").trim() : "";
  let secretSource = secret ? "managed_secret_file" : "";
  if (!secret) {
    const migratedSecret = readExistingEnvSecret(legacyEnv);
    secret = migratedSecret || crypto.randomBytes(48).toString("base64url");
    secretSource = migratedSecret ? "migrated_legacy_env" : "generated";
    fs.writeFileSync(secretFile, `${secret}\n`, { encoding: "utf8", mode: 0o600 });
  }
  if (process.platform !== "win32") {
    fs.chmodSync(secretFile, 0o600);
  }
  return { secret, secretFile, secretSource };
}

const invokedAsScript = process.argv[1]
  && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (invokedAsScript) {
  const result = ensureManagedAuthSecret({
    adminDir: argumentValue("--admin-dir") || undefined,
  });
  console.log(JSON.stringify({
    ok: true,
    secretFile: result.secretFile,
    secretSource: result.secretSource,
    projectEnvWritten: false,
  }));
}
