import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("admin sidebar exposes task groups with discoverable primary and secondary entries", () => {
    const source = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");

    assert.match(source, /ADMIN_TASK_NAV\.map/);
    assert.match(source, /\{t\(group\.title\)\}/);
    assert.match(source, /href=\{item\.href\} prefetch=\{false\}/);
    assert.match(source, /group\.items\.slice\(1\)\.map/);
    assert.match(source, /<Dialog open=\{mobileOpen\}/);
});

test("admin sidebar navigation does not expose text or link context menus", () => {
    const source = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");

    assert.match(source, /data-v8-context-menu-ignore/);
    assert.match(source, /onContextMenu=\{\(event\) => event\.preventDefault\(\)\}/);
    assert.match(source, /select-none overflow-y-auto/);
});

test("admin sidebar omits the redundant return rail and uses an unframed collapse control", () => {
    const source = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");

    assert.doesNotMatch(source, /WEB_CHAT_URL|backToChat|ArrowLeft/);
    assert.doesNotMatch(source, /rounded-full border border-border bg-background/);
    assert.match(source, /onClick=\{toggleCollapse\}[\s\S]*focus-visible:ring-2/);
});

test("admin sidebar group labels are localized in Chinese", () => {
    const locale = JSON.parse(fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "zh-CN.json"), "utf8"));

    assert.equal(locale["lib.admin.navigation.k44e34d5c"], "概览");
    assert.equal(locale["lib.admin.navigation.k7e688826"], "能力");
    assert.equal(locale["lib.admin.navigation.k3ff43c59"], "平台");
});

test("admin sign-out clears the session without accepting a cross-origin callback", () => {
    const sidebar = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");
    const topbar = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");
    const lockIndex = sidebar.indexOf("window.v8osShell.lockAdminSession()");
    const signOutIndex = sidebar.indexOf('signOut({ redirect: false, redirectTo: "/login" })');

    assert.match(sidebar, /signOut\(\{ redirect: false, redirectTo: "\/login" \}\)/);
    assert.match(sidebar, /window\.v8osShell\.lockAdminSession\(\)/);
    assert.ok(lockIndex >= 0 && lockIndex < signOutIndex, "Shell must lock before the session-clear request starts");
    assert.match(sidebar, /fetch\("\/api\/auth\/session", \{/);
    assert.match(sidebar, /if \(remainingSession\?\.user\) throw new Error\("admin_session_not_cleared"\)/);
    assert.match(sidebar, /if \(shellLocked\) \{[\s\S]*window\.location\.reload\(\)/);
    assert.match(sidebar, /window\.location\.replace\(canonicalLoginUrl\)/);
    assert.doesNotMatch(sidebar, /onClick=\{\(\) => signOut\(\)\}/);
    assert.match(topbar, /disabled: adminSessionLock\?\.locked === true/);
});
