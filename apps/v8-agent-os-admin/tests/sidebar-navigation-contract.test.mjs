import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("admin sidebar keeps navigation groups visible without per-group collapse controls", () => {
    const source = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");

    assert.match(source, /ADMIN_NAV_GROUPS\.map/);
    assert.match(source, /\{t\(group\.title\)\}/);
    assert.doesNotMatch(source, /openGroups|setOpenGroups|isGroupActive/);
});

test("admin sidebar navigation does not expose text or link context menus", () => {
    const source = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Sidebar.tsx"), "utf8");

    assert.match(source, /data-v8-context-menu-ignore/);
    assert.match(source, /onContextMenu=\{\(event\) => event\.preventDefault\(\)\}/);
    assert.match(source, /select-none overflow-y-auto/);
});

test("admin sidebar group labels are localized in Chinese", () => {
    const locale = JSON.parse(fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "zh-CN.json"), "utf8"));

    assert.equal(locale["lib.admin.navigation.k44e34d5c"], "概览");
    assert.equal(locale["lib.admin.navigation.k7e688826"], "能力");
    assert.equal(locale["lib.admin.navigation.k3ff43c59"], "平台");
});
