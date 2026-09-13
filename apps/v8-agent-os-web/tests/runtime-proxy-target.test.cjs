const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const http=require('node:http');const ts=require('typescript');
test('local authentication restarts after cleanup and ignores a cancelled discovery',async()=>{
 const filename=path.resolve(__dirname,'../src/app/chat/ChatClient.tsx');
 const source=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 let effect;
 function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(source)==='useEffect'&&node.arguments[0]?.getText(source).includes('await readLocalAdminBaseUrl()'))effect=node.arguments[0].getText(source);ts.forEachChild(node,visit)}visit(source);
 assert.ok(effect);
 const discoveries=[],posts=[],logins=[];let refreshes=0;
 const context={status:'unauthenticated',localConnectAttemptedRef:{current:false},setLocalConnectError:()=>{},t:key=>key,router:{refresh(){refreshes++}},
 readLocalAdminBaseUrl:()=>new Promise(resolve=>discoveries.push(resolve)),
 fetch:async(url,request)=>{posts.push(JSON.parse(request.body));return {ok:true}},signIn:async(...request)=>{logins.push(request);return {}}};
 const compiled=ts.transpileModule(`globalThis.mount=${effect}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(compiled,context);
 const stopOld=context.mount();stopOld();const stopNew=context.mount();
 assert.equal(discoveries.length,2,'StrictMode setup/cleanup/setup must have a live replacement');
 discoveries[0]('http://old.invalid');discoveries[1]('http://current.invalid');
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(posts.length,1);assert.equal(posts[0].adminBaseUrl,'http://current.invalid');
 assert.equal(logins.length,1);assert.equal(logins[0][1].adminBaseUrl,'http://current.invalid');assert.equal(refreshes,1);
 stopNew();
});
test('one built runtime resolver follows two isolated Admin and Engine targets without restarting',async t=>{
 const servers=[];async function server(label){return new Promise(resolve=>{const s=http.createServer((_req,res)=>res.end(label));s.listen(0,'127.0.0.1',()=>{servers.push(s);resolve(`http://127.0.0.1:${s.address().port}`)})})}
 t.after(()=>servers.forEach(server=>server.close()));
 const a=await server('A'),b=await server('B');let bridge={adminBaseUrl:a,engineBaseUrl:a+'/v1'};
 const exports={};const source=fs.readFileSync(path.resolve(__dirname,'../src/lib/server/runtime-config.ts'),'utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports,process:{env:{}},URL,require(name){if(name==='next/headers')return{cookies:async()=>({get:()=>undefined})};if(name==='@/lib/server/bridge-config')return{readCanonicalBridge:()=>bridge};throw new Error(name)}});
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'A');assert.equal(await exports.resolveAdminApiBaseUrl(),a+'/api');
 bridge={adminBaseUrl:b,engineBaseUrl:b+'/v1'};
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'B');assert.equal(await exports.resolveAdminApiBaseUrl(),b+'/api');
 assert.equal(exports.resolveLocalAdminRootUrl(),b);
 const login={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/local-admin-connection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports:login,URL,fetch:async(url)=>{assert.equal(url,'/api/connection?local=1');return {ok:true,json:async()=>({connection:{adminBaseUrl:b}})}}});
 assert.equal(await login.readLocalAdminBaseUrl(),b,'new browser without a cookie receives the canonical instance URL');
 for(const app of ['web','admin']){const next=fs.readFileSync(path.resolve(__dirname,`../../v8-agent-os-${app}/next.config.ts`),'utf8');assert.doesNotMatch(next,/9530|readBridgeConfig|homedir/);}
});
