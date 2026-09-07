import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";

if (!process.argv.includes("--live")) throw new Error("Pass --live to test a running Admin UI.");
const baseUrl = process.env.V8_ADMIN_TEST_URL || "http://127.0.0.1:9528";
const login = process.env.V8_ADMIN_TEST_LOGIN;
const password = process.env.V8_ADMIN_TEST_PASSWORD;
if (!login || !password) throw new Error("Set V8_ADMIN_TEST_LOGIN and V8_ADMIN_TEST_PASSWORD.");
const output = fs.mkdtempSync(path.join(os.tmpdir(), "v8-model-token-ui-"));
const executablePath = [process.env.V8_BROWSER_EXECUTABLE,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && fs.existsSync(candidate));
const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
let page;
const errors = [];
try {
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN" });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.locator("#login").fill(login);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:[/?].*)?$/, { timeout: 30_000 });

    // Only configuration transports are fixtures. Forms and post-save reloads
    // are the production UI; no real model setting or credential is changed.
    let model;
    const provider = { id: "fixture", code: "fixture", name: "Budget Fixture", type: "API",
        apiStandard: "openai", baseUrl: "https://example.org/v1", isEnabled: true };
    const control = () => ({ data: { models: [model], providersOverview: [], roles: [] } });
    const writes = [];
    await page.route("**/api/model-hub/bootstrap*", (route) => route.fulfill({ json: {
        providers: [{ ...provider, models: [model] }], models: [model], hubEnvelope: control(), catalog: { providers: [] },
    } }));
    await page.route("**/api/providers/fixture*", (route) => {
        assert.equal(route.request().method(), "GET");
        return route.fulfill({ json: { ...provider, models: [model] } });
    });
    await page.route("**/api/model-control-plane*", (route) => route.fulfill({ json: control() }));
    await page.route("**/api/models/**", async (route) => {
        if (route.request().method() !== "PUT") return route.continue();
        const patch = route.request().postDataJSON();
        writes.push(patch);
        model = { ...model, outputTokenMode: patch.outputTokenMode,
            contextWindow: Number(patch.contextWindow),
            ...(patch.maxTokens !== undefined ? { maxTokens: Number(patch.maxTokens) } : {}) };
        await route.fulfill({ json: { ok: true } });
    });
    for (const routeName of ["model-hub", "models/providers/fixture"]) {
        model = { id: "fixture::budget-model", modelRef: "fixture::budget-model", providerId: "fixture",
            modelId: "budget-model", type: "TEXT", contextWindow: 1000000, maxTokens: 4096,
            outputTokenMode: "auto", isEnabled: true, provider: { name: provider.name } };
        await page.goto(`${baseUrl}/admin/${routeName}`, { waitUntil: "domcontentloaded" });
        const open = () => page.locator('button[title="编辑"],button[title="Edit"]').last().click();
        await open();
        const dialog = page.getByRole("dialog");
        const budget = dialog.locator('input[name="maxTokens"]');
        const mode = dialog.getByRole("combobox", { name: /输出预算模式|Output budget mode/i });
        const setMode = async (value) => {
            await mode.click();
            await page.getByRole("option", { name: value, exact: true }).click();
        };
        const save = async () => {
            const response = page.waitForResponse((item) => item.request().method() === "PUT" && item.url().includes("/api/models/"));
            await dialog.locator('button[type="submit"]').click();
            await response;
            await dialog.waitFor({ state: "hidden" });
        };
        assert.equal(await budget.isDisabled(), true);
        await setMode("自定义上限");
        await budget.fill("8192");
        await dialog.locator('input[name="contextWindow"]').fill("500000");
        await save();
        assert.equal(writes.at(-1).outputTokenMode, "fixed");
        assert.equal(Number(writes.at(-1).maxTokens), 8192);
        assert.equal(model.contextWindow, 500000);
        await open();
        assert.equal(await budget.inputValue(), "8192");
        await setMode("自动");
        await dialog.locator('input[name="contextWindow"]').fill("128000");
        await save();
        assert.equal(writes.at(-1).outputTokenMode, "auto");
        assert.equal(writes.at(-1).maxTokens, undefined);
        assert.equal(model.contextWindow, 128000);
        await page.reload({ waitUntil: "domcontentloaded" });
        await open();
        assert.equal(await budget.isDisabled(), true);
        await setMode("自定义上限");
        assert.equal(await budget.inputValue(), "8192");
        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 1000 });
            assert.equal(await dialog.evaluate((node) => node.scrollWidth > node.clientWidth + 1), false);
            await dialog.screenshot({ path: path.join(output, `${routeName.replaceAll("/", "-")}-${width}.png`) });
        }
        await page.keyboard.press("Escape");
        await page.setViewportSize({ width: 1440, height: 1000 });
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, evidenceMode: "preview-ui-mocked-config", writes: writes.length, output,
        checks: ["both-editors", "500k-and-128k-save", "auto-fixed-roundtrip", "manual-value-retained", "reload-parity", "desktop-mobile-fit"] }));
} catch (error) {
    if (page && !page.url().includes("/login")) await page.screenshot({ path: path.join(output, "failure.png") });
    console.error(JSON.stringify({ output, errors }));
    throw error;
} finally {
    await browser.close();
}
