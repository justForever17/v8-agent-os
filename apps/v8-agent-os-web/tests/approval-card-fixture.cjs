/* eslint-disable @typescript-eslint/no-require-imports */
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const postcss = require("postcss");
const tailwindcss = require("tailwindcss");

const app = path.resolve(__dirname, "..");
const repo = path.resolve(app, "../..");
const cardFile = "src/components/chat/ApprovalCard.tsx";
const body = "需要确认：写入项目文件\n网络连接已中断，请重试。完整错误：request <fixture> & 中文\n"
    + "错误详情保持完整，不应被省略。".repeat(60) + "\nFULL_PROBLEM_END";
const eventSummary = {
  operation: "write_file", target: "E:/fixture/" + "long-path/".repeat(45) + "result.txt",
  host: "fixture-only", providerId: "synthetic", credentialClass: "none",
  riskCode: "fixture_network_failure", matchedRule: "synthetic-rule",
  nextAction: "重试后继续，保留未完成内容。",
};

function loadSource(relative, overrides = {}, cache = new Map()) {
  if (cache.has(relative)) return cache.get(relative);
  const source = overrides[relative] ?? fs.readFileSync(path.join(app, relative), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const compiledModule = { exports: {} };
  cache.set(relative, compiledModule.exports);
  const requireLocal = id => {
    if (!id.startsWith("@/")) return require(id);
    const target = "src/" + id.slice(2);
    const filename = [target + ".tsx", target + ".ts"].find(candidate => fs.existsSync(path.join(app, candidate)));
    if (!filename) throw new Error(`Unknown fixture import: ${id}`);
    return loadSource(filename, overrides, cache);
  };
  new Function("module", "exports", "require", output)(compiledModule, compiledModule.exports, requireLocal);
  return compiledModule.exports;
}

async function renderFixture(baselineRef) {
  const source = baselineRef ? execFileSync("git", ["show", `${baselineRef}:apps/v8-agent-os-web/${cardFile}`], { cwd: repo, encoding: "utf8" }) : fs.readFileSync(path.join(app, cardFile), "utf8");
  const { ApprovalCard } = loadSource(cardFile, { [cardFile]: source });
  const markup = renderToStaticMarkup(React.createElement(ApprovalCard, {
    title: "确认操作", body, status: "等待确认", tone: "safety", eventSummary,
  }));
  const config = loadSource("tailwind.config.ts").default;
  // Use the installed Tailwind compiler, real utility classes and global theme.
  config.content = [{ raw: source + markup + '<html class="dark">', extension: "tsx" }];
  const { css } = await postcss([tailwindcss(config)]).process(fs.readFileSync(path.join(app, "src/app/globals.css"), "utf8"), { from: path.join(app, "src/app/globals.css") });
  return {
    body, eventSummary,
    html: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style><body><main style="max-width:560px;margin:24px auto;padding:12px">${markup}</main></body></html>`,
  };
}

module.exports = { renderFixture };
if (require.main === module) {
  renderFixture(process.argv[2]).then(result => process.stdout.write(JSON.stringify(result))).catch(error => { console.error(error); process.exitCode = 1; });
}
