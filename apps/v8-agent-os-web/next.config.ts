import type { NextConfig } from "next";

const createNextConfig = (): NextConfig => ({
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  output: "standalone",
  reactCompiler: true,
  transpilePackages: ["@v8/session-realtime"],
  experimental: { externalDir: true },
  // Runtime BFF routes own proxy targets; build artifacts contain no instance ports.
});

export default createNextConfig;
