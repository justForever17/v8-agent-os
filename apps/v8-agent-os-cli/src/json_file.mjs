import fs from "node:fs";
import path from "node:path";

export function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

export function readJsonFile(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  const content = fs.readFileSync(filePath, "utf8");
  if (!content.trim()) return fallback;
  return JSON.parse(content);
}

export function writeJsonFile(filePath, payload) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

export function backupFile(filePath, label = "backup") {
  if (!fs.existsSync(filePath)) return null;
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "_");
  const target = `${filePath}.${label}.${stamp}.bak`;
  fs.copyFileSync(filePath, target);
  return target;
}
