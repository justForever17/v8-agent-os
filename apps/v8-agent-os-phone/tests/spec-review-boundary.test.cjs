const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function compile(file, mocks = {}) {
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  } }).outputText;
  const record = { exports: {} };
  new Function('require', 'module', 'exports', output)(name => Object.hasOwn(mocks, name) ? mocks[name] : require(name), record, record.exports);
  return record.exports;
}

function phoneApi() {
  const locale = { translateCurrent: key => key };
  return compile(path.join(root, 'src/lib/phone-api.ts'), {
    '@/src/lib/admin-client': compile(path.join(root, 'src/lib/admin-client.ts'), { '@/src/lib/locale': locale }),
    '@/src/lib/session-history': {}, '@/src/lib/locale': locale, '@/src/lib/admin-connection-profiles': {},
  });
}

test('Spec API preserves exact review hash and approval ID; conflicts retain structured codes without replay', async () => {
  const api = phoneApi(), requests = [];
  const fetcher = async (url, init) => {
    requests.push({ url, body: JSON.parse(init.body) });
    return Response.json({ detail: { code: 'spec_approval_document_changed', summary: 'Changed document' } }, { status: 409 });
  };
  await assert.rejects(api.refreshSpecApprovalReview(fetcher, 'old/id', 'raw-byte-hash'), error => error.status === 409 && error.code === 'spec_approval_document_changed');
  assert.deepEqual(requests, [{ url: '/api/client/approvals/old%2Fid/refresh-spec-review', body: { response: { documentSha256: 'raw-byte-hash' } } }]);
  const ok = async (url, init) => { requests.push({ url, body: JSON.parse(init.body) }); return Response.json({ ok: true }); };
  await api.approvePendingItem(ok, 'new/id', 'unchanged comment', true, 'new-hash');
  assert.deepEqual(requests[1], { url: '/api/client/approvals/new%2Fid/approve', body: { response: { answer: 'unchanged comment', approved: true, documentSha256: 'new-hash' } } });
  await api.approveSpecStage(ok, 'spec', 'requirements', 'workspace', 'comment', 'shown-hash');
  assert.equal(requests[2].body.documentSha256, 'shown-hash');
  await api.editSpecStage(ok, 'spec', 'requirements', 'workspace', 'REQ-1', 'new text', 'reason', 'shown-hash');
  assert.equal(requests[3].body.expectedDocumentSha256, 'shown-hash');
  assert.equal(requests.length, 4);
});

test('Mobile refresh BFF authenticates before body/Engine and preserves immutable replacement or 409', async () => {
  const oldFetch = global.fetch;
  let authenticated = false;
  const calls = [];
  class NextResponse extends Response {
    static json(value, init) { return new NextResponse(JSON.stringify(value), init); }
  }
  const route = compile(path.resolve(root, '../v8-agent-os-admin/src/app/api/client/approvals/[id]/refresh-spec-review/route.ts'), {
    'next/server': { NextResponse },
    '@/lib/server/client-request-auth': {
      resolveClientUserEmail: async () => authenticated ? 'fixture@invalid' : null,
      unauthorizedClientJson: () => NextResponse.json({ code: 'auth_pre_execution' }, { status: 401, headers: { 'X-V8-Auth-Stage': 'pre_execution' } }),
    },
    '@/lib/server/runtime-config': { resolveEngineBaseUrl: () => 'http://engine.invalid', resolveInternalSecret: () => 'synthetic-internal' },
    '@/lib/server/engine-fetch': { engineFetch: (url, init) => global.fetch(url, init) },
  });
  try {
    global.fetch = async (url, init) => { calls.push({ url, init }); return Response.json({ detail: { code: 'spec_approval_document_changed' } }, { status: 409 }); };
    const denied = await route.POST({ json: () => assert.fail('must not read unauthenticated action body') }, { params: Promise.resolve({ id: 'old/id' }) });
    assert.equal(denied.status, 401); assert.equal(calls.length, 0);
    authenticated = true;
    const body = { response: { documentSha256: 'shown-hash' } };
    const request = () => new Request('http://phone.invalid/api/client/approvals/old/refresh-spec-review', { method: 'POST', body: JSON.stringify(body) });
    const conflict = await route.POST(request(), { params: Promise.resolve({ id: 'old/id' }) });
    assert.equal(conflict.status, 409);
    assert.equal((await conflict.json()).detail.code, 'spec_approval_document_changed');
    assert.equal(conflict.headers.get('x-v8-auth-stage'), null);
    assert.equal(calls[0].url, 'http://engine.invalid/approvals/old%2Fid/refresh-spec-review');
    assert.equal(calls[0].init.headers['x-v8-agent-os-user-email'], 'fixture@invalid');
    assert.equal(calls[0].init.headers['x-v8-agent-os-secret'], 'synthetic-internal');
    assert.deepEqual(JSON.parse(calls[0].init.body), body);
    const replacement = { approval: { id: 'new', status: 'pending', request: { documentSha256: 'shown-hash' } }, replacesApprovalId: 'old' };
    global.fetch = async () => Response.json(replacement);
    assert.deepEqual(await (await route.POST(request(), { params: Promise.resolve({ id: 'old' }) })).json(), replacement);
  } finally { global.fetch = oldFetch; }
});

test('Phone Spec review links retain the old approval identity without embedding comment drafts', () => {
  const { isSpecStageApproval, specApprovalReviewHref, specReviewDraftKey } = compile(path.join(root, 'src/lib/spec-approval-review.ts'));
  const approval = { approval_id: 'old/id', approval_kind: 'spec_stage_approval', request: { specId: 'spec', stage: 'requirements', workspacePath: 'E:/space name' } };
  assert.ok(isSpecStageApproval(approval));
  const url = new URL(specApprovalReviewHref(approval), 'http://fixture.invalid');
  assert.equal(url.searchParams.get('approvalId'), 'old/id');
  assert.equal(url.searchParams.get('workspace'), 'E:/space name');
  assert.notEqual(specReviewDraftKey('A', 'old/id'), specReviewDraftKey('B', 'old/id'));
});

test('Phone and existing mobile detail BFF request and forward full Spec content', async () => {
  const api = phoneApi();
  let requested;
  await api.getSpecDetail(async url => { requested = url; return Response.json({ stages: {} }); }, 'spec/id', 'workspace with spaces');
  assert.equal(new URL(requested, 'http://fixture.invalid').searchParams.get('full_content'), 'true');
  const oldFetch = global.fetch;
  class NextResponse extends Response { static json(data, init) { return new NextResponse(JSON.stringify(data), init); } }
  const route = compile(path.resolve(root, '../v8-agent-os-admin/src/app/api/client/specs/[id]/route.ts'), {
    'next/server': { NextResponse },
    '@/lib/server/client-request-auth': { resolveClientUserEmail: async () => 'fixture@invalid' },
    '@/lib/server/runtime-config': { resolveEngineBaseUrl: () => 'http://engine.invalid' },
    '@/lib/server/engine-fetch': { engineFetch: (url, init) => global.fetch(url, init) },
  });
  try {
    let forwarded;
    global.fetch = async url => { forwarded = new URL(url); return Response.json({ stages: { requirements: { content: '\ufeffraw\r\n', documentSha256: 'same-read', truncated: false } } }); };
    const response = await route.GET({ nextUrl: new URL(requested, 'http://fixture.invalid') }, { params: Promise.resolve({ id: 'spec/id' }) });
    assert.equal(forwarded.searchParams.get('full_content'), 'true');
    assert.equal((await response.json()).stages.requirements.content, '\ufeffraw\r\n');
  } finally { global.fetch = oldFetch; }
});
