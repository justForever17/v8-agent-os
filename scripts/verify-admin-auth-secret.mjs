import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..");
const helper = path.join(repoRoot, "scripts", "ensure-admin-auth-secret.mjs");
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-admin-secret-"));
const stateRoot = path.join(tempRoot, "state");
const adminDir = path.join(tempRoot, "admin");
fs.mkdirSync(adminDir, { recursive: true });

function runHelper() {
    const result = spawnSync(process.execPath, [helper, "--admin-dir", adminDir], {
        env: { ...process.env, V8_AGENT_OS_HOME: stateRoot },
        encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
}

try {
    runHelper();
    const secretFile = path.join(stateRoot, "secrets", "admin-auth-secret");
    const firstSecret = fs.readFileSync(secretFile, "utf8").trim();
    assert.ok(firstSecret.length >= 48);
    assert.ok(!fs.existsSync(path.join(adminDir, ".env.local")), "managed auth must not create project .env.local");

    runHelper();
    const secondSecret = fs.readFileSync(secretFile, "utf8").trim();
    assert.equal(secondSecret, firstSecret, "managed Admin auth secret must be stable across launches");
    assert.ok(!fs.existsSync(path.join(stateRoot, "config.json")), "auth secret management must not create or modify config.json");

    console.log(JSON.stringify({
        ok: true,
        checks: [
            "secret_generated_outside_repository",
            "project_env_not_created",
            "secret_stable_across_launches",
            "config_json_untouched",
        ],
    }, null, 2));
} finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
}
