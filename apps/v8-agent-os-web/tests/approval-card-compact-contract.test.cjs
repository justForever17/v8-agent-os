/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const test = require("node:test");
const { renderFixture } = require("./approval-card-fixture.cjs");

test("the actual compact card renders a closed native disclosure with the complete issue and event fields", async () => {
  const { html, eventSummary } = await renderFixture();
  assert.match(html, /<details\b[^>]*data-approval-card="compact"/);
  assert.doesNotMatch(html, /<details\b[^>]*\bopen[= >]/);
  assert.match(html, /<summary\b/);
  assert.match(html, /<p\b[^>]*>[\s\S]*FULL_PROBLEM_END<\/p>/);
  for (const value of Object.values(eventSummary)) assert.ok(html.includes(value));
  // Full interaction, layout, keyboard and clipboard are checked in Chromium
  // by approval_card_disclosure.py against this same production component.
});
