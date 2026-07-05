import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { CONFIG_PATH, MCP_CONFIG_PATH, STATE_ROOT, timestampForFile } from "./paths.mjs";
import { backupFile, ensureDir, writeJsonFile } from "./json_file.mjs";
import { runDoctor } from "./doctor.mjs";

export async function runRepair({ dryRun = true, yes = false } = {}) {
  const doctor = await runDoctor({ preferEngine: false });
  const applied = [];
  const skipped = [];

  for (const action of doctor.repairPlan?.actions || []) {
    if (action.id === "create_state_root") {
      if (!dryRun || yes) ensureDir(STATE_ROOT);
      applied.push({ ...action, dryRun });
      continue;
    }
    if (action.id === "backup_config.json" || action.id === "backup_mcp.json") {
      if (!dryRun || yes) {
        backupFile(action.path, "repair");
      }
      applied.push({ ...action, dryRun });
      continue;
    }
    skipped.push({ ...action, reason: action.safe ? "manual_review" : "requires_explicit_install_or_kill" });
  }

  const report = {
    createdAt: new Date().toISOString(),
    dryRun,
    applied,
    skipped,
    doctorSummary: doctor.summary,
  };
  const reportDir = path.join(STATE_ROOT, "reports", "cli_repair");
  if (!dryRun || yes) {
    ensureDir(reportDir);
    writeJsonFile(path.join(reportDir, `${timestampForFile()}.json`), report);
  }
  return report;
}

export function repairAuthSecret() {
  const secretsDir = path.join(STATE_ROOT, "secrets");
  const secretFile = path.join(secretsDir, "admin-auth-secret");
  ensureDir(secretsDir);
  if (fs.existsSync(secretFile)) return { status: "exists", secretFile };
  const value = crypto.randomBytes(48).toString("base64url");
  fs.writeFileSync(secretFile, `${value}\n`, { encoding: "utf8", mode: 0o600 });
  return { status: "created", secretFile };
}
