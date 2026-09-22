// HISTORICAL: frozen 9.13.1 checkout/state reproduction, not a current release gate.
// Current single-host acceptance: apps/v8-agent-os-shell/tests/scripts/product_surface_native.mjs.
// Real production Electron + Web/Admin/Engine. No API routes are mocked.
// Uses only the explicitly handed-off state and API-created synthetic sessions.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {verifyResidentRoundTrips} from './shell-resident-scenario.mjs';

const own=path.resolve(import.meta.dirname,'../../..');
const args=process.argv.slice(2);
const option=(key,fallback)=>args.includes(key)?args[args.indexOf(key)+1]:fallback;
assert(args.includes('--live')&&args.includes('--allow-side-effects'),'Explicit real UI and synthetic state mutation flags required');
const repo='E:/Projects/v8chat/.codex-worktrees/9131-integration';
const state=path.resolve(option('--state-root',''));
assert.equal(state,path.resolve('E:/Projects/v8chat/.codex-tmp/release-20260913-1/preview-state-1'));
const candidate=option('--candidate','');assert.match(candidate,/^[0-9a-f]{40}$/);
const out=path.resolve(option('--out',''));assert.ok(out.startsWith(path.join(own,'tmp/experience-9131')+path.sep));
const manifest=path.resolve(option('--manifest',''));assert.ok(manifest.startsWith(path.join(own,'tmp/experience-9131')+path.sep));
const records=JSON.parse(fs.readFileSync(manifest,'utf8'));assert.deepEqual(Object.keys(records).sort(),['A','B']);
for(const record of Object.values(records)){assert.match(record.id,/^[0-9a-f-]{36}$/);assert.equal(record.userId,'shell-preview-fixture');}
const only=option('--only','queue,theme').split(',');
fs.mkdirSync(out,{recursive:true});
const env={...process.env,V8_REPO_ROOT:repo,V8_AGENT_OS_HOME:state,V8OS_DESKTOP_ISOLATED_USER_DATA_ROOT:path.join(state,'electron'),
  V8_ENGINE_PYTHON:'E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/.venv/Scripts/python.exe'};
delete env.ELECTRON_RUN_AS_NODE;
const cli=(command,extra=[],cliEnv=env)=>JSON.parse(execFileSync(process.execPath,[path.join(repo,'apps/v8-agent-os-cli/bin/v8os.mjs'),command,...extra,'--json'],{cwd:own,env:cliEnv,encoding:'utf8',timeout:60000}));
const git=(...args)=>execFileSync('git',['-C',repo,...args],{encoding:'utf8'}).trim();
assert.equal(git('rev-parse',candidate),candidate);
const require=createRequire(path.join(repo,'apps/v8-agent-os-admin/package.json'));
const {_electron}=require('playwright');
const evidence=[],errors=[],requests=[],documents=[];
const report={candidate,sourceHeadAtStart:git('rev-parse','HEAD'),stateRoot:state,
  buildIds:Object.fromEntries(['admin','web'].map(name=>[name,fs.readFileSync(path.join(repo,`apps/v8-agent-os-${name}/.next/BUILD_ID`),'utf8').trim()])),
  level:'REAL_ELECTRON_PRODUCTION_SERVICES',qualification:'Synthetic history/queue rows prepared with production Database owner. Existing queue visibility/editing is tested; no active run or model execution is created.',
  evidence,errors,requests,documents};
if(only.includes('cli')){
  const without={...env};delete without.V8_ENGINE_PYTHON;
  const statuses=cli('status',[],without);const engine=statuses.find(x=>x.id==='engine');
  assert.ok(engine.managed&&engine.pidAlive&&engine.portOpen&&engine.state==='managed_running');
  report.cliWithoutInterpreterOverride=engine;
}
report.beforeLaunchShellStop=cli('stop',['--only','shell']);
const app=await _electron.launch({executablePath:path.join(repo,'apps/v8-agent-os-desktop-pet/node_modules/electron/dist/electron.exe'),
  args:[path.join(repo,'apps/v8-agent-os-shell')],cwd:own,env,timeout:120000});
let admin,web;
const describeUrl=url=>url.startsWith('data:')?'startup-data-url':url.split('?')[0];
const observe=page=>{
  page.setDefaultTimeout(15000);
  page.on('pageerror',error=>errors.push({url:describeUrl(page.url()),error:String(error).slice(0,500)}));
  page.on('framenavigated',frame=>{if(frame===page.mainFrame())documents.push({at:Date.now(),url:describeUrl(frame.url())});});
  page.on('request',request=>{if(request.url().startsWith('http')){const url=new URL(request.url());requests.push({at:Date.now(),origin:url.origin,path:url.pathname,method:request.method(),type:request.resourceType()});}});
};
app.context().pages().forEach(observe);app.context().on('page',observe);
const waitPage=async(port)=>{
  const deadline=Date.now()+60000;
  while(Date.now()<deadline){const found=app.context().pages().find(p=>p.url().startsWith(`http://127.0.0.1:${port}/`));if(found)return found;await new Promise(r=>setTimeout(r,200));}
  throw Error(`Product surface ${port} never reached; actual pages: ${app.context().pages().map(p=>describeUrl(p.url()))}`);
};
const observeVisibility=page=>page.evaluate(()=>{
  window.__experienceDoc ||= crypto.randomUUID();
  window.v8osShell.onSurfaceVisibilityChange(state=>{window.__surfaceVisible=state.visible;});
});
async function switchTo(kind){
  const target=kind==='web'?web:admin;
  if(await target.evaluate(()=>window.__surfaceVisible===true))return;
  await (kind==='web'?admin:web).getByRole('button',{name:kind==='web'?'聊天':'控制台',exact:true}).click();
  await target.waitForFunction(()=>window.__surfaceVisible===true);
}
const choose=async label=>{await switchTo('web');await web.getByTitle('Independent Shell '+label,{exact:true}).click();await web.waitForURL('**/chat?id='+records[label].id);await web.locator('textarea').first().waitFor();};
const draft=()=>web.locator('textarea').first().evaluate(e=>({text:e.value,start:e.selectionStart,end:e.selectionEnd,
  scroll:document.querySelector('.v8-chat-viewport-surface').scrollTop,doc:window.__experienceDoc,timeOrigin:performance.timeOrigin}));
const queueApi=label=>web.evaluate(async id=>{const response=await fetch('/api/chat-queue?session_id='+id,{cache:'no-store'});return {status:response.status,payload:await response.json()};},records[label].id);
const readStoredDraft=(label='A')=>web.evaluate(async id=>{
  const databases=await indexedDB.databases();if(!databases.some(x=>x.name==='v8-composer-drafts-v1'))return [];
  const request=indexedDB.open('v8-composer-drafts-v1');
  const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
  const rows=await new Promise((resolve,reject)=>{const result=db.transaction('drafts','readonly').objectStore('drafts').getAll();result.onsuccess=()=>resolve(result.result);result.onerror=()=>reject(result.error);});
  db.close();return rows.filter(row=>{try{return JSON.parse(row.key)[3]===id;}catch{return false;}}).map(row=>({key:row.key,values:row.values,revision:row.revision,saved:row.saved,hydrated:row.hydrated}));
},records[label].id);
async function test(id,fn){
  if(!only.includes(id))return;
  const row={id};try{row.details=await fn();row.status='PASS';}catch(error){row.status='FAIL';row.error=String(error).slice(0,2000);}
  evidence.push(row);console.log(JSON.stringify({id,status:row.status,error:row.error}));
  fs.writeFileSync(path.join(out,'review.json'),JSON.stringify(report,null,2));
}
try{
  const startDeadline=Date.now()+60000;
  while(Date.now()<startDeadline){
    admin=app.context().pages().find(p=>p.url().startsWith('http://127.0.0.1:9528/'));
    web=app.context().pages().find(p=>p.url().startsWith('http://127.0.0.1:9527/'));
    if(admin||web)break;await new Promise(r=>setTimeout(r,200));
  }
  if(!admin&&web){await observeVisibility(web);await web.getByRole('button',{name:'控制台',exact:true}).click();admin=await waitPage(9528);}
  assert.ok(admin,'Real bootstrap must reach an authorized product surface');
  if(new URL(admin.url()).pathname==='/login'){
    await admin.locator('#login').fill('shell-preview-fixture');await admin.locator('#password').fill('public-shell-preview-fixture');
    if(await admin.locator('#name').count()){await admin.locator('#name').fill('桌面独立合成验收');await admin.locator('#confirmPassword').fill('public-shell-preview-fixture');}
    await admin.locator('button[type=submit]').click();await admin.waitForURL('**/admin',{timeout:60000});
  }
  await observeVisibility(admin);
  await admin.getByRole('button',{name:'聊天',exact:true}).click();web=await waitPage(9527);await observeVisibility(web);
  await web.getByTitle('Independent Shell A',{exact:true}).waitFor({timeout:30000});await choose('A');
  report.initialA=await draft();
  report.electron=await app.evaluate(({app,BrowserWindow})=>({version:process.versions.electron,chrome:process.versions.chrome,windows:BrowserWindow.getAllWindows().length,userData:app.getPath('userData'),pid:process.pid}));
  await test('queue',async()=>{
    const observations=[];
    report.queueProgress={observations};
    for(const label of ['A','B','A']){
      await choose(label);const api=await queueApi(label);
      assert.equal(api.status,200);assert.equal(api.payload.queuedMessages.length,label==='A'?2:1);
      for(const item of api.payload.queuedMessages)await web.getByText(item.content,{exact:true}).waitFor();
      const other=label==='A'?'B':'A';assert.equal(await web.getByText(`Independent ${other} pending queue 1`,{exact:true}).count(),0);
      observations.push({label,queueIds:api.payload.queuedMessages.map(x=>x.id),draft:await draft()});
    }
    const previous=await queueApi('A');const item=previous.payload.queuedMessages[0];
    const queueRow=web.getByText(item.content,{exact:true}).locator('..').locator('..');
    await queueRow.getByRole('button',{name:'编辑消息',exact:true}).click();
    await web.getByRole('menuitem',{name:'编辑消息',exact:true}).click();
    const editor=web.getByPlaceholder('修改这条排队消息',{exact:true});await editor.fill('Independent A queue edited through real UI');
    const dialog=editor.locator('..');
    await dialog.getByRole('button',{name:'保存',exact:true}).click();await editor.waitFor({state:'hidden'});
    const edited=await queueApi('A');const actual=edited.payload.queuedMessages.find(x=>x.id===item.id);
    assert.equal(actual.content,'Independent A queue edited through real UI');assert.equal(actual.state,'pending');
    const cancelled=edited.payload.queuedMessages.find(x=>x.id!==item.id);
    const cancelRow=web.getByText(cancelled.content,{exact:true}).locator('..').locator('..');
    await cancelRow.getByRole('button',{name:'关闭排队',exact:true}).click();
    await web.getByText(cancelled.content,{exact:true}).waitFor({state:'hidden'});
    const remaining=await queueApi('A');assert.deepEqual(remaining.payload.queuedMessages.map(x=>x.id),[item.id]);
    Object.assign(report.queueProgress,{editedId:item.id,cancelledId:cancelled.id,remaining:remaining.payload.queuedMessages.map(x=>({id:x.id,content:x.content,state:x.state}))});
    await web.reload({waitUntil:'domcontentloaded'});await observeVisibility(web);
    await web.getByText(actual.content,{exact:true}).waitFor();
    assert.equal((await draft()).text,'Shell_A_UNSENT_中文_Keep_exact_draft');
    await web.screenshot({path:path.join(out,'queue-after-real-edit-reload.png')});
    const storage=JSON.parse(execFileSync(env.V8_ENGINE_PYTHON,['-X','utf8','-c',
      'import sqlite3,json,sys; from pathlib import Path; p=Path(sys.argv[1]).resolve(); c=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True); c.row_factory=sqlite3.Row; rows=c.execute("SELECT id,session_id,content,state,client_message_id FROM chat_user_message_queue WHERE id IN (?,?)",sys.argv[2:]).fetchall(); print(json.dumps([dict(r) for r in rows])); c.close()',
      path.join(state,'state.db'),item.id,cancelled.id],{cwd:own,env,encoding:'utf8',timeout:15000}));
    assert.equal(storage.find(x=>x.id===item.id).content,actual.content);assert.equal(storage.find(x=>x.id===cancelled.id).state,'cancelled');
    return {observations,editedQueue:{id:actual.id,content:actual.content,state:actual.state},cancelledId:cancelled.id,storage,reloadPreserved:true,executed:false};
  });
  await test('draft',async()=>{
    await choose('A');const marker='Independent_durable_A_reload_9131';
    await web.locator('textarea').first().fill(marker);await web.waitForTimeout(700);
    const before=await readStoredDraft();assert.ok(before.some(row=>row.values.text===marker&&row.saved&&row.hydrated),'The marker must be durably stored before reload');
    await web.reload({waitUntil:'domcontentloaded'});await observeVisibility(web);
    await web.waitForFunction(()=>{const input=document.querySelector('textarea');return input&&!input.disabled;});
    await web.waitForTimeout(1500);
    const actual=await draft(),after=await readStoredDraft();
    report.draftPersistence={before,after,expected:marker,actual};
    await web.screenshot({path:path.join(out,'durable-draft-after-reload.png')});
    assert.equal(actual.text,marker,'An already persisted draft must survive reload without being replaced by an initial scroll record');
    assert.ok(after.some(row=>row.key===before.find(x=>x.values.text===marker).key&&row.values.text===marker));
    return report.draftPersistence;
  });
  await test('cold-draft',async()=>{
    await choose('A');await web.waitForFunction(()=>{const input=document.querySelector('textarea');return input&&!input.disabled;});await web.waitForTimeout(1200);
    const actual=await draft(),stored=await readStoredDraft();
    assert.equal(actual.text,'Independent_durable_A_reload_9131');
    assert.ok(stored.some(row=>row.values.text===actual.text));
    return {actual,stored,qualification:'A new governed Shell process restored the marker written and verified by the preceding draft run.'};
  });
  await test('draft-state',async()=>{
    await choose('A');const input=web.locator('textarea').first();
    await web.waitForFunction(()=>{const field=document.querySelector('textarea');return field&&!field.disabled;});
    assert.equal(await input.inputValue(),'Independent_durable_A_reload_9131');
    await input.press('Home');for(let i=0;i<3;i++)await input.press('ArrowRight');for(let i=0;i<6;i++)await input.press('Shift+ArrowRight');
    const viewport=web.locator('.v8-chat-viewport-surface');const box=await viewport.boundingBox();
    const scrolling=await viewport.evaluate(element=>({top:element.scrollTop,max:element.scrollHeight-element.clientHeight}));
    assert.ok(scrolling.max>500,'Long synthetic history must have a meaningful reading range');
    await web.mouse.move(box.x+box.width/2,box.y+box.height/2);await web.mouse.wheel(0,scrolling.max/2-scrolling.top);await web.waitForTimeout(700);
    const before=await draft(),diskA=await readStoredDraft();assert.equal(before.start,3);assert.equal(before.end,9);
    assert.ok(before.scroll>100&&before.scroll<scrolling.max-100,'Reading position must be away from both top and bottom');
    assert.ok(diskA.some(row=>row.values.text===before.text&&row.values.selection?.start===3&&row.values.selection?.end===9));
    await choose('B');await web.locator('textarea').first().fill('Independent_B_durable_6809641c');await web.waitForTimeout(700);
    const diskB=await readStoredDraft('B');assert.ok(diskB.some(row=>row.values.text==='Independent_B_durable_6809641c'&&row.saved));
    await choose('A');await web.waitForTimeout(300);const returned=await draft();
    assert.equal(returned.text,before.text);assert.equal(returned.start,3);assert.equal(returned.end,9);assert.ok(Math.abs(returned.scroll-before.scroll)<1);
    const adminBefore=await admin.evaluate(()=>({doc:window.__experienceDoc,timeOrigin:performance.timeOrigin}));
    await switchTo('admin');await switchTo('web');const surfaceReturn=await draft();
    assert.deepEqual(surfaceReturn,returned);assert.deepEqual(await admin.evaluate(()=>({doc:window.__experienceDoc,timeOrigin:performance.timeOrigin})),adminBefore);
    for(const label of ['A','B']){const api=await queueApi(label);assert.equal(api.status,200);assert.equal(api.payload.queuedMessages.length,1);}
    const result={before,returned,surfaceReturn,diskA,diskB,noQueueMutations:true,qualification:'Real keyboard selection and mouse wheel, A/B fresh markers, and one resident surface round trip. No source/file upload exercised.'};
    fs.writeFileSync(path.join(out,'draft-state-expected.json'),JSON.stringify(result,null,2));
    await web.screenshot({path:path.join(out,'draft-state-A-restored.png')});return result;
  });
  await test('cold-state',async()=>{
    const expectedPath=path.resolve(option('--expected-state',''));
    assert.ok(expectedPath.startsWith(path.join(own,'tmp/experience-9131')+path.sep));
    const expected=JSON.parse(fs.readFileSync(expectedPath,'utf8'));
    await choose('A');await web.waitForFunction(()=>{const field=document.querySelector('textarea');return field&&!field.disabled;});await web.waitForTimeout(1200);
    const actual=await draft(),diskA=await readStoredDraft();
    assert.equal(actual.text,expected.before.text);assert.equal(actual.start,expected.before.start);assert.equal(actual.end,expected.before.end);
    assert.ok(Math.abs(actual.scroll-expected.before.scroll)<1,`Cold scroll ${actual.scroll} versus durable ${expected.before.scroll}`);
    await choose('B');await web.waitForFunction(()=>{const field=document.querySelector('textarea');return field&&!field.disabled;});await web.waitForTimeout(700);
    const b=await draft();assert.equal(b.text,'Independent_B_durable_6809641c');
    await choose('A');await web.waitForTimeout(300);assert.equal((await draft()).text,expected.before.text);
    return {actual,diskA,B:b,qualification:'A separate governed Shell process restored both fresh markers plus A selection/scroll; old erased text is not recovered.'};
  });
  await test('resident',async()=>{
    await choose('A');const input=web.locator('textarea').first();await input.fill('Shell_A_UNSENT_中文_Keep_exact_draft');
    await input.press('Home');for(let i=0;i<3;i++)await input.press('ArrowRight');for(let i=0;i<6;i++)await input.press('Shift+ArrowRight');
    const box=await web.locator('.v8-chat-viewport-surface').boundingBox();await web.mouse.move(box.x+box.width/2,box.y+box.height/2);await web.mouse.wheel(0,-3200);await web.waitForTimeout(350);
    const baseline=await draft();assert.ok(baseline.scroll>0);
    const adminBaseline=await admin.evaluate(()=>({doc:window.__experienceDoc,timeOrigin:performance.timeOrigin}));
    return verifyResidentRoundTrips({web,admin,app,out,baseline,adminBaseline});
  });
  await test('theme',async()=>{
    const variants=[];
    report.themeVariants=variants;
    await app.evaluate(({BrowserWindow})=>{const window=BrowserWindow.getAllWindows()[0];window.show();window.focus();});
    for(const [sourceKind,desired] of [['web','dark'],['admin','light'],['web','dark'],['admin','light']]){
      const source=sourceKind==='web'?web:admin,target=sourceKind==='web'?admin:web,targetKind=sourceKind==='web'?'admin':'web';
      await switchTo(sourceKind);
      if(!(await source.locator('html').getAttribute('class')).split(/\s+/).includes(desired)){
        const written=source.waitForResponse(response=>new URL(response.url()).pathname==='/api/ui-preferences/theme'&&response.request().method()==='PUT');
        await source.getByRole('button',{name:'切换明暗主题',exact:true}).click();assert.equal((await written).status(),200);
      }
      await source.waitForFunction(theme=>document.documentElement.classList.contains(theme),desired);
      await source.screenshot({path:path.join(out,`${sourceKind}-${desired}.png`)});
      const requestStart=requests.length;
      await switchTo(targetKind);
      let synchronized=true;try{await target.waitForFunction(theme=>document.documentElement.classList.contains(theme),desired,{timeout:5000});}catch{synchronized=false;}
      const activationGets=requests.slice(requestStart).filter(x=>x.path==='/api/ui-preferences/theme'&&x.origin===`http://127.0.0.1:${targetKind==='web'?9527:9528}`&&x.method==='GET').length;
      const canonical=await target.evaluate(async()=>{const response=await fetch('/api/ui-preferences/theme');return {status:response.status,payload:await response.json()};});
      const actual=await target.evaluate(()=>({theme:document.documentElement.className,focused:document.hasFocus()}));
      await target.screenshot({path:path.join(out,`${targetKind}-${desired}.png`)});
      variants.push({sourceKind,targetKind,desired,synchronized,canonical,actual,activationGets});
      assert.equal(canonical.payload.theme,desired);assert.ok(synchronized,'Resident Admin must refresh the saved theme when its Shell surface becomes visible');
      assert.ok(activationGets>=1&&activationGets<=2,'Activation GETs must remain bounded');
    }
    await switchTo('web');await web.waitForTimeout(300);
    const start=requests.length;
    // Repeat the existing governed IPC; these are real Shell visibility events,
    // not a synthetic DOM visibility dispatch or mocked callback.
    for(let i=0;i<5;i++)await web.evaluate(()=>window.v8osShell.openWeb());
    await web.waitForTimeout(400);
    const repeatGets=requests.slice(start).filter(x=>x.path==='/api/ui-preferences/theme'&&x.method==='GET').length;
    assert.equal(repeatGets,0,'Repeated visible=true must not create a refresh storm');
    return {variants,repeatedRealShellActivation:5,repeatGets};
  });
}catch(error){report.bootstrapFailure=String(error).slice(0,1600);process.exitCode=1;}
finally{
  if(web&&!web.isClosed())await web.screenshot({path:path.join(out,'final-web.png')}).catch(()=>{});
  report.chatSubmissionRequests=requests.filter(r=>/^\/api\/(chat-submit|chat\/submit|chat)$/.test(r.path));
  report.summary={passed:evidence.filter(x=>x.status==='PASS').length,failed:evidence.filter(x=>x.status==='FAIL').length};
  report.governedShellStop=cli('stop',['--only','shell']);
  report.closed=true;fs.writeFileSync(path.join(out,'review.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({summary:report.summary,out,bootstrapFailure:report.bootstrapFailure}));
  assert.equal(report.chatSubmissionRequests.length,0,'No chat/model submission is permitted in this harness');
  if(report.summary.failed)process.exitCode=1;
}
