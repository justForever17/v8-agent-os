import fs from "node:fs";
import path from "node:path";

const roots = [
  path.resolve("src/app/admin/(dashboard)"),
  path.resolve("src/components/admin"),
  path.resolve("src/components/admin-shell"),
  path.resolve("src/components/layout"),
];
const forbidden = /^(?:bg-white(?:\/.+)?|bg-slate-(?:50|100)(?:\/.+)?|text-slate-(?:400|500|600|700|800|900|950)(?:\/.+)?|border-slate-(?:100|200|300)(?:\/.+)?)$/;
const tokenPattern = /[A-Za-z0-9_\-:[\]/.%]+/g;
const violations = [];

function visit(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      visit(target);
      continue;
    }
    if (!entry.isFile() || !target.endsWith(".tsx")) continue;
    const lines = fs.readFileSync(target, "utf8").split(/\r?\n/);
    lines.forEach((line, index) => {
      for (const token of line.match(tokenPattern) || []) {
        if (token.includes("dark:")) continue;
        const utility = token.split(":").at(-1) || "";
        if (forbidden.test(utility)) {
          violations.push(`${path.relative(process.cwd(), target)}:${index + 1} ${token}`);
        }
      }
    });
  }
}

roots.forEach(visit);
if (violations.length) {
  console.error("[validate-theme-coverage] Light-only Admin surface tokens found:\n" + violations.join("\n"));
  process.exit(1);
}
console.log("[validate-theme-coverage] OK");
