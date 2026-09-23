const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("structured Web narrative does not reinterpret inline think tags", () => {
  const parser = readText("apps/v8-agent-os-web/src/lib/chat/content-detector.ts");
  const dispatcher = readText("apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx");
  assert.match(parser, /parseInlineThinking = true/);
  assert.match(dispatcher, /parseContentToBlocks\(node\.content, isStreaming, 0, false, node\.editedBy === "user"\)/);
});

test("structured Phone narrative does not reinterpret inline think tags", () => {
  const parser = readText("apps/v8-agent-os-phone/src/lib/content-detector.ts");
  const dispatcher = readText("apps/v8-agent-os-phone/src/components/chat/ContentDispatcher.tsx");
  const bubble = readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx");
  assert.match(parser, /parseInlineThinking = true/);
  assert.match(dispatcher, /parsePhoneContentBlocks\(String\(node\.content \|\| ""\), false, 0, false, node\.editedBy === "user"\)/);
  assert.match(bubble, /hasStructuredNodes \? \[\] : parsePhoneContentBlocks\(String\(message\.content \|\| ""\), false, 0, true, message\.editedBy === "user"\)/);
});

test("structured reasoning is not duplicated; legacy reasoning and literal user revisions remain distinct", () => {
  for (const [file, name] of [
    ["apps/v8-agent-os-web/src/lib/chat/content-detector.ts", "parseContentToBlocks"],
    ["apps/v8-agent-os-phone/src/lib/content-detector.ts", "parsePhoneContentBlocks"],
  ]) {
    const exports = {};
    const code = ts.transpileModule(readText(file), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    new Function("exports", code)(exports);
    const parse = exports[name];
    const content = "before <think>already supplied as a separate reasoning node</think> after";
    const structured = parse(content, false, 0, false, false);
    assert.ok(structured.every(block => block.type !== "thinking"), file);
    assert.equal(structured.map(block => block.content.trim()).join(" "), "before after", file);
    assert.ok(parse(content, false, 0, true, false).some(block => block.type === "thinking"), "The legacy parser is a counterexample to re-enabling inline thinking.");
    const edited = content + '<tool-call id="quoted" /><voice>quoted audio</voice>';
    assert.deepEqual(parse(edited, true, 0, false, true).map(block => [block.type, block.content]), [["text", edited]]);
  }
});

test("Web and Phone animate only the terminal node of the active trace segment", () => {
  const web = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const phone = readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx");

  assert.match(web, /index === timelineSegments\.length - 1/);
  assert.match(web, /nodeIdx === segment\.nodes\.length - 1/);
  assert.doesNotMatch(web, /isExecuting=\{!!\(isLoading && isLast\)\}/);
  assert.match(phone, /active=\{segment\.active\}/);
  assert.match(phone, /index === nodes\.length - 1/);
  assert.match(phone, /index === timelineSegments\.length - 1/);
  assert.match(phone, /index === fallbackBlocks\.length - 1/);
  assert.doesNotMatch(phone, /isExecuting=\{assistantActive\}/);
});

test("Web reserves the assistant action row while a streamed message settles", () => {
  const web = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  assert.match(web, /\(!isLoading && message\.content\) \|\| \(isLoading && message\.content\)/);
  assert.match(web, /isLoading \? \(\s*<div className="h-6" aria-hidden="true"/);
});
