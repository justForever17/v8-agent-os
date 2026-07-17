import assert from "node:assert/strict";
import test from "node:test";

import {
  buildComposerInlineSegments,
  insertComposerReference,
  removeComposerReferenceAtBackspace,
  resolveComposerInlineQuery,
  stripComposerReferences,
} from "../dist/index.js";

const command = { kind: "command", id: "command:spec list", label: "spec list" };
const design = { kind: "skill", id: "skill:design", label: "Design System" };
const animation = { kind: "skill", id: "skill:animation", label: "Find Animation Opportunities" };

test("inline references preserve arbitrary text order and reference colors can follow kind", () => {
  const text = "先看 @Design System，再执行 /spec list，最后问 @Find Animation Opportunities";
  const segments = buildComposerInlineSegments(text, [command, design, animation]);
  assert.equal(segments.map((segment) => segment.text).join(""), text);
  assert.deepEqual(
    segments.filter((segment) => segment.type === "reference").map((segment) => segment.reference?.kind),
    ["skill", "command", "skill"],
  );
});

test("one command is allowed while multiple mentions remain available", () => {
  assert.deepEqual(resolveComposerInlineQuery("正文 /sp", 6, false), {
    kind: "command",
    start: 3,
    end: 6,
    query: "sp",
  });
  assert.equal(resolveComposerInlineQuery("正文 /sp", 6, true), null);
  assert.equal(resolveComposerInlineQuery("@des", 4, true)?.kind, "mention");
});

test("selected references do not reopen the picker while normal text continues", () => {
  const text = "看看 @Design System 后续正文";
  assert.equal(resolveComposerInlineQuery(text, text.length, false, [design]), null);
});

test("insertion happens at the caret and stripping only removes structured references", () => {
  const source = "开头 @des 后文";
  const query = resolveComposerInlineQuery(source, 7, false);
  assert.ok(query);
  const inserted = insertComposerReference(source, query, design);
  assert.equal(inserted.text, "开头 @Design System 后文");
  assert.equal(stripComposerReferences(inserted.text, [design]), "开头 后文");
});

test("backspace removes a whole inline reference atomically", () => {
  const text = "前文 @Design System 后文";
  const caret = text.indexOf(" 后文");
  const deletion = removeComposerReferenceAtBackspace(text, [design], caret, caret);
  assert.ok(deletion);
  assert.equal(deletion.text, "前文 后文");
  assert.deepEqual(deletion.removedReferenceIds, ["skill:design"]);
});
