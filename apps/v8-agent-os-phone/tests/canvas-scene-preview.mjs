// Production Phone MediaPlayer + source resolver + lightbox on React Native Web.
// Native WebView is represented by an iframe; this is not a physical-phone test.
// node tests/canvas-scene-preview.mjs --live <output-dir> <web-base> <video-artifact-id> <image-artifact-id>
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
if(process.argv[2]!=='--live'||!process.argv[6])throw new Error('Use --live <output> <web-base> <video-id> <image-id>');
const out=path.resolve(process.argv[3]);const base=process.argv[4];
const phone=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const web=path.resolve(phone,'../v8-agent-os-web');
const {chromium}=require('../../v8-agent-os-web/node_modules/playwright');
const webpack=require(path.join(web,'node_modules/next/dist/compiled/webpack/webpack')).webpack;
fs.mkdirSync(out,{recursive:true});
const write=(file,text)=>fs.writeFileSync(path.join(out,file),text);
write('prefs.ts',`import {getThemeColors} from '@/src/theme/tokens';import {translateCurrent} from '@/src/lib/locale';export const useUiPrefs=()=>({colors:getThemeColors('light'),t:translateCurrent,themeMode:'light'});`);
write('session.ts',`export const useAppSession=()=>({adminBaseUrl:${JSON.stringify(base)},authorizedFetch:fetch});`);
write('native.tsx',`import React from 'react';export const WebView=({source,style})=><iframe title="Phone media WebView" srcDoc={source.html} style={{border:0,width:'100%',height:style?.height||300}}/>;export const MaterialCommunityIcons=()=> <span aria-hidden="true">◇</span>;export const useAudioPlayer=()=>({});export const useAudioPlayerStatus=()=>({});export const useIsFocused=()=>true;export const useAppVisibility=()=>true;export const setStringAsync=async()=>{};export const downloadUrlToUserSelectedFile=async()=>{throw Error('Not exercised in preview test')};`);
const artifact=(id)=>`${base}/api/artifacts/${encodeURIComponent(id)}/content?sessionId=session-scene`;
write('entry.tsx',`import React from 'react';import {createRoot} from 'react-dom/client';import {MediaPlayer,ImagePreview} from '@/src/components/chat/MediaRenderers';createRoot(document.getElementById('root')).render(<main style={{padding:16,fontFamily:'system-ui',display:'grid',gap:16}}><header><h2 style={{fontSize:18}}>场景结果</h2><p style={{fontSize:12,color:'#777'}}>本地代理渲染 · 双实体运动与镜头</p></header><MediaPlayer type="video" title="巡游机器人与青釉球" src={${JSON.stringify(artifact(process.argv[5]))}} candidates={[${JSON.stringify(artifact(process.argv[5]))}]} /><ImagePreview alt="实体身份与参考图集" src={${JSON.stringify(artifact(process.argv[6]))}} candidates={[${JSON.stringify(artifact(process.argv[6]))}]} /></main>);`);
write('loader.cjs',`const ts=require(${JSON.stringify(require.resolve('typescript'))});module.exports=function(source){return ts.transpileModule(source,{fileName:this.resourcePath,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,esModuleInterop:true}}).outputText};`);
const alias={'react-native$':require.resolve('react-native-web'),'@/src/providers/ui-prefs$':path.join(out,'prefs.ts'),'@/src/providers/app-session$':path.join(out,'session.ts')};
for(const mod of ['react-native-webview','@expo/vector-icons','expo-audio','@react-navigation/native','@/src/hooks/use-app-visibility','expo-clipboard','@/src/lib/file-transfer'])alias[mod+'$']=path.join(out,'native.tsx');
alias['@']=phone;
await new Promise((resolve,reject)=>webpack({mode:'production',devtool:false,context:phone,entry:path.join(out,'entry.tsx'),output:{path:out,filename:'fixture.js'},resolve:{extensions:['.tsx','.ts','.js'],modules:[path.join(phone,'node_modules'),'node_modules'],alias},module:{rules:[{test:/\.tsx?$/,exclude:/node_modules/,use:path.join(out,'loader.cjs')}]},optimization:{minimize:false},plugins:[new webpack.DefinePlugin({__DEV__:'false'})]},(error,stats)=>error||stats.hasErrors()?reject(error||new Error(stats.toString({all:false,errors:true}))):resolve()));
write('index.html','<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script src="fixture.js"></script>');
const server=http.createServer((req,res)=>{const f=req.url==='/'?'index.html':'fixture.js';res.setHeader('Content-Type',f.endsWith('.js')?'text/javascript':'text/html; charset=utf-8');res.end(fs.readFileSync(path.join(out,f)));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
// Browser-only host mapping represents a reachable paired server without
// weakening Phone's real rejection of localhost media URLs.
const host=new URL(base).hostname;
const browser=await chromium.launch({headless:true,channel:'msedge',args:['--no-proxy-server',...(host.endsWith('.test')?[`--host-resolver-rules=MAP ${host} 127.0.0.1`]:[])]});
const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle'});
 const frame=page.frameLocator('iframe').first();const player=frame.locator('video');
 await player.waitFor();
 await player.evaluate(async v=>{v.muted=true;await v.play();});await page.waitForTimeout(500);
 const video=await player.evaluate(v=>({width:v.videoWidth,height:v.videoHeight,time:v.currentTime,duration:v.duration}));
 if(!video.width||video.time<=0)throw Error('Phone result did not decode/play');
 await player.evaluate(v=>v.pause());
 await page.screenshot({path:path.join(out,'phone-scene-preview.png'),fullPage:true});
 await page.getByText('巡游机器人与青釉球',{exact:true}).click();
 await page.waitForTimeout(400);
 if(await page.locator('iframe').count()<2)throw Error('Phone lightbox did not open');
 await page.screenshot({path:path.join(out,'phone-scene-lightbox.png')});
 write('phone-evidence.json',JSON.stringify({level:'Phone React Native Web + native WebView shim',physicalPhone:false,video,errors,externalProvider:false},null,2));
 console.log(JSON.stringify({status:'passed',video,errors,out}));
}catch(error){await page.screenshot({path:path.join(out,'phone-failure.png'),fullPage:true});console.log(JSON.stringify({errors,text:await page.locator('body').innerText()}));throw error;}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
