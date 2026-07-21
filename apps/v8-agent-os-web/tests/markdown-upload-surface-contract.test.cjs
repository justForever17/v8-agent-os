const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web and Phone render Markdown tables as bounded two-axis viewports without rewriting authored line breaks", () => {
  const webMarkdown = readText("apps/v8-agent-os-web/src/components/chat/MarkdownRenderer.tsx");
  const phoneMarkdown = readText("apps/v8-agent-os-phone/src/components/chat/MarkdownRenderer.tsx");

  assert.match(webMarkdown, /data-markdown-table-viewport/);
  assert.match(webMarkdown, /overflow-auto overscroll-contain/);
  assert.match(webMarkdown, /whitespace-nowrap border-b border-r/);
  assert.match(webMarkdown, /mb-2 whitespace-pre-wrap last:mb-0/);
  assert.doesNotMatch(webMarkdown, /\) : <table>\{children\}<\/table>/);

  assert.match(phoneMarkdown, /import \{ Linking, ScrollView, StyleSheet, Text, View \}/);
  assert.match(phoneMarkdown, /columnWidths/);
  assert.match(phoneMarkdown, /Math\.min\(960, widest/);
  assert.match(phoneMarkdown, /<ScrollView\s+horizontal\s+nestedScrollEnabled/);
  assert.match(phoneMarkdown, /showsHorizontalScrollIndicator=\{false\}/);
  assert.match(phoneMarkdown, /showsVerticalScrollIndicator=\{false\}/);
  assert.match(phoneMarkdown, /numberOfLines=\{1\}/);
  assert.match(phoneMarkdown, /\.split\("\\n\\n"\)/);
});

test("Web file picking and drag-and-drop share the canonical attachment upload path", () => {
  const input = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");

  assert.match(input, /const uploadFiles = React\.useCallback/);
  assert.match(input, /appendUploadScope\(formData, uploadScope, "web_upload"\)/);
  assert.match(input, /fetch\(`\/api\/upload`, \{ method: 'POST', body: formData \}\)/);
  assert.match(input, /void uploadFiles\(selectedFiles\)/);
  assert.match(input, /void uploadFiles\(droppedFiles\)/);
  assert.match(input, /onDragEnter=\{handleFileDragEnter\}/);
  assert.match(input, /onDrop=\{handleFileDrop\}/);
  assert.match(input, /files\.length \+ newFiles\.length > 14/);
});
