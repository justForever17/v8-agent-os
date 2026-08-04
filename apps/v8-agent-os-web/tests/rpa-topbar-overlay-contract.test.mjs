import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function read(...segments) {
    return fs.readFileSync(path.join(webRoot, ...segments), "utf8");
}

test("RPA topbar preloads code without mounting the data-fetching panel", () => {
    const source = read("src", "components", "layout", "RpaTopbarOverlay.tsx");
    const mountEffect = source.match(/useEffect\(\(\) => \{([\s\S]*?)\n\s*\}, \[\]\);/u)?.[1] || "";

    assert.match(mountEffect, /void loadRPAQuickPanel\(\)/);
    assert.doesNotMatch(mountEffect, /setActivated|requestIdleCallback|setTimeout/);
    assert.equal((source.match(/setActivated\(true\)/g) || []).length, 1);
    assert.match(source, /if \(!activated\) setActivated\(true\)/);
    assert.match(source, /\{activated \? createPortal\(/);
    assert.doesNotMatch(source, /setMounted/);
    assert.doesNotMatch(source, /fetch\(["'`]\/api\/rpa\//);
});

test("the dedicated RPA route owns the only RPA panel on that page", () => {
    const overlay = read("src", "components", "layout", "RpaTopbarOverlay.tsx");
    const page = read("src", "app", "rpa", "page.tsx");

    assert.match(overlay, /if \(pathname === "\/rpa"\) return null/);
    assert.match(overlay, /<RpaTopbarOverlayContent key=\{pathname\} \/>/);
    assert.equal((page.match(/<RPAQuickPanel\b/g) || []).length, 1);
    assert.doesNotMatch(page, /RpaTopbarOverlay/);
});
