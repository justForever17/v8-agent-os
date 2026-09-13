const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
function load(name) {
    const exports = {};
    const source = fs.readFileSync(path.join(__dirname, '../src/lib', name + '.ts'), 'utf8');
    vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
        { exports, require, crypto: require('node:crypto').webcrypto, setTimeout, clearTimeout, Date, indexedDB: { open() { throw new Error('fixture quota denied'); } } });
    return exports;
}
test('complete drafts isolate instances, principals, workspaces and sessions', async () => {
    const d = load('composer-drafts');
    const a = d.draftOwnerKey('instance-a', 'owner-a', 'workspace-a', 'session-a');
    const alternatives = [['instance-b','owner-a','workspace-a','session-a'], ['instance-a','owner-b','workspace-a','session-a'], ['instance-a','owner-a','workspace-b','session-a'], ['instance-a','owner-a','workspace-a','session-b']].map((parts) => d.draftOwnerKey(...parts));
    for (const [field,value] of Object.entries({ text:'A draft', skills:[{name:'skill-a'}], plugins:[{pluginId:'plugin-a'}], sources:[{sourceId:'source-a'}], selection:{start:2,end:4} })) d.setDraftField(a,field,value,null);
    for (const key of alternatives) assert.equal(Object.keys(d.readDraft(key).values).length,0);
    d.setDraftField(alternatives[3],'text','B draft','');
    assert.equal(d.readDraft(a).values.text,'A draft');
    assert.equal(d.readDraft(a).values.sources[0].sourceId,'source-a');
    await d.flushAllDrafts();
});
test('a delayed submission acknowledgement cannot erase revision two or another session', async () => {
    const d = load('composer-drafts'); const a='A',b='B';
    d.setDraftField(a,'text','first','');
    const first = d.beginDraftSubmission(a);
    assert.equal(d.beginDraftSubmission(a).clientMessageId,first.clientMessageId,'unknown ack retry keeps its original id');
    d.setDraftField(a,'text','second',''); d.setDraftField(b,'text','other session','');
    assert.equal(d.acknowledgeDraft(a,first.revision),false);
    assert.equal(d.readDraft(a).values.text,'second'); assert.equal(d.readDraft(b).values.text,'other session');
    const second = d.beginDraftSubmission(a);
    assert.notEqual(second.clientMessageId,first.clientMessageId);
    d.setDraftField(a,'selection',{start:2,end:2},null);
    assert.equal(d.acknowledgeDraft(a,second.revision),true,'selection alone does not change submitted content');
    assert.equal(d.readDraft(a).values.text,undefined);
    await d.flushAllDrafts();
});
test('storage denied keeps editable memory draft with a visible failure signal', async () => {
    const d = load('composer-drafts');
    d.setDraftField('A','text','keep this',''); await d.flushDraft('A');
    assert.equal(d.readDraft('A').error,true); assert.equal(d.readDraft('A').values.text,'keep this');
    d.setDraftField('A','text','can continue',''); await d.flushDraft('A');
    assert.equal(d.readDraft('A').values.text,'can continue');
});
test('persisted attachments retain source identity and mark local files for reselection', async () => {
    const d = load('composer-drafts');
    d.setDraftField('A','files',[{name:'image.png',type:'image/png',size:123}],[]);
    d.setDraftField('A','sources',[{sourceId:'source-a',resourceRef:{resourceId:'r-a'}}],[]);
    const saved=d.persistentDraft(d.readDraft('A'));
    assert.equal(saved.values.files[0].reselect,true); assert.equal(saved.values.sources[0].sourceId,'source-a');
    await d.flushAllDrafts();
});
test('queue omission, stale empty snapshots and partial windows cannot erase known pending items', () => {
    const {reconcileQueueSnapshot: apply}=load('queue-snapshot');
    const current=Array.from({length:25},(_,i)=>({id:String(i)}));
    assert.equal(apply(current,null,21,20,true),current);
    assert.equal(apply(current,[],1,20,true),current);
    assert.equal(apply(current,current.slice(0,20),21,20,false).length,25);
    assert.equal(apply(current,[],21,20,true).length,0);
});
test('queue pagination restarts a changed revision and never resurrects consumed items', async () => {
    const {readCompleteQueue}=load('queue-snapshot'); let reads=0;
    const result=await readCompleteQueue('A',async(cursor)=>{
        reads++;
        const changed=reads>1;
        return {sessionId:'A',latestSeq:changed?21:20,queuedMessages:cursor===null?(changed?[{id:'kept'}]:[{id:'consumed'}]):[{id:'tail'}],queuedMessagesWindow:{hasMore:cursor===null,nextOrdinal:cursor===null?20:null}};
    },()=>true);
    assert.equal(result.sequence,21); assert.deepEqual(Array.from(result.items,item=>item.id),['kept','tail']);
    assert.equal(reads,4);
    await assert.rejects(readCompleteQueue('A',async()=>({sessionId:'B',latestSeq:21,queuedMessages:[],queuedMessagesWindow:{hasMore:false,nextOrdinal:null}}),()=>true));
});
