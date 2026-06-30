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

function resolveEngineOrigin() {
  try {
    const value = String(readCanonicalBridge().engineBaseUrl || "").trim();
    if (value) {
      return value.replace(/\/v1\/?$/, "").replace(/\/$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9530";
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
      {
        source: "/api/client/bg_processes/:path*",
        destination: `${resolveEngineOrigin()}/v1/bg_processes/:path*`,
      },
      {
        source: "/api/bg_processes/:path*",
        destination: `${resolveEngineOrigin()}/v1/bg_processes/:path*`,
      },
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
