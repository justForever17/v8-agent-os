// Bundle the actual Phone components with React Native Web, a synthetic theme
// provider, and decorative icons replaced. No Phone state, network or model APIs.
// node tests/build-approval-card-ui.cjs <installed-webpack-module> <output-dir>
const fs = require("node:fs");
const path = require("node:path");
const phoneRoot = path.resolve(__dirname, "..");
const webpack = require(path.resolve(process.argv[2])).webpack;
const output = path.resolve(process.argv[3]);
fs.mkdirSync(output, { recursive: true });
const component = path.join(phoneRoot, "src/components/chat/ApprovalCard.tsx");
const tokens = path.join(phoneRoot, "src/theme/tokens.ts");
const locales = path.join(phoneRoot, "src/i18n/locales");
fs.writeFileSync(path.join(output, "prefs.ts"), `
import { getThemeColors } from ${JSON.stringify(tokens)};
import en from ${JSON.stringify(path.join(locales, "en.json"))};
import zh from ${JSON.stringify(path.join(locales, "zh-CN.json"))};
export const useUiPrefs = () => {
    const params = new URLSearchParams(location.search);
    const themeMode = params.get("theme") === "dark" ? "dark" : "light";
    const strings = params.get("lang") === "en" ? en : zh;
    return { themeMode, colors: getThemeColors(themeMode), t: (key) => strings[key] || key };
};
`);
fs.writeFileSync(path.join(output, "icons.tsx"), `
import React from "react";
export const MaterialCommunityIcons = ({ name }) => <span aria-hidden="true">{name === "chevron-up" ? "⌃" : name === "chevron-down" ? "⌄" : "◇"}</span>;
`);
fs.writeFileSync(path.join(output, "entry.tsx"), `
import React from "react";
import { createRoot } from "react-dom/client";
import { ApprovalCard } from ${JSON.stringify(component)};
document.body.style.background = new URLSearchParams(location.search).get("theme") === "dark" ? "#070B16" : "#FFFFFF";
const body = Array.from({length: 12}, (_, i) => "请确认目标文件的写入权限，操作尚未执行。 " + i).join("\\n") + "\\nISSUE_TAIL";
const eventSummary = {
    operation: "修改文件", target: "E:/synthetic/workspace/" + "long-folder/".repeat(10) + "target.txt",
    host: "fixture-device", providerId: "fixture-provider", credentialClass: "system",
    riskCode: "WRITE_REQUIRES_REVIEW", matchedRule: "RULE_TAIL", nextAction: "确认授权后继续 NEXT_TAIL",
};
const props = { title: "安全复核", body, tone: "safety", status: "waiting", eventSummary };
createRoot(document.getElementById("root")).render(<main style={{padding: 12, display:"flex",flexDirection:"column",gap:16}}>
    <section data-testid="compact"><ApprovalCard {...props}/></section>
    <section data-testid="spec"><ApprovalCard {...props} compact={false}/></section>
</main>);
`);
fs.writeFileSync(path.join(output, "ts-loader.cjs"), `
const ts = require(${JSON.stringify(require.resolve("typescript"))});
module.exports = function(source) { return ts.transpileModule(source, {fileName: this.resourcePath, compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
    esModuleInterop: true,
}}).outputText; };
`);
fs.writeFileSync(path.join(output, "index.html"), '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script src="fixture.js"></script>');
webpack({
    mode: "development", devtool: false, context: phoneRoot,
    entry: path.join(output, "entry.tsx"), output: { path: output, filename: "fixture.js" },
    resolve: { extensions: [".tsx", ".ts", ".js"], modules: [path.join(phoneRoot, "node_modules"), "node_modules"], alias: {
        "react-native$": require.resolve("react-native-web"),
        "@/src/providers/ui-prefs$": path.join(output, "prefs.ts"),
        "@expo/vector-icons$": path.join(output, "icons.tsx"),
        "@": phoneRoot,
    } },
    module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(output, "ts-loader.cjs") }] },
    optimization: { minimize: false },
}, (error, stats) => {
    if (error || stats.hasErrors()) { console.error(error || stats.toString({ all:false, errors:true })); process.exitCode = 1; }
    else console.log("Phone approval fixture bundled: " + output);
});
