// Full production Canvas component against an isolated production Engine HTTP service.
// Start Engine tests/scripts/run_canvas_scene_local_server.py first. No provider mocks.
// node tests/proxy_scene_ui.mjs --live <output-directory> [engine-port] [web-port]
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
if (process.argv[2] !== '--live' || !process.argv[3]) throw new Error('Use --live <isolated output directory>');
const output = path.resolve(process.argv[3]);
const enginePort = Number(process.argv[4] || 19531);
const port = Number(process.argv[5] || 19527);
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webpack = require(path.join(web, 'node_modules/next/dist/compiled/webpack/webpack')).webpack;
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, 'entry.tsx'), `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { LocaleProvider } from '@/components/providers/LocaleProvider';
import { CreativeArtifactCanvas } from '@/components/workbench/CreativeArtifactCanvas';
const sessionId = new URLSearchParams(location.search).get('session') || 'session-scene';
createRoot(document.getElementById('root')).render(<LocaleProvider initialLocale="zh-CN"><main style={{height:'100vh',display:'flex',flexDirection:'column'}}><header style={{padding:'10px 20px',borderBottom:'1px solid #ddd',fontSize:12}}>Canvas · 本地隔离验收</header><div style={{flex:1,minHeight:0,position:'relative'}}><CreativeArtifactCanvas document={{id:'scene-doc',kind:'creative_canvas',title:'Scene',subjectRef:{sessionId}}} /></div></main></LocaleProvider>);
`);
fs.writeFileSync(path.join(output, 'loader.cjs'), `const ts=require(${JSON.stringify(require.resolve('typescript'))});module.exports=function(source){return ts.transpileModule(source,{fileName:this.resourcePath,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,esModuleInterop:true}}).outputText};`);
await new Promise((resolve, reject) => webpack({
  mode:'production', devtool:false, context:web, entry:path.join(output,'entry.tsx'), output:{path:output,filename:'fixture.js'},
  resolve:{extensions:['.tsx','.ts','.js','.jsx'],modules:[path.join(web,'node_modules'),'node_modules'],alias:{'@':path.join(web,'src')}},
  module:{rules:[{test:/\.[jt]sx?$/,exclude:/node_modules/,use:path.join(output,'loader.cjs')}]},
  optimization:{minimize:false},plugins:[new webpack.DefinePlugin({'process.env.NODE_ENV':JSON.stringify('production')})],
},(error,stats)=>error||stats.hasErrors()?reject(error||new Error(stats.toString({all:false,errors:true}))):resolve()));
const cssRoot=path.join(web,'.next/static/css');
fs.writeFileSync(path.join(output,'app.css'),fs.readdirSync(cssRoot).filter(name=>name.endsWith('.css')).map(name=>fs.readFileSync(path.join(cssRoot,name),'utf8')).join('\n'));
fs.writeFileSync(path.join(output,'index.html'),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
const requests=[];
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/api/') || url.pathname.startsWith('/v1/')){
    let target=url.pathname.replace(/^\/api\/workbench\//,'/v1/').replace(/^\/api\//,'/v1/');
    if(target==='/v1/upload') target='/v1/chat/upload';
    if(target==='/v1/sources' && url.searchParams.has('sessionId')) {url.searchParams.set('session_id',url.searchParams.get('sessionId'));url.searchParams.delete('sessionId');}
    if(target==='/v1/sources' && url.searchParams.has('includeUnbound')) {url.searchParams.set('include_unbound',url.searchParams.get('includeUnbound'));url.searchParams.delete('includeUnbound');}
    if(target==='/v1/artifacts') {target='/v1/sessions/'+encodeURIComponent(url.searchParams.get('sessionId')||'session-scene')+'/artifacts';}
    if(target==='/v1/plugins/mentions'){res.writeHead(200,{'Content-Type':'application/json'});res.end('{"plugins":[]}');return;}
    requests.push({method:req.method,path:target});
    const upstream=http.request({hostname:'127.0.0.1',port:enginePort,path:target+url.search,method:req.method,headers:{...req.headers,host:'127.0.0.1:'+enginePort}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
    upstream.on('error',()=>{res.writeHead(502);res.end('Isolated Engine unavailable');});req.pipe(upstream);return;
  }
  if(url.pathname==='/fixture-requests'){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(requests));return;}
  const file=path.resolve(output,url.pathname==='/'?'index.html':'.'+url.pathname);
  if(!file.startsWith(output+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.mp4':'video/mp4'})[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
});
server.listen(port,'127.0.0.1',()=>console.log(JSON.stringify({web:`http://127.0.0.1:${port}`,engine:`http://127.0.0.1:${enginePort}`,output})));
