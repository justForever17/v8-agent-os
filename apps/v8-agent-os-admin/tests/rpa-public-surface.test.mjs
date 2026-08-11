import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { projectPublicRpaAvailability } from "../src/lib/server/rpa-public-surface.mjs";

test("RPA availability exposes capability booleans without local paths or raw import errors", () => {
    const projected = projectPublicRpaAvailability({
        robotFramework: true,
        robotFrameworkDetail: {
            origin: "C:\\Users\\private-user\\feature-packs\\robot\\__init__.py",
            error: "private import error",
        },
        rpaFramework: false,
        rpaFrameworkDetail: {
            origin: "/home/private-user/feature-packs/RPA/__init__.py",
            error: "native loader failed",
        },
        libraries: {
            "RPA.Windows": true,
            "RPA.Browser.Selenium": true,
            "RPA.Excel.Files": false,
            "Private.Library": true,
        },
        libraryDetails: {
            "RPA.Windows": { origin: "C:\\private\\RPA\\Windows.py", error: null },
        },
    });

    assert.deepEqual(projected, {
        robotFramework: true,
        rpaFramework: false,
        libraries: {
            "RPA.Windows": true,
            "RPA.Browser.Selenium": true,
            "RPA.Excel.Files": false,
        },
    });
    const serialized = JSON.stringify(projected);
    assert.doesNotMatch(serialized, /origin|error|private-user|Private\.Library/i);
});

test("malformed RPA availability fails closed", () => {
    assert.deepEqual(projectPublicRpaAvailability(null), {
        robotFramework: false,
        rpaFramework: false,
        libraries: {
            "RPA.Windows": false,
            "RPA.Browser.Selenium": false,
            "RPA.Excel.Files": false,
        },
    });
});

test("RPA availability proxy has a bounded, fail-closed deadline", () => {
    const routeSource = fs.readFileSync(
        path.resolve("src", "app", "api", "rpa", "[[...segments]]", "route.ts"),
        "utf8",
    );
    assert.match(routeSource, /AbortSignal\.timeout\(7_000\)/);
    assert.match(routeSource, /rpa_availability_timeout/);
});
