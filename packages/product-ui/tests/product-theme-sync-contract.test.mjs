import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("theme sync keeps the initial theme out of the reactive refresh dependencies", () => {
  const source = fs.readFileSync(path.join(packageRoot, "src", "ProductTheme.tsx"), "utf8");

  assert.match(source, /const setNextThemeRef = useRef\(setNextTheme\)/);
  assert.match(source, /if \(writeInFlightCount\.current > 0\) return Promise\.resolve\(\)/);
  assert.match(source, /setNextThemeRef\.current\(initialTheme\)/);
  assert.match(source, /Initial canonical theme must be applied exactly once/);
  assert.doesNotMatch(source, /\}, \[initialSyncState, initialTheme, refreshTheme, setNextTheme\]\);/);
});

test("theme writes preserve the optimistic theme on failure and use last-write-wins sequencing", () => {
  const source = fs.readFileSync(path.join(packageRoot, "src", "ProductTheme.tsx"), "utf8");

  assert.match(source, /const requestId = \+\+requestSequence\.current/);
  assert.match(source, /if \(requestId !== requestSequence\.current\) return/);
  assert.match(source, /setSyncState\("degraded"\)/);
  const writeFailureBlock = source.slice(
    source.indexOf('console.warn("[ProductTheme] Canonical theme write failed"'),
    source.indexOf('} finally {', source.indexOf('console.warn("[ProductTheme] Canonical theme write failed"')),
  );
  assert.doesNotMatch(writeFailureBlock, /setNextThemeRef\.current/);
});
