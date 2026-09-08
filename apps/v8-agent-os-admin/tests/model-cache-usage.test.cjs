const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const source = fs.readFileSync(path.join(__dirname, "../src/components/models/ModelCacheUsage.tsx"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const sandbox = { exports: {}, require };
vm.runInNewContext(compiled, sandbox);
const { ModelCacheUsage, ModelCacheWindowSummary } = sandbox.exports;

function translate(locale) {
    const messages = JSON.parse(fs.readFileSync(path.join(__dirname, `../src/i18n/locales/${locale}.json`), "utf8"));
    return (key, params = {}) => {
        assert.ok(Object.hasOwn(messages, key), `missing translation ${key}`);
        return messages[key].replace(/\{(\w+)\}/g, (_, name) => String(params[name]));
    };
}

for (const locale of ["en", "zh-CN"]) {
    test(`ordinary invocation preserves unknown and reported zero (${locale})`, () => {
        const t = translate(locale);
        const unknown = renderToStaticMarkup(React.createElement(ModelCacheUsage, { t }));
        assert.equal(unknown.split(t("app.admin.dashboard.page.cacheUsage.unreported")).length - 1, 2);
        const zero = renderToStaticMarkup(React.createElement(ModelCacheUsage, { t, usage: { readTokens: 0, writeTokens: 0 } }));
        assert.ok(zero.includes(t("app.admin.dashboard.page.cacheUsage.read", { tokens: "0" })));
        assert.ok(zero.includes(t("app.admin.dashboard.page.cacheUsage.write", { tokens: "0" })));
        assert.ok(!zero.includes(t("app.admin.dashboard.page.cacheUsage.unreported")));
    });

    test(`mixed history displays reported-call coverage instead of a full-window hit claim (${locale})`, () => {
        const t = translate(locale);
        const markup = renderToStaticMarkup(React.createElement(ModelCacheWindowSummary, { t, usage: {
            invocations: 4, cachedInputTokens: 1200, cacheWriteInputTokens: 100,
            cacheReadReportedInvocations: 3, cacheReadUnknownInvocations: 1,
            cacheWriteReportedInvocations: 2, cachedInputTokenRate: 0.7059,
        } }));
        assert.ok(markup.includes(t("app.admin.dashboard.page.cacheUsage.coverage", { reported: 3, writesReported: 2, total: 4, rate: "70.6%" })));
        assert.ok(markup.includes(t("app.admin.dashboard.page.cacheUsage.write", { tokens: "100" })));
        const unknown = renderToStaticMarkup(React.createElement(ModelCacheWindowSummary, { t, usage: {
            invocations: 4, cachedInputTokens: null, cacheWriteInputTokens: null,
            cacheReadReportedInvocations: 0, cachedInputTokenRate: null,
        } }));
        assert.ok(unknown.includes(t("app.admin.dashboard.page.cacheUsage.coverage", {
            reported: 0, writesReported: 0, total: 4, rate: t("app.admin.dashboard.page.cacheUsage.unreported"),
        })));
        assert.equal(renderToStaticMarkup(React.createElement(ModelCacheWindowSummary, { t })), "");
    });
}
