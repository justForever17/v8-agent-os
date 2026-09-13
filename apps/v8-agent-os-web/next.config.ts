import type { NextConfig } from "next";

const createNextConfig = (): NextConfig => ({
  output: "standalone",
  reactCompiler: true,
  transpilePackages: ["@v8/session-realtime"],
  experimental: { externalDir: true },
  // Runtime BFF routes own proxy targets; build artifacts contain no instance ports.
});

export default createNextConfig;
