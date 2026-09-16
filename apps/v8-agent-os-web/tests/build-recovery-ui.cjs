const fs = require("node:fs");
const path = require("node:path");
const webpack = require("next/dist/compiled/webpack/webpack").webpack;
const root = path.resolve(__dirname, "..");
const output = path.resolve(process.argv[2] || path.join(root, ".recovery-ui"));
const phone = process.argv.includes("--phone");
const phoneRoot = path.resolve(root, "../v8-agent-os-phone");
const phoneAdapters = path.join(__dirname, "fixtures/recovery-phone-adapters.tsx");
fs.mkdirSync(output, { recursive: true });
const loader = path.join(output, "loader.cjs");
fs.writeFileSync(loader, `const ts=require(${JSON.stringify(require.resolve("typescript"))}); module.exports=function(source){return ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;};`);
fs.writeFileSync(path.join(output, "index.html"), '<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body><div id="root"></div><script src="/bundle.js"></script></body></html>');
webpack({
    mode: "development", entry: path.join(__dirname, phone ? "fixtures/recovery-phone-ui.tsx" : "fixtures/recovery-ui.tsx"),
    output: { path: output, filename: "bundle.js" }, devtool: false,
    resolve: { extensions: [".tsx", ".ts", ".js", ".json"], alias: {
        ...(phone ? {
            "@/src/providers/app-session": phoneAdapters, "@/src/providers/ui-prefs": phoneAdapters,
            "@/src/lib/mobile-storage": phoneAdapters, "@expo/vector-icons": phoneAdapters,
            "@/src": path.join(phoneRoot, "src"), "react-native$": path.join(__dirname, "fixtures/recovery-native-adapter.ts"),
            "react-native-web": path.join(phoneRoot, "node_modules/react-native-web/dist/cjs"),
        } : {}),
        "@": path.join(root, "src"), "next/navigation": path.join(__dirname, "fixtures/recovery-router.ts"),
        "react": path.join(root, "node_modules/react"), "react-dom": path.join(root, "node_modules/react-dom"),
    } },
    module: { rules: [{ test: /\.tsx?$/, use: loader }] },
}, (error, stats) => {
    if (error || stats.hasErrors()) { console.error(error || stats.toString({ all: false, errors: true })); process.exitCode = 1; }
    else console.log(output);
});
