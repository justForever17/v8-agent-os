import assert from "node:assert/strict";
import test from "node:test";
import { actOnAgentPage, closeAgentPage, observeAgentPage, omitAriaInputValues } from "../../scripts/browser_agent_surface.mjs";

function fixture() {
  const doc = {};
  globalThis.document = doc;
  const events = [];
  function node(tag, text, attributes = {}) {
    return { tagName: tag, innerText: text, parentElement: null, ownerDocument: doc,
      getAttribute: (name) => attributes[name] ?? null, getClientRects: () => [1],
      scrollBy: (value) => events.push(["scroll", value]), attributes };
  }
  const button = node("BUTTON", "Save", { id: "save", role: "button" });
  const input = node("INPUT", "", { id: "title", type: "text", "aria-label": "Title" });
  const body = node("BODY", "Save");
  body.querySelectorAll = () => [button, input];
  button.parentElement = input.parentElement = body;
  let currentUrl = "https://example.test";
  let matches = 1;
  const handles = [];
  function locator(element) {
    return { count: async () => matches,
      evaluateHandle: async (fn) => { const value = fn(element); const handle = { value, disposed: false,
        evaluate: async (read) => read(value), dispose: async () => { handle.disposed = true; } }; handles.push(handle); return handle; },
      evaluate: async (fn, arg) => fn(element, arg?.value ?? arg),
      ariaSnapshot: async () => '- document\n  - textbox "Title"\n  - button "Save"',
      click: async (options) => events.push(["click", options]),
      fill: async (text, options) => events.push(["fill", text, options]),
      press: async (key, options) => events.push(["press", key, options]),
    };
  }
  const page = { on() {}, once() {}, url: () => currentUrl, title: async () => "Fixture",
    locator: (selector) => locator(selector === "css:light=html > body" ? body : selector === "#title" ? input : button),
    getByRole: (role, opts) => { assert.equal(opts.exact, true); return locator(role === "textbox" ? input : button); },
    close: async () => events.push(["close"]),
  };
  return { page, button, input, events, handles, setUrl: (url) => { currentUrl = url; }, setMatches: (n) => { matches = n; } };
}

test("hierarchical observation and unique role action, no force or first fallback", async () => {
  const f = fixture();
  const seen = await observeAgentPage(f.page);
  assert.match(seen.accessibility, /\n  - button/);
  assert.equal(seen.dom[1].parent, 0);
  assert.equal(seen.dom[2].text, "");
  const result = await actOnAgentPage(f.page, { action: "click", role: "button", name: "Save", observationId: seen.observationId });
  assert.equal(result.applied, true);
  assert.deepEqual(f.events, [["click", { timeout: 4000 }]]);
  await assert.rejects(actOnAgentPage(f.page, { action: "click", selector: "#save", observationId: seen.observationId }), /stale_observation/);
});

test("ambiguous locator, changed target and changed document never act", async () => {
  const f = fixture();
  const seen = await observeAgentPage(f.page);
  const request = { action: "click", selector: "#save", observationId: seen.observationId };
  f.setMatches(2);
  await assert.rejects(actOnAgentPage(f.page, request), /locator_ambiguous/);
  f.setMatches(1);
  f.button.innerText = "Delete account";
  await assert.rejects(actOnAgentPage(f.page, request), /target_changed_since_observation/);
  f.button.innerText = "Save";
  globalThis.document = {};
  await assert.rejects(actOnAgentPage(f.page, request), /page_changed_since_observation/);
  assert.deepEqual(f.events, []);
});

test("replacement element identity and navigation invalidate observed target", async () => {
  const f = fixture();
  const seen = await observeAgentPage(f.page);
  f.input.ownerDocument = {};
  await assert.rejects(actOnAgentPage(f.page, { action: "fill", selector: "#title", text: "hello", observationId: seen.observationId }), /target_not_in_current_observation/);
  f.setUrl("https://another.test");
  await assert.rejects(closeAgentPage(f.page, { observationId: seen.observationId }), /page_changed_since_observation/);
  assert.deepEqual(f.events, []);
});

test("password input does not disclose value and rejects automated fill", async () => {
  const f = fixture();
  f.input.attributes.type = "password";
  f.input.value = "SECRET-SHOULD-NOT-BE-READ";
  const seen = await observeAgentPage(f.page);
  assert.doesNotMatch(JSON.stringify(seen), /SECRET-SHOULD-NOT-BE-READ/);
  await assert.rejects(actOnAgentPage(f.page, { action: "fill", selector: "#title", text: "secret", observationId: seen.observationId }), /password_input_requires_user_control/);
  assert.deepEqual(f.events, []);
});

test("actual Playwright password/textbox value shape and multiline values are removed without losing sibling hierarchy", () => {
  const raw = '- main:\n  - textbox "Password": SECRET-CANARY\n  - textbox "Title: label" [disabled]: |-\n      SECRET-MULTILINE\n      second line\n  - button "Save: now"\n  - status: Saved';
  const redacted = omitAriaInputValues(raw);
  assert.doesNotMatch(redacted, /SECRET|second line/);
  assert.match(redacted, /textbox "Title: label" \[disabled\]/);
  assert.match(redacted, /\n  - button "Save: now"\n  - status: Saved/);
});

test("latest observation disposes old state; arbitrary script and hotkey are not actions", async () => {
  const f = fixture();
  const old = await observeAgentPage(f.page);
  const current = await observeAgentPage(f.page);
  assert.equal(f.handles[0].disposed, true);
  await assert.rejects(actOnAgentPage(f.page, { action: "click", selector: "#save", observationId: old.observationId }), /stale_observation/);
  await assert.rejects(actOnAgentPage(f.page, { action: "eval", selector: "#save", observationId: current.observationId }), /unsupported_agent_action/);
  await assert.rejects(actOnAgentPage(f.page, { action: "press", key: "Control+L", selector: "#save", observationId: current.observationId }), /unsupported_key/);
  assert.deepEqual(f.events, []);
});
