import type { NextConfig } from "next";
import fs from "fs";
import os from "os";
import path from "path";

const localApiNamespaces = [
  "approvals",
  "artifacts",
  "auth",
  "chat",
  "conversations",
  "projects",
  "realtime",
  "rpa",
  "runs",
  "sessions",
  "upload",
  "workspace",
];

function resolveAdminApiBaseUrl() {
  try {
    const candidates = [
      path.join(os.homedir(), ".v8chat", "config.json"),
      path.join(os.homedir(), ".v8-agent-os", "config.json"),
    ];
    const configPath = candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
    if (fs.existsSync(configPath)) {
      const raw = fs.readFileSync(configPath, "utf-8");
      const parsed = JSON.parse(raw) as { systemBase?: { bridge?: { adminBaseUrl?: string } } };
      const value = String(parsed?.systemBase?.bridge?.adminBaseUrl || "").trim();
      if (value) {
        return value.replace(/\/$/, "");
      }
    }
  } catch {}
  return "http://127.0.0.1:9528/api";
}

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: true,
  async rewrites() {
    return [
      ...localApiNamespaces.map((namespace) => ({
        source: `/api/${namespace}/:path*`,
        destination: `/api/${namespace}/:path*`,
      })),
      {
        source: '/api/:path*',
        destination: `${resolveAdminApiBaseUrl()}/:path*`,
      },
    ];
  },
};

export default nextConfig;
