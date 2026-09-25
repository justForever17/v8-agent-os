import assert from "node:assert/strict";
import test from "node:test";

import { resolveProductOrigin } from "../src/product_origin.mjs";
import { resolveProductOrigin as coreResolveProductOrigin } from "../src/core_control.mjs";

test("resolveProductOrigin returns default 9527 loopback origin", () => {
  assert.equal(resolveProductOrigin({}), "http://127.0.0.1:9527");
  assert.equal(coreResolveProductOrigin({}), "http://127.0.0.1:9527");
});

test("resolveProductOrigin honors PORT environment variable", () => {
  assert.equal(resolveProductOrigin({ PORT: "19527" }), "http://127.0.0.1:19527");
});

test("resolveProductOrigin honors local V8_WEB_BASE_URL, AUTH_URL, and NEXTAUTH_URL", () => {
  assert.equal(resolveProductOrigin({ V8_WEB_BASE_URL: "http://localhost:9527" }), "http://localhost:9527");
  assert.equal(resolveProductOrigin({ AUTH_URL: "http://127.0.0.1:19528" }), "http://127.0.0.1:19528");
  assert.equal(resolveProductOrigin({ NEXTAUTH_URL: "http://[::1]:9527" }), "http://[::1]:9527");
});

test("resolveProductOrigin rejects non-local hostnames or non-root paths", () => {
  assert.throws(() => resolveProductOrigin({ V8_WEB_BASE_URL: "http://example.com:9527" }), /Product Web requires a local origin/);
  assert.throws(() => resolveProductOrigin({ V8_WEB_BASE_URL: "http://127.0.0.1:9527/subpath" }), /Product Web requires a local origin/);
  assert.throws(() => resolveProductOrigin({ V8_WEB_BASE_URL: "ftp://127.0.0.1:9527" }), /Product Web requires a local origin/);
});
