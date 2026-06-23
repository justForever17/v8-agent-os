import assert from "node:assert/strict";
import path from "node:path";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..");
const bootstrap = path.join(repoRoot, "bootstrap.ps1");

function findPowerShell() {
    const candidates = process.platform === "win32"
        ? ["pwsh.exe", "pwsh", "powershell.exe"]
        : ["pwsh"];
    for (const candidate of candidates) {
        const probe = spawnSync(candidate, ["-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"], {
            encoding: "utf8",
        });
        if (probe.status === 0) {
            return candidate;
        }
    }
    throw new Error("PowerShell is required to verify bootstrap.ps1");
}

function runBootstrap(ps, extraArgs) {
    const prefix = process.platform === "win32"
        ? ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", bootstrap]
        : ["-NoProfile", "-File", bootstrap];
    return spawnSync(ps, [...prefix, ...extraArgs], {
        cwd: repoRoot,
        env: {
            ...process.env,
            V8_AGENT_OS_BOOTSTRAP_DRY_RUN: "1",
        },
        encoding: "utf8",
    });
}

const ps = findPowerShell();
const webMode = runBootstrap(ps, ["--profile", "minimal", "--services", "engine+admin+web"]);
assert.equal(webMode.status, 0, webMode.stderr || webMode.stdout);
assert.match(webMode.stdout, /Services\s+:\s+engine\+admin\+web/);
assert.match(webMode.stdout, /Web dir\s+:/);
assert.ok(
    webMode.stdout.includes(path.join(repoRoot, "apps", "v8-agent-os-web"))
        || webMode.stdout.includes("apps\\v8-agent-os-web")
        || webMode.stdout.includes("apps/v8-agent-os-web"),
    "dry-run output should include the os-web app directory",
);

const unsupported = runBootstrap(ps, ["--profile", "minimal", "--services", "engine+web"]);
assert.notEqual(unsupported.status, 0, "bootstrap must not silently accept unsupported web-only service mode");
assert.match(`${unsupported.stdout}\n${unsupported.stderr}`, /Unsupported --services value: engine\+web/);

console.log(JSON.stringify({
    ok: true,
    powerShell: ps,
    checks: [
        "engine_admin_web_service_mode_accepted",
        "web_app_directory_reported_in_dry_run",
        "unsupported_engine_web_mode_rejected",
    ],
}, null, 2));
