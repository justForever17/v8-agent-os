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
async function test(id,fn){
  if(!only.includes(id))return;
  const row={id};try{row.details=await fn();row.status='PASS';}catch(error){row.status='FAIL';row.error=String(error).slice(0,2000);}
  evidence.push(row);console.log(JSON.stringify({id,status:row.status,error:row.error}));
  fs.writeFileSync(path.join(out,'review.json'),JSON.stringify(report,null,2));
}
try{
  admin=await waitPage(9528);
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
    const dialog=web.locator('[role=dialog]').filter({has:editor});
    await dialog.getByRole('button',{name:'保存',exact:true}).click();await editor.waitFor({state:'hidden'});
    const edited=await queueApi('A');const actual=edited.payload.queuedMessages.find(x=>x.id===item.id);
    assert.equal(actual.content,'Independent A queue edited through real UI');assert.equal(actual.state,'pending');
    await web.reload({waitUntil:'domcontentloaded'});await observeVisibility(web);
    await web.getByText(actual.content,{exact:true}).waitFor();
    assert.equal((await draft()).text,'Shell_A_UNSENT_中文_Keep_exact_draft');
    await web.screenshot({path:path.join(out,'queue-after-real-edit-reload.png')});
    return {observations,editedQueue:{id:actual.id,content:actual.content,state:actual.state},reloadPreserved:true,executed:false};
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
    for(const desired of ['dark','light']){
      await switchTo('web');
      if(!(await web.locator('html').getAttribute('class')).split(/\s+/).includes(desired))await web.getByRole('button',{name:'切换明暗主题',exact:true}).click();
      await web.waitForFunction(theme=>document.documentElement.classList.contains(theme),desired);
      await web.screenshot({path:path.join(out,`web-${desired}.png`)});
      await switchTo('admin');
      let synchronized=true;try{await admin.waitForFunction(theme=>document.documentElement.classList.contains(theme),desired,{timeout:5000});}catch{synchronized=false;}
      const canonical=await admin.evaluate(async()=>{const response=await fetch('/api/ui-preferences/theme');return {status:response.status,payload:await response.json()};});
      const actual=await admin.evaluate(()=>({theme:document.documentElement.className,focused:document.hasFocus()}));
      await admin.screenshot({path:path.join(out,`admin-${desired}.png`)});
      variants.push({desired,synchronized,canonical,actual});
      assert.equal(canonical.payload.theme,desired);assert.ok(synchronized,'Resident Admin must refresh the saved theme when its Shell surface becomes visible');
    }
    await switchTo('web');return {variants};
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
