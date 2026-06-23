import fs from "node:fs";
import { buildClientToolSurface } from "../dist/index.js";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
if (!Array.isArray(input)) {
  throw new Error("client tool surface replay input must be an array");
}

const output = input.map((item) => ({
  name: String(item?.name || ""),
  surface: buildClientToolSurface({
    toolName: String(item?.name || "tool"),
    state: String(item?.state || "result"),
    result: item?.result,
  }),
}));

process.stdout.write(JSON.stringify(output));
