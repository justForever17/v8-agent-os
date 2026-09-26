import type { NextConfig } from "next";

const createNextConfig = (): NextConfig => ({
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  output: "standalone",
  compress: true,
  reactCompiler: true,
  transpilePackages: ["@v8/session-realtime"],
  experimental: {
    externalDir: true,
    optimizePackageImports: [
      "lucide-react",
      "@lobehub/icons-static-svg",
      "recharts",
      "framer-motion",
    ],
  },
  // Runtime BFF routes own proxy targets; build artifacts contain no instance ports.
});

export default createNextConfig;
