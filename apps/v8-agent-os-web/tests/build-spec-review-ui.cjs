/* eslint-disable @typescript-eslint/no-require-imports */
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { execFileSync } = require("node:child_process");
const { webpack } = require("next/dist/compiled/webpack/webpack");
const postcss = require("postcss");
const tailwindcss = require("tailwindcss");
const app = path.resolve(__dirname, "..");
const baseline = process.env.V8_SPEC_REVIEW_BASELINE_REF;
const output = path.resolve(app, baseline ? "../../tmp/spec-review-ui-baseline" : "../../tmp/spec-review-ui");
async function main() {
    fs.mkdirSync(output, { recursive: true });
    const loader = path.join(output, "ts-loader.cjs");
    const frozen = baseline ? execFileSync('git', ['show', `${baseline}:apps/v8-agent-os-web/src/components/chat/SpecDocumentConfirmationDialog.tsx`], {cwd: app, encoding: 'utf8'}) : null;
    fs.writeFileSync(loader, `const ts=require(${JSON.stringify(require.resolve("typescript"))});module.exports=function(source){if(this.resourcePath.replaceAll('\\\\','/').endsWith('/SpecDocumentConfirmationDialog.tsx')&&${Boolean(frozen)})source=${JSON.stringify(frozen)};return ts.transpileModule(source,{fileName:this.resourcePath,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText};`);
    await new Promise((resolve, reject) => webpack({
        mode: "development", target: "web", devtool: false,
        entry: path.join(__dirname, "spec-review-ui.entry.tsx"),
        output: { path: output, filename: "bundle.js" },
        resolve: { extensions: [".tsx", ".ts", ".js", ".mjs", ".json"], symlinks: false,
            modules: [path.join(app, "node_modules"), "node_modules"],
            alias: { "@/components/providers/LocaleProvider$": path.join(__dirname, "spec-review-ui-locale.tsx"), "@": path.join(app, "src") },
        },
        module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: [loader] }] },
    }, (error, stats) => error ? reject(error) : stats.hasErrors() ? reject(new Error(stats.toString({ all: false, errors: true }))) : resolve()));
    const configSource = ts.transpileModule(fs.readFileSync(path.join(app, "tailwind.config.ts"), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
    const configModule = { exports: {} };
    new Function("module", "exports", "require", configSource)(configModule, configModule.exports, require);
    const config = configModule.exports.default;
    config.content = [path.join(app, "src/components/**/*.{ts,tsx}").replaceAll("\\", "/"), path.join(__dirname, "spec-review-ui*.tsx").replaceAll("\\", "/")];
    const { css } = await postcss([tailwindcss(config)]).process(fs.readFileSync(path.join(app, "src/app/globals.css"), "utf8"), { from: path.join(app, "src/app/globals.css") });
    fs.writeFileSync(path.join(output, "style.css"), css);
    fs.writeFileSync(path.join(output, "index.html"), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><link rel="stylesheet" href="/style.css"><body><div id="root"></div><script src="/bundle.js"></script></body></html>');
    console.log(output);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
