const test = require('node:test');
const assert = require('node:assert/strict');
const { createResidentSurfaces } = require('../lib/resident-surfaces.cjs');
function fixture() {
  const makeContents = () => ({ url:'', loads:[], events:[], closed:false, getURL(){return this.url;}, isDestroyed(){return this.closed;}, async loadURL(url){this.loads.push(url);this.url=url;}, send(...args){this.events.push(args);}, focus(){}, close(){this.closed=true;} });
  const web=makeContents(), admin=makeContents(); let created=0;
  const registry=createResidentSurfaces({baseContents:web,createAdminView(){created++;return{webContents:admin,setBounds(){},setVisible(value){admin.visible=value;}};},attachView(){},getBounds(){return{x:0,y:0,width:800,height:600};},webOrigin:()=> 'http://127.0.0.1:22827',adminOrigin:()=> 'http://127.0.0.1:22828',observe(){}});
  return {registry,web,admin,created:()=>created};
}
test('thirty Web/Admin returns keep both documents and the last deep Admin route',async()=>{
  const f=fixture();await f.registry.open('http://127.0.0.1:22827/chat?id=session-a');
  assert.equal(f.created(),0);
  await f.registry.open('http://127.0.0.1:22828/admin/models');
  for(let i=0;i<30;i++){
    await f.registry.open('http://127.0.0.1:22827/chat',{resume:true});
    await f.registry.open('http://127.0.0.1:22828/admin',{resume:true});
  }
  assert.equal(f.created(),1);assert.equal(f.web.loads.length,1);assert.equal(f.admin.loads.length,1);
  assert.match(f.admin.getURL(),/models$/);assert.match(f.web.getURL(),/session-a$/);
  await f.registry.open('http://127.0.0.1:22827/chat?id=session-b',{sessionId:'session-b'});
  assert.equal(f.web.loads.length,1);
  assert.deepEqual(f.web.events.find(([channel])=>channel==='v8os-shell:navigate-session'),['v8os-shell:navigate-session',{sessionId:'session-b'}]);
});
test('registered identity and precise origin both gate IPC; all auxiliary views close on quit',async()=>{
  const f=fixture();await f.registry.open('http://127.0.0.1:22828/admin');
  assert.equal(f.registry.owns(f.web,'http://127.0.0.1:22828/admin'),false);
  assert.equal(f.registry.owns(f.admin,'http://127.0.0.1:22828/admin'),true);
  assert.equal(f.registry.owns({...f.admin},'http://127.0.0.1:22828/admin'),false);
  await assert.rejects(f.registry.open('http://127.0.0.1:9530/'));
  f.registry.visibility(false);
  assert.deepEqual(f.admin.events.at(-1),['v8os-shell:surface-visibility',{visible:false}]);
  f.registry.dispose();assert.equal(f.admin.closed,true);
});
