import assert from "node:assert/strict";

// Browser/component contract with a fake OS boundary. This never saves a real
// credential, registers a component, authenticates Windows or substitutes for live.
export async function verifySystemOperationsCard(page, baseUrl) {
    const empty = () => ({ configured: false, username: "", domain: "" });
    const settings = { profiles: { unlock: empty(), run_privileged: empty() }, currentAccount: { username: "fixture-current-account", domain: "." }, platform: { os: "windows", unlock: { registered: false }, privilege: { registered: false }, setup: { state: "idle" } } };
    const submitted = [];
    // The parent page needs its own registry envelope before it mounts this
    // card. CI intentionally runs no Engine; a developer's live Engine must
    // not accidentally supply this fixture dependency.
    let configurationReads = 0;
    await page.route("**/api/config-registry/system-base*", (route) => {
        configurationReads += 1;
        return route.fulfill({ json: {
            domain: "system-base", title: "Fixture", summary: "", data: {},
            source: "isolated-ui-fixture", savePath: [], reloadRequired: false,
            warnings: [], advancedFields: [],
        } });
    });
    await page.route("**/api/system-operations/**", async (route) => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        if (path.endsWith("/settings")) return route.fulfill({ json: settings });
        const action = path.split("/").at(-1);
        if (request.method() === "PUT") {
            const body = request.postDataJSON();
            submitted.push(body);
            if (body.username !== settings.currentAccount.username) return route.fulfill({ status: 422, json: { detail: { code: "system_account_not_found" } } });
            settings.profiles[action] = { configured: true, username: body.username, domain: body.domain };
        } else if (request.method() === "DELETE") settings.profiles[action] = empty();
        return route.fulfill({ json: { ok: true } });
    });
    await page.goto(`${baseUrl}/admin/system-base`, { waitUntil: "domcontentloaded" });
    const form = page.locator("form:has(#unlock-account)");
    await form.locator("#unlock-account").waitFor();
    await form.locator("#unlock-account").fill("not-an-os-account");
    await form.locator("#unlock-password").fill("synthetic-secret-中文 🔐 ");
    await form.locator('button[type="submit"]').click();
    await form.locator('[role="alert"]').waitFor();
    assert.equal(await form.locator("#unlock-password").inputValue(), "");
    assert.match(await form.locator('[role="alert"]').innerText(), /Windows/);
    await form.locator('button[type="button"]').first().click();
    assert.equal(await form.locator("#unlock-account").inputValue(), settings.currentAccount.username);
    await form.locator("#unlock-password").fill("synthetic-secret-中文 🔐 ");
    await form.locator('button[type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector('#unlock-password').value);
    assert.equal(submitted.at(-1).password, "synthetic-secret-中文 🔐 ");
    assert.equal(settings.profiles.run_privileged.configured, false);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector('#unlock-account')?.value === 'fixture-current-account');
    assert.equal(await form.locator("#unlock-password").inputValue(), "");
    await form.locator('button[type="button"]').last().click();
    await page.waitForFunction(() => document.querySelector('#unlock-account')?.value === '');
    assert.equal(settings.profiles.unlock.configured, false);
    assert.ok(configurationReads >= 2, "parent configuration is isolated on navigation and reload");
    await page.unroute("**/api/system-operations/**");
    await page.unroute("**/api/config-registry/system-base*");
}
