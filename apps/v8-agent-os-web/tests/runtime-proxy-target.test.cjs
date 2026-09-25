const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const http=require('node:http');const ts=require('typescript');
test('local authentication uses the Engine local-session credential flow', () => {
 const source=fs.readFileSync(path.resolve(__dirname, '../src/app/chat/ChatClient.tsx'),'utf8');
 assert.match(source,/status !== "unauthenticated"/); assert.match(source,/signIn\("credentials"/);
 assert.match(source,/localSession: "1"/); assert.doesNotMatch(source,/readLocalAdminBaseUrl/);
});
test('one Product Web host remains authoritative while the Engine target changes without restarting',async t=>{
 const servers=[];async function server(label){return new Promise(resolve=>{const s=http.createServer((_req,res)=>res.end(label));s.listen(0,'127.0.0.1',()=>{servers.push(s);resolve(`http://127.0.0.1:${s.address().port}`)})})}
 t.after(()=>servers.forEach(server=>server.close()));
 const a=await server('A'),b=await server('B');
 let bridge={adminBaseUrl:'http://127.0.0.1:9528',engineBaseUrl:a+'/v1'};
 const productOrigin={};
 const productSource=fs.readFileSync(path.resolve(__dirname,'../src/lib/server/product-origin.ts'),'utf8');
 const coreProductOrigin=require(path.resolve(__dirname,'../../v8-agent-os-cli/src/product_origin.mjs'));
 vm.runInNewContext(ts.transpileModule(productSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports:productOrigin,process:{env:{V8_WEB_BASE_URL:'http://127.0.0.1:9527'}},URL,require(name){if(name==='@core/product_origin.mjs')return coreProductOrigin;throw new Error(name)}});
 const exports={};const source=fs.readFileSync(path.resolve(__dirname,'../src/lib/server/runtime-config.ts'),'utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports,process:{env:{V8_WEB_BASE_URL:'http://127.0.0.1:9527'}},URL,require(name){if(name==='next/headers')return{cookies:async()=>({get:()=>undefined})};if(name==='@/lib/server/bridge-config')return{readCanonicalBridge:()=>bridge};if(name==='./product-origin')return productOrigin;throw new Error(name)}});
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'A');
 assert.equal(await exports.resolveAdminApiBaseUrl(),'http://127.0.0.1:9527/api/admin');
 assert.equal(exports.resolveLocalAdminRootUrl(),'http://127.0.0.1:9527');
 bridge={adminBaseUrl:b,engineBaseUrl:b+'/v1'};
 assert.equal(await (await fetch(await exports.resolveEngineRootUrl())).text(),'B','Engine target remains dynamically resolved from the canonical bridge');
 assert.equal(await exports.resolveAdminApiBaseUrl(),'http://127.0.0.1:9527/api/admin','a legacy Admin URL cannot become the Web host authority');
 assert.equal(exports.resolveLocalAdminRootUrl(),'http://127.0.0.1:9527');
 const next=fs.readFileSync(path.resolve(__dirname,'../../v8-agent-os-web/next.config.ts'),'utf8');
 assert.doesNotMatch(next,/9530|readBridgeConfig|homedir/);
});
