const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "../..");

const config = getDefaultConfig(projectRoot);
config.resolver.assetExts = [...config.resolver.assetExts, "wasm"];
const priorBlockList = config.resolver.blockList || [];
config.resolver.blockList = [
  ...(Array.isArray(priorBlockList) ? priorBlockList : [priorBlockList]),
  /[\\/]android[\\/](?:build|\.cxx)[\\/].*/,
  /[\\/]9131-phone[\\/]cxx[\\/].*/,
];
// Expo SQLite's web worker requires SharedArrayBuffer. This is the local Expo
// server only; production native storage continues through the SQLite module.
const enhanceMiddleware = config.server.enhanceMiddleware;
config.server.enhanceMiddleware = (middleware, server) => {
  const enhanced = enhanceMiddleware ? enhanceMiddleware(middleware, server) : middleware;
  return (request, response, next) => {
    response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
    response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
    enhanced(request, response, next);
  };
};

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];

module.exports = config;
