const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const http=require('node:http');const ts=require('typescript');
test('one built runtime resolver follows two isolated Admin and Engine targets without restarting',async t=>{
 const servers=[];async function server(label){return new Promise(resolve=>{const s=http.createServer((_req,res)=>res.end(label));s.listen(0,'127.0.0.1',()=>{servers.push(s);resolve(`http://127.0.0.1:${s.address().port}`)})})}
 t.after(()=>servers.forEach(server=>server.close()));
 const a=await server('A'),b=await server('B');let bridge={adminBaseUrl:a,engineBaseUrl:a+'/v1'};
 const exports={};const source=fs.readFileSync(path.resolve(__dirname,'../src/lib/server/runtime-config.ts'),'utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports,process:{env:{}},URL,require(name){if(name==='next/headers')return{cookies:async()=>({get:()=>undefined})};if(name==='@/lib/server/bridge-config')return{readCanonicalBridge:()=>bridge};throw new Error(name)}});
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'A');assert.equal(await exports.resolveAdminApiBaseUrl(),a+'/api');
 bridge={adminBaseUrl:b,engineBaseUrl:b+'/v1'};
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'B');assert.equal(await exports.resolveAdminApiBaseUrl(),b+'/api');
 for(const app of ['web','admin']){const next=fs.readFileSync(path.resolve(__dirname,`../../v8-agent-os-${app}/next.config.ts`),'utf8');assert.doesNotMatch(next,/9530|readBridgeConfig|homedir/);}
});
