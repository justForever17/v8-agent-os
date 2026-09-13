const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const app = path.resolve(__dirname, '..');
function source(relative) {
  const filename = path.join(app, relative);
  return ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
function find(root, predicate) {
  let result;
  function visit(node) { if (predicate(node)) result = node; ts.forEachChild(node, visit); }
  visit(root);
  assert.ok(result, 'the production rendering branch must be found');
  return result;
}
function compile(code, dependencies) {
  const module = { exports: {} };
  const output = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function('module', 'exports', 'require', ...Object.keys(dependencies), output)(module, module.exports, require, ...Object.values(dependencies));
  return module.exports;
}

const chat = source('src/components/chat/ChatMessage.tsx');
const dispatcher = source('src/components/chat/ContentDispatcher.tsx');
const renderable = find(chat, node => ts.isFunctionDeclaration(node) && node.name?.text === 'isRenderableTimelineNode').getText(chat);
const assistantNarrative = find(chat, node => ts.isVariableDeclaration(node) && node.name.getText(chat) === 'hasAssistantNarrativeNode').getText(chat);
const fallback = find(chat, node => ts.isVariableDeclaration(node) && node.name.getText(chat) === 'assistantContentFallbackVisible').getText(chat);
const fallbackJsx = find(chat, node => ts.isJsxExpression(node) && node.expression && ts.isBinaryExpression(node.expression)
  && node.expression.left.getText(chat) === 'assistantContentFallbackVisible').expression.getText(chat);
const narrative = find(dispatcher, node => ts.isCaseClause(node) && node.expression.getText(dispatcher) === "'narrative'")
  .statements.map(node => node.getText(dispatcher)).join('\n');
const detector = compile(fs.readFileSync(path.join(app, 'src/lib/chat/content-detector.ts'), 'utf8'), {});

// Execute the original visibility predicates, narrative parser/JSX and fallback
// JSX. Only the leaf Markdown presentation is a plain text paragraph here.
const MarkdownRenderer = ({ content }) => React.createElement('p', null, content);
const MessageBlockItem = ({ block }) => React.createElement(MarkdownRenderer, { content: block.content });
const evaluate = compile(`
  ${renderable}
  function Narrative({node}) { const isStreaming=false; ${narrative} }
  exports.render = function(message) {
    const normalizedContent=message.content;
    const renderableNodes=message.nodes.filter(node=>isRenderableTimelineNode(node,false));
    const ${assistantNarrative}; const ${fallback};
    return <>{renderableNodes.map(node=><Narrative key={node.id} node={node}/>)}{${fallbackJsx}}</>;
  };
`, { MarkdownRenderer, MessageBlockItem, parseContentToBlocks: detector.parseContentToBlocks });

test('canonical assistant node and mirrored content render one body; a role-less synthetic node reproduces the duplicate', () => {
  const content = Array.from({ length: 80 }, (_, index) => `A paragraph ${String(index + 1).padStart(3, '0')} — Synthetic history.`).join('\n\n');
  const seedNode = { id: 'fixture-text', kind: 'narrative', content, finalized: true };
  const fixtureHtml = renderToStaticMarkup(evaluate.render({ role: 'assistant', content, nodes: [seedNode] }));
  const canonicalHtml = renderToStaticMarkup(evaluate.render({ role: 'assistant', content, nodes: [{ ...seedNode, role: 'assistant', timestamp: 1 }] }));
  const labels = html => [...html.matchAll(/A paragraph (\d{3})/g)].map(match => match[1]);
  assert.equal(labels(fixtureHtml).length, 160);
  assert.deepEqual(labels(fixtureHtml).slice(79, 82), ['080', '001', '002']);
  assert.equal(labels(canonicalHtml).length, 80);
  assert.deepEqual(labels(canonicalHtml), Array.from({ length: 80 }, (_, index) => String(index + 1).padStart(3, '0')));
});

test('normal content-only assistant fallback remains visible exactly once', () => {
  const html = renderToStaticMarkup(evaluate.render({ role: 'assistant', content: 'fallback-only-fixture', nodes: [] }));
  assert.equal(html.split('fallback-only-fixture').length - 1, 1);
});
