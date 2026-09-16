/* eslint-disable @typescript-eslint/no-require-imports */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { webcrypto, createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
function load(relative, bindings = {}) {
    const source = fs.readFileSync(path.join(root, relative), 'utf8');
    const output = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {};
    new Function('exports', 'require', 'crypto', 'TextEncoder', output)(exports, name => bindings[name] || require(name), webcrypto, TextEncoder);
    return exports;
}
const helper = load('apps/v8-agent-os-web/src/lib/spec-review.ts');
const hash = text => createHash('sha256').update(text).digest('hex');
const documentPath = '.v8/specs/fixture/requirements.md';
const document = content => ({content, documentSha256: hash(content), documentPath, truncated: false});
const approval = (id, content) => ({id, status: 'pending', request: {specId: 'spec-A', stage: 'requirements', workspacePath: 'E:/fixture', documentSha256: hash(content), documentPath}});

test('review hash preserves BOM, CRLF and a long tail; no summary can substitute for full content', async () => {
    const content = '\uFEFF# Requirement\r\n' + '内容'.repeat(120000) + '\r\nFULL_TAIL\r\n';
    const result = await helper.verifiedSpecDocument(document(content));
    assert.equal(result.content, content);
    assert.equal(result.documentSha256, hash(content));
    await assert.rejects(helper.verifiedSpecDocument({...document(content), truncated: true}));
    await assert.rejects(helper.verifiedSpecDocument({...document(content), content: content.trim()}));
    await assert.rejects(helper.verifiedSpecDocument({content: 'summary only'}));
});

test('old and legacy cards never match current content; refreshed card must retain scope and path', () => {
    const old = approval('old', 'A'); const current = document('B');
    assert.equal(helper.specReviewMatches(old, current), false);
    assert.equal(helper.specReviewMatches({id: 'legacy', request: {specId: 'spec-A'}}, document('A')), false);
    const next = approval('new', 'B');
    assert.equal(helper.validateRefreshedSpecApproval({approval: next, replacesApprovalId: 'old'}, old, current).id, 'new');
    for (const changed of [{documentPath: 'another.md'}, {workspacePath: 'E:/wrong'}, {stage: 'design'}, {specId: 'other'}]) {
        assert.throws(() => helper.validateRefreshedSpecApproval({approval: {...next, request: {...next.request, ...changed}}, replacesApprovalId: 'old'}, old, current));
    }
});

test('409 version failures and HTTP 200 semantic failures retain readable errors and machine code', () => {
    const error = helper.specError({detail: {code: 'spec_approval_document_changed', message: 'Document changed'}}, 'fallback');
    assert.equal(error.code, 'spec_approval_document_changed');
    assert.equal(error.message, 'Document changed');
    assert.equal(helper.specError({ok: false, error: 'stage is locked'}, 'fallback').message, 'stage is locked');
});

test('Web and Admin refresh preserve the shown hash and old id, with auth checked before forwarding', async () => {
    const requests = []; let webAuthorized = false; let adminAuthorized = false;
    const json = (payload, init) => Response.json(payload, init);
    const admin = load('apps/v8-agent-os-admin/src/app/api/approvals/[id]/refresh-spec-review/route.ts', {
        'next/server': {},
        '@/lib/server/runtime-config': {resolveInternalSecret: () => 'synthetic-secret'},
        '@/lib/server/request-auth': {resolveAuthorizedUserEmail: async () => adminAuthorized ? 'test@example.invalid' : null, unauthorizedJson: () => json({error: 'Unauthorized'}, {status: 401})},
        '@/lib/server/engine-command-proxy': {proxyEngineCommand: async (req, url, target, headers) => {
            requests.push({url, target, headers, body: await req.json()});
            return json({approval: approval('new', 'B'), replacesApprovalId: 'old'});
        }},
    });
    const web = load('apps/v8-agent-os-web/src/app/api/approvals/[id]/refresh-spec-review/route.ts', {
        'next/server': {},
        '@/lib/server/proxy/client-proxy': {
            requireClientProxyContext: async () => webAuthorized ? {context: {}} : {response: json({error: 'Unauthorized'}, {status: 401})},
            safeClientProxyFetch: async (_context, url, init) => {
                assert.equal(url, '/approvals/old/refresh-spec-review');
                return {response: await admin.POST(new Request('http://synthetic.invalid' + url, init), {params: Promise.resolve({id: 'old'})})};
            },
        },
        '@/lib/server/proxy/proxy-response': {relayJsonProxyResponse: response => response},
    });
    const invoke = () => web.POST(new Request('http://synthetic.invalid', {method: 'POST', body: JSON.stringify({response: {documentSha256: hash('B')}})}), {params: Promise.resolve({id: 'old'})});
    assert.equal((await invoke()).status, 401); assert.equal(requests.length, 0);
    webAuthorized = true;
    assert.equal((await invoke()).status, 401); assert.equal(requests.length, 0);
    adminAuthorized = true;
    const result = await (await invoke()).json();
    assert.equal(result.approval.id, 'new'); assert.equal(requests.length, 1);
    assert.equal(requests[0].body.response.documentSha256, hash('B'));
    assert.equal(requests[0].target.approvalId, 'old');
    assert.equal(requests[0].headers['x-v8-agent-os-user-email'], 'test@example.invalid');
});

test('standalone Spec page approves the displayed version, uses CAS for edits, and keeps drafts on semantic failure', async () => {
    const file = 'apps/v8-agent-os-web/src/app/specs/SpecApprovalClient.tsx';
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let callback;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'postStageAction') callback = node.initializer;
        ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(callback);
    const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && ['readJson', 'normalizeError'].includes(node.name?.text));
    const compiled = ts.transpileModule(functions.map(node => node.getText(ast)).join('\n') + `\nexports.run = ${callback.getText(ast)};`,
        {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const writes = []; const cleared = []; const errors = []; let fail = false;
    const bindings = { ...helper, selectedSpecId: 'spec-A', selectedStage: 'requirements', workspacePath: 'E:/fixture',
        currentStage: document('shown document'), actionIdentity: 'A', actionIdentityRef: {current: 'A'}, stageActionPendingRef: {current: false},
        comment: 'keep comment', replacement: 'keep draft', sectionRef: '',
        fetch: async (url, init) => { writes.push({url, body: JSON.parse(init.body)}); return Response.json(fail ? {ok: false, error: 'locked'} : {ok: true}); },
        setBusy() {}, setError: error => errors.push(error), setComment: value => cleared.push(value), setReplacement: value => cleared.push(value),
        loadSpecDetail: async () => {}, loadSpecs: async () => {}, t: key => key,
    };
    const exports = {};
    new Function('exports', ...Object.keys(bindings), compiled)(exports, ...Object.values(bindings));
    await exports.run('approve');
    assert.equal(writes[0].body.documentSha256, hash('shown document'));
    cleared.length = 0; fail = true;
    await exports.run('edit');
    assert.equal(writes[1].body.expectedDocumentSha256, hash('shown document'));
    assert.equal(cleared.length, 0);
    assert.equal(errors.at(-1), 'locked');
});

test('ChatClient retires both immutable IDs after replacement approval and never closes another session', async () => {
    const file = 'apps/v8-agent-os-web/src/app/chat/ChatClient.tsx';
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let callback;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'handleGovernanceApprovalResolve') callback = node.initializer.arguments[0];
        ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(callback);
    const compiled = ts.transpileModule(`exports.run = ${callback.getText(ast)};`, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const active = {current: 'session-A'}; const pending = {current: 'old'};
    const removed = []; const closed = []; let finish;
    const bindings = { governancePendingApprovalId: 'old', governancePendingApprovalIdRef: pending, activeConversationIdRef: active,
        resolveApproval: () => new Promise(resolve => { finish = resolve; }), setGovernanceApprovalBusy() {},
        setDismissedGovernanceApprovalId() {}, removeGovernanceApproval: id => removed.push(id), setGovernanceApprovalOpen: value => closed.push(value),
        readString: value => typeof value === 'string' ? value : '',
    };
    const exports = {};
    new Function('exports', ...Object.keys(bindings), compiled)(exports, ...Object.values(bindings));
    const approved = exports.run('', true, {approvalId: 'old', documentSha256: hash('C'), replaceSpecReview: true});
    pending.current = 'new';
    finish({approval: {id: 'new', status: 'approved'}, replacesApprovalId: 'old'}); await approved;
    assert.deepEqual(removed, ['old', 'new']); assert.deepEqual(closed, [false]);
    removed.length = 0; closed.length = 0; pending.current = 'old';
    const late = exports.run('', true, {approvalId: 'old', documentSha256: hash('C')});
    active.current = 'session-B'; pending.current = 'B-card';
    finish({approval: {id: 'old', status: 'approved'}}); await late;
    assert.deepEqual(removed, []); assert.deepEqual(closed, []);
});
