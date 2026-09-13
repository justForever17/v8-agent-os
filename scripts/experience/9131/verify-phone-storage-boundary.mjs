// Frozen production modules with controlled storage failures and real in-memory SQL.
// No application configuration, tokens, user database, emulator state or network.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';

const repo=path.resolve(import.meta.dirname,'../../..');
const args=process.argv.slice(2);
const candidate=args[args.indexOf('--candidate')+1];
assert(args.includes('--candidate') && /^[0-9a-f]{8,40}$/.test(candidate));
const only=args.includes('--only')?args[args.indexOf('--only')+1].split(','):[];
const require=createRequire(path.join(repo,'apps/v8-agent-os-admin/package.json'));
const ts=require('typescript');
const plain=value=>JSON.parse(JSON.stringify(value));
const sources=new Map();
const sourceFor=name=>{
  const filename=`apps/v8-agent-os-phone/src/${name}.ts`;
  if(!sources.has(filename)) sources.set(filename,execFileSync('git',['-C',repo,'show',`${candidate}:${filename}`],{encoding:'utf8'}));
  return [filename,sources.get(filename)];
};

function environment(){
  const secure=new Map(),metadata=new Map(),modules=new Map();
  const failures={secureWrite:false,metadataKey:null};
  const sql=new DatabaseSync(':memory:');
  const db={
    execAsync:async query=>sql.exec(query),
    runAsync:async (query,params=[])=>sql.prepare(query).run(...params),
    getFirstAsync:async (query,params=[])=>sql.prepare(query).get(...params)??null,
    getAllAsync:async (query,params=[])=>sql.prepare(query).all(...params),
    withTransactionAsync:async operation=>{sql.exec('BEGIN');try{await operation();sql.exec('COMMIT');}catch(error){sql.exec('ROLLBACK');throw error;}},
    prepareAsync:async query=>({executeAsync:async params=>sql.prepare(query).run(...params),finalizeAsync:async()=>{}}),
  };
  const storage={getItem:async key=>metadata.get(key)??null,
    setItem:async(key,value)=>{if(failures.metadataKey===key)throw Error('synthetic-storage-fault');metadata.set(key,value);},
    removeItem:async key=>metadata.delete(key)};
  const secureStore={getItemAsync:async key=>secure.get(key)??null,
    setItemAsync:async(key,value)=>{if(failures.secureWrite)throw Error('synthetic-sensitive-canary');secure.set(key,value);},
    deleteItemAsync:async key=>secure.delete(key)};
  function load(name){
    if(modules.has(name))return modules.get(name);
    const [filename,source]=sourceFor(name);
    const module={exports:{}};modules.set(name,module.exports);
    const imports=specifier=>{
      if(specifier==='react-native')return {Platform:{OS:'android'}};
      if(specifier==='expo-secure-store')return secureStore;
      if(specifier==='expo-sqlite/kv-store')return {default:storage};
      if(specifier==='expo-sqlite')return {openDatabaseAsync:async filename=>{assert.equal(filename,'v8_phone_cache_v2.db');return db;}};
      if(specifier==='@/src/lib/admin-client')return {normalizeAdminBaseUrl:value=>String(value||'').replace(/\/$/,'')};
      if(specifier.startsWith('@/src/'))return load(specifier.slice(6));
      throw Error(`Unreviewed import: ${specifier}`);
    };
    const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
    vm.runInNewContext(code,{module,exports:module.exports,require:imports,URL,crypto:globalThis.crypto,setTimeout,clearTimeout},{filename});
    return module.exports;
  }
  return {secure,metadata,failures,sql,load};
}

const rows=[];
async function check(id,fn){if(only.length&&!only.some(prefix=>id.startsWith(prefix)))return;const env=environment();try{rows.push({id,status:'PASS',evidence:await fn(env)});}catch(error){rows.push({id,status:error.code==='ERR_ASSERTION'?'FAIL':'HARNESS_ERROR',error:String(error)});}finally{env.sql.close();}}
const directoryKey='v8.phone.profiles.v2';
const activeKey='v8.phone.activeAdminConnectionProfileId';
const profile=id=>({id,label:`Synthetic ${id}`,instanceId:`instance-${id}`,principalId:'principal-same',
  adminBaseUrl:`http://fixture.invalid/${id}`,credentialRef:`synthetic-ref-${id}`,lastUsedAt:'2026-09-13T00:00:00Z'});
function seed(env){env.metadata.set(directoryKey,JSON.stringify([profile('A')]));env.metadata.set(activeKey,'A');env.secure.set('synthetic-ref-A','public-fixture-value');}

await check('S01-native-credential-write-reject-is-visible',async env=>{
  const storage=env.load('lib/mobile-storage');env.failures.secureWrite=true;
  await assert.rejects(()=>storage.writeSecureItem('synthetic-ref-B','public-fixture-value'),error=>
    error.message.includes('Credentials were not saved')&&!error.message.includes('synthetic-sensitive-canary'));
  assert.equal(env.secure.size,0);return {rejected:true,sanitized:true,persisted:false};
});
await check('S02-directory-write-failure-preserves-active-profile',async env=>{
  seed(env);const profiles=env.load('lib/admin-connection-profiles');
  const original=env.metadata.get(directoryKey);env.failures.metadataKey=directoryKey;
  await assert.rejects(()=>profiles.updateAdminConnectionProfiles(current=>[...current,
    {...profile('B'),accessToken:'public-synthetic-B',refreshToken:'public-synthetic-refresh-B'}]));
  assert.equal(env.metadata.get(directoryKey),original);assert.equal(env.metadata.get(activeKey),'A');
  assert.deepEqual([...env.secure.keys()],['synthetic-ref-A']);
  env.failures.metadataKey=null;
  await profiles.updateAdminConnectionProfiles(current=>[...current,{...profile('B'),accessToken:'public-synthetic-B',refreshToken:'public-synthetic-refresh-B'}]);
  assert.deepEqual(JSON.parse(env.metadata.get(directoryKey)).map(p=>p.id),['A','B']);
  return {oldDirectoryPreserved:true,oldCredentialRetained:true,newSlotCleanedOnFailure:true,retryAddsB:true};
});
await check('S03-concurrent-directory-and-stale-activation',async env=>{
  seed(env);const profiles=env.load('lib/admin-connection-profiles');
  await Promise.all([profiles.updateAdminConnectionProfiles(current=>[...current,profile('B')]),profiles.updateAdminConnectionProfiles(current=>[...current,profile('C')])]);
  assert.deepEqual(JSON.parse(env.metadata.get(directoryKey)).map(p=>p.id).sort(),['A','B','C']);
  let published=0;
  await assert.rejects(()=>profiles.commitActiveAdminConnectionProfile('B','wrong-ref',()=>published++));
  env.failures.metadataKey=activeKey;
  await assert.rejects(()=>profiles.commitActiveAdminConnectionProfile('B','synthetic-ref-B',()=>published++));
  assert.equal(published,0);assert.equal(env.metadata.get(activeKey),'A');
  return {concurrentAddsRetained:3,staleCredentialRejected:true,pointerWriteFailureNotPublished:true};
});
await check('S04-draft-failure-retry-and-stale-submit-clear',async env=>{
  const {PhoneDraftStore}=env.load('lib/phone-drafts');const saved=new Map();let fail=true;
  const store=new PhoneDraftStore({read:async key=>saved.get(key)??null,write:async(key,value)=>{if(fail)throw Error('synthetic-disk-full');saved.set(key,value);}});
  const key='synthetic-complete-session';await store.hydrate(key);
  for(const [field,value] of Object.entries({input:'UNSENT A1',files:[{id:'file-A',uri:'file:///synthetic'}],plugins:['plugin-A'],selection:{start:1,end:4}}))store.set(key,field,value);
  await assert.rejects(()=>store.flush(key));
  assert.equal(store.get(key).values.input,'UNSENT A1');assert.match(store.get(key).error,/not saved/);
  const submittedRevision=store.get(key).composerRevision;store.set(key,'input','NEWER A2');
  assert.equal(store.compareAndSet(key,submittedRevision,{input:'',files:[],plugins:[]}),false);
  fail=false;await store.flush(key);
  const reopened=new PhoneDraftStore({read:async key=>saved.get(key)??null,write:async()=>{throw Error('read-only reopen');}});
  await reopened.hydrate(key);
  assert.deepEqual(plain(reopened.get(key).values),{input:'NEWER A2',files:[{id:'file-A',uri:'file:///synthetic'}],plugins:['plugin-A'],selection:{start:1,end:4}});
  return {failedWriteVisible:true,unsentFieldsPreserved:true,staleClearRejected:true,reopenExact:true};
});
await check('S05-real-sql-identity-cursor-tombstone-isolation',async env=>{
  const identity=env.load('lib/phone-identity'),cache=env.load('services/LocalDatabaseService');
  const keys=['A','B','a'].map(id=>identity.phoneAuthorityKey({instanceId:id,principalId:'same-principal',profileId:'same-profile'}));
  assert.equal(new Set(keys).size,3);
  const handles=keys.map(key=>cache.createLocalDatabase(key,'same-serving'));
  const message=value=>({id:'message-1',content:value,ordinal:1,turnId:'turn-1',turnPosition:1,createdAt:'2026-09-13'});
  for(let i=0;i<handles.length;i++){await handles[i].upsertMessages('session-1',[message(['A','B','a'][i])]);await handles[i].setSyncCursor('session-1',`cursor-${i}`);}
  await handles[0].deleteMessages('session-1',['message-1']);
  await handles[0].upsertMessages('session-1',[message('A late resurrection')]);
  await handles[1].upsertMessages('session-1',[message('B late update')]);
  assert.deepEqual(plain(await handles[0].getMessages('session-1')),[]);
  assert.equal((await handles[1].getMessages('session-1'))[0].content,'B late update');
  assert.equal((await handles[2].getMessages('session-1'))[0].content,'a');
  for(let i=0;i<handles.length;i++)assert.equal(await handles[i].getSyncCursor('session-1'),`cursor-${i}`);
  await handles[0].deleteSessionData('session-1');
  assert.equal(await handles[0].getSyncCursor('session-1'),'');assert.equal(await handles[1].getSyncCursor('session-1'),'cursor-1');
  return {sameSessionAndMessageIds:true,caseRetained:true,tombstonePreventsOnlyAResurrection:true,cursorsIndependent:true,deleteALeavesB:true};
});
await check('S06-submitting-restart-preserves-intent-identity',async env=>{
  const {PhoneDraftStore}=env.load('lib/phone-drafts');
  const intent={state:'submitting',clientMessageId:'synthetic-stable-message-id',fingerprint:'synthetic-exact-fingerprint',sessionKey:'synthetic-A-session-1',composerRevision:5,future:{zero:0,flag:false}};
  const saved=JSON.stringify({revision:9,composerRevision:5,values:{input:'PREVIOUS INPUT',pendingIntent:intent,plugins:['synthetic-plugin']}});
  let releaseRead,persisted;
  const deferred=new Promise(resolve=>{releaseRead=resolve;});
  const store=new PhoneDraftStore({read:async()=>deferred,write:async(_key,value)=>{persisted=value;}});
  const loading=store.hydrate('synthetic-A-session-1');
  store.set('synthetic-A-session-1','input','NEW INPUT WHILE HYDRATING');releaseRead(saved);await loading;
  const restored=plain(store.get('synthetic-A-session-1').values);
  assert.equal(restored.input,'NEW INPUT WHILE HYDRATING');
  assert.deepEqual(restored.pendingIntent,{...intent,state:'acceptance_unknown'});
  assert.deepEqual(restored.plugins,['synthetic-plugin']);
  await store.flush('synthetic-A-session-1');
  assert.deepEqual(JSON.parse(persisted).values.pendingIntent,{...intent,state:'acceptance_unknown'});
  return {unknownAcceptanceVisible:true,clientMessageIdPreserved:true,fingerprintAndSessionPreserved:true,newInputPreserved:true,recoveredStatePersisted:true,networkSubmission:false};
});

const report={candidate,level:'BOUNDARY_EXECUTED',node:process.version,
  qualification:'Frozen actual production modules. Native storage APIs are controlled fakes; SQL runs in real SQLite :memory:. No Android Keystore/physical OS fault injection.',
  sourceFiles:[...sources.keys()],rows};
const suffix=only.length?'-'+only.join('-'):'';
fs.writeFileSync(path.join(repo,`scripts/experience/9131/reports/phone-storage-${candidate.slice(0,8)}${suffix}.json`),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(rows));process.exitCode=rows.some(row=>row.status!=='PASS')?1:0;
