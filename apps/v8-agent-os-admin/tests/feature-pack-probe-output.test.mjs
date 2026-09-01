import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeFeaturePackProbeOutput,
  parseFeaturePackProbeMarker,
} from "../src/lib/server/feature-pack-probe-output.ts";


test("feature pack probe parses a UTF-16LE-shaped success sentinel", () => {
  const sentinel = '__V8_SMOKE__{"kind":"onnx","selectedExecutionProvider":"CPUExecutionProvider"}\r\n';
  const decodedAsUtf8 = Buffer.from(sentinel, "utf16le").toString("utf8");

  assert.match(decodedAsUtf8, /\u0000/);
  assert.doesNotMatch(normalizeFeaturePackProbeOutput(decodedAsUtf8), /\u0000/);
  assert.deepEqual(parseFeaturePackProbeMarker(decodedAsUtf8, "__V8_SMOKE__"), {
    kind: "onnx",
    selectedExecutionProvider: "CPUExecutionProvider",
  });
});


test("feature pack probe keeps the sentinel boundary strict", () => {
  assert.equal(
    parseFeaturePackProbeMarker('untrusted-prefix __V8_SMOKE__{"kind":"onnx"}', "__V8_SMOKE__"),
    null,
  );
  assert.equal(parseFeaturePackProbeMarker("__V8_SMOKE__not-json", "__V8_SMOKE__"), null);
});
