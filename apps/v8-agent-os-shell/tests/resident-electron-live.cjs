// Actual Electron + production resident registry/preload; synthetic HTTP surfaces.
const { app, BrowserWindow, WebContentsView } = require('electron');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createResidentSurfaces } = require('../lib/resident-surfaces.cjs');
if (!process.argv.includes('--live')) throw new Error('Pass --live to launch isolated Electron');
app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'v8-shell-resident-')));
app.whenReady().then(async () => {
  const servers = [];
  const serve = () => new Promise(resolve => {
    const server=http.createServer((_req,res)=>{res.setHeader('Content-Type','text/html;charset=utf-8');res.end('<textarea id="draft"></textarea><div id="scroll" style="height:100px;overflow:auto"><div style="height:1000px">fixture</div></div><script>window.documentIdentity=crypto.randomUUID();window.visibleEvents=[];window.v8osShell.onSurfaceVisibilityChange(s=>visibleEvents.push(s.visible));</script>');});
    server.listen(0,'127.0.0.1',()=>{servers.push(server);resolve(`http://127.0.0.1:${server.address().port}`);});
  });
  const webOrigin=await serve(),adminOrigin=await serve();
  const preferences={preload:path.resolve(__dirname,'../electron/preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true};
  const win=new BrowserWindow({show:false,width:900,height:600,webPreferences:preferences});
  let admin;
  const registry=createResidentSurfaces({baseContents:win.webContents,createAdminView(){admin=new WebContentsView({webPreferences:preferences});return admin;},attachView:v=>win.contentView.addChildView(v),getBounds:()=>({x:0,y:0,width:900,height:600}),webOrigin:()=>webOrigin,adminOrigin:()=>adminOrigin,observe(){}});
  try {
    await registry.open(`${webOrigin}/chat?id=fixture-a`);
    const identity=await win.webContents.executeJavaScript('documentIdentity');
    await win.webContents.executeJavaScript('draft.value="fixture draft"; draft.focus();draft.setSelectionRange(2,5);scroll.scrollTop=320;');
    await registry.open(`${adminOrigin}/admin/models`);
    const adminIdentity=await admin.webContents.executeJavaScript('documentIdentity');
    const samples=[];
    for(let index=0;index<30;index++){
      const start=performance.now();
      await registry.open(`${webOrigin}/chat`,{resume:true});
      const state=await win.webContents.executeJavaScript('({id:documentIdentity,text:draft.value,start:draft.selectionStart,end:draft.selectionEnd,top:scroll.scrollTop})');
      samples.push(performance.now()-start);
      assert.deepEqual(state,{id:identity,text:'fixture draft',start:2,end:5,top:320});
      await registry.open(`${adminOrigin}/admin`,{resume:true});
      assert.equal(await admin.webContents.executeJavaScript('documentIdentity'),adminIdentity);
    }
    registry.visibility(false);
    await new Promise(resolve=>setTimeout(resolve,50));
    assert.equal(await admin.webContents.executeJavaScript('visibleEvents.at(-1)'),false);
    assert.equal(registry.owns(admin.webContents,`${webOrigin}/chat`),false);
    const sorted=samples.sort((a,b)=>a-b);
    const result={level:'ACTUAL_ELECTRON_SYNTHETIC_HTTP',electron:process.versions.electron,n:30,medianMs:(sorted[14]+sorted[15])/2,p95Ms:sorted[28],documentsPreserved:true,selectionAndScroll:true};
    const adminContents=admin.webContents;
    const destroyed=new Promise(resolve=>adminContents.once('destroyed',resolve));
    registry.dispose(); await destroyed; assert.equal(adminContents.isDestroyed(),true);
    const out=path.resolve(__dirname,'../../../tmp/resident-web-evidence/shell-result.json');fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } catch(error) { console.error(error); process.exitCode=1; }
  finally {registry.dispose();win.destroy();servers.forEach(s=>s.close());app.exit(process.exitCode||0);}
});
