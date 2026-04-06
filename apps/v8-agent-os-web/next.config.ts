import type { NextConfig } from "next";
import { readCanonicalBridge } from "./src/lib/server/bridge-config";

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
    const value = String(readCanonicalBridge().adminBaseUrl || "").trim();
    if (value) {
      return value.replace(/\/$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9528/api";
}

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: true,
  transpilePackages: ["@v8/session-realtime"],
  experimental: {
    externalDir: true,
  },
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
