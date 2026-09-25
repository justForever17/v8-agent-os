import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
if (!process.argv[2]) throw new Error("Output path required");
await build({
  entryPoints: [path.join(root, "scripts/server/feature-packs-entry.ts")],
  outfile: path.resolve(process.argv[2]),
  bundle: true, platform: "node", format: "esm", target: "node20",
  alias: {
    "@admin": path.join(root, "apps/v8-agent-os-web/src/admin"),
    "@core": path.join(root, "apps/v8-agent-os-cli/src"),
  },

});
