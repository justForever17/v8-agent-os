// Real browser + real isolated Engine + authored reference PNGs. No external generation claim.
// node tests/proxy_scene_acceptance.mjs --live <output-directory> [web-url]
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
if(process.argv[2]!=='--live') throw new Error('Explicit --live required');
const out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:true});
const sharp=require('sharp');
const {chromium}=require('../../v8-agent-os-admin/node_modules/playwright');
const base=process.argv[4]||'http://127.0.0.1:19527';
const sourceFiles=[];
for(const [name,body] of [
 ['robot-front','<rect x="195" y="165" width="130" height="150" rx="30" fill="#dd9b43"/><rect x="205" y="70" width="110" height="90" rx="28" fill="#dda957"/><circle cx="230" cy="108" r="8" fill="#23394a"/><circle cx="290" cy="108" r="8" fill="#23394a"/><path d="M218 136H302" stroke="#23394a" stroke-width="7"/><g fill="#64837a"><rect x="150" y="172" width="32" height="160" rx="16"/><rect x="338" y="172" width="32" height="160" rx="16"/><rect x="209" y="318" width="37" height="120" rx="15"/><rect x="275" y="318" width="37" height="120" rx="15"/></g><path d="M220 230H300M220 250H300" stroke="#efd5a9" stroke-width="7"/>'],
 ['robot-back','<rect x="195" y="165" width="130" height="150" rx="30" fill="#c38a47"/><rect x="205" y="70" width="110" height="90" rx="28" fill="#dda957"/><rect x="220" y="190" width="80" height="100" rx="10" fill="#64837a"/><g stroke="#eedbb8" stroke-width="6"><path d="M236 210H284M236 232H284M236 254H284"/></g><g fill="#64837a"><rect x="150" y="172" width="32" height="160" rx="16"/><rect x="338" y="172" width="32" height="160" rx="16"/><rect x="209" y="318" width="37" height="120" rx="15"/><rect x="275" y="318" width="37" height="120" rx="15"/></g>'],
 ['orb-front','<circle cx="260" cy="255" r="160" fill="#65b8b2"/><ellipse cx="210" cy="205" rx="70" ry="40" fill="#c2ece2" opacity=".7"/><path d="M110 230Q260 120 410 280M108 275Q260 400 410 220" stroke="#f0d386" stroke-width="11" fill="none"/>'],
 ['orb-detail','<circle cx="260" cy="260" r="215" fill="#65b8b2"/><path d="M40 270Q260 80 480 270M40 310Q260 510 480 200" stroke="#f0d386" stroke-width="23" fill="none"/><g fill="#397c81"><circle cx="210" cy="230" r="4"/><circle cx="275" cy="190" r="3"/><circle cx="305" cy="330" r="5"/></g>'],
 ]) {
 const file=path.join(out,name+'.png');
 await sharp(Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="520" height="520"><rect width="520" height="520" fill="#f3eee3"/>${body}</svg>`)).png().toFile(file);sourceFiles.push(file);
}
const browser=await chromium.launch({headless:true,channel:'msedge',args:['--no-proxy-server']});
const page=await browser.newPage({viewport:{width:1500,height:980}});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const api=async()=>{const r=await fetch(base+'/api/workbench/sessions/session-scene/canvas/graph');return await r.json();};
try{
 await page.goto(base,{waitUntil:'networkidle'});
 if(!process.argv.includes('--resume')) {
 if(await page.getByRole('button',{name:'编辑场景与图集',exact:true}).count()) await page.getByRole('button',{name:'编辑场景与图集',exact:true}).first().click();
 else {await page.getByTestId('creative-artifact-canvas').click({button:'right',position:{x:240,y:180}});await page.getByRole('button',{name:'更多动作',exact:true}).click();await page.getByRole('menuitem',{name:'多实体代理场景',exact:true}).click();}
 const dialog=page.getByRole('dialog',{name:'代理场景',exact:true});
 await dialog.getByLabel('实体名称',{exact:true}).fill('巡游机器人');
 await dialog.getByRole('combobox',{name:'主体类型',exact:true}).selectOption('character');
 await dialog.getByRole('combobox',{name:'代理形体',exact:true}).selectOption('capsule');
 await dialog.getByLabel('尺寸（米） Y',{exact:true}).fill('1.6');
 await dialog.getByLabel('位置（米） Y',{exact:true}).fill('0.9');
 await dialog.getByLabel('外形与身份特征',{exact:true}).fill('双圆形深色眼睛，圆角方头，胸前两条浅色横纹，背面绿色通风盖。');
 await dialog.getByLabel('材质与表面细节',{exact:true}).fill('温暖赭黄色搪瓷躯干，哑光苔绿色四肢；保持前后细节一致。');
 await dialog.getByLabel('场景风格与光照',{exact:true}).fill('温暖手工玩具展台，柔和日光，米色背景，细腻实体质感。');
 await dialog.getByRole('tab',{name:'动作',exact:true}).click();
 await dialog.getByRole('button',{name:'在当前时间添加动作关键帧',exact:true}).click();
 await dialog.getByLabel('播放时间',{exact:true}).fill('4');
 await dialog.getByRole('button',{name:'在当前时间添加动作关键帧',exact:true}).click();
 await dialog.getByLabel('位置（米） X',{exact:true}).fill('1.5');
 await dialog.getByLabel('右臂角度',{exact:true}).fill('-70');
 await dialog.getByLabel('左腿角度',{exact:true}).fill('30');
 const entities=(await api()).graph.nodes.find(n=>n.actionDefinitionId==='creative_media.render_proxy_scene_control_pack').parameters.scene.entities;
 await dialog.getByRole('button',{name:entities[1].name||'实体 2',exact:true}).click();
 await dialog.getByRole('tab',{name:'实体',exact:true}).click();
 await dialog.getByLabel('实体名称',{exact:true}).fill('青釉金纹球');
 await dialog.getByRole('combobox',{name:'代理形体',exact:true}).selectOption('sphere');
 await dialog.getByLabel('外形与身份特征',{exact:true}).fill('青绿色球体，两条交错金色曲线，保持球形比例。');
 await dialog.getByLabel('材质与表面细节',{exact:true}).fill('半透明陶瓷釉面，少量细小烧制斑点，金纹反光。');
 await dialog.getByLabel('播放时间',{exact:true}).fill('0');
 await dialog.getByRole('tab',{name:'动作',exact:true}).click();
 await dialog.getByRole('button',{name:'在当前时间添加动作关键帧',exact:true}).click();
 await dialog.getByLabel('播放时间',{exact:true}).fill('4');
 await dialog.getByRole('button',{name:'在当前时间添加动作关键帧',exact:true}).click();
 await dialog.getByLabel('位置（米） X',{exact:true}).fill('-1.5');
 await dialog.getByLabel('位置（米） Z',{exact:true}).fill('1');
 await dialog.getByRole('tab',{name:'参考图集',exact:true}).click();
 await dialog.locator('input[type=file]').setInputFiles(sourceFiles);
 await page.waitForFunction(()=>document.querySelectorAll('[role=dialog] select option').length>=14);
 // Uploads are real session sources; binding fetches bytes and hashes them before save.
 for(const [entity,name,role,purpose] of [
 ['巡游机器人','robot-front.png','front','仅用于机器人的正面身份、胸纹和外形，不参考背景。'],
 ['巡游机器人','robot-back.png','back','机器人转身和遮挡后恢复时使用背面通风盖及四肢材质。'],
 ['青釉金纹球','orb-front.png','front','仅用于球体的青釉、金色曲线位置和球形轮廓。'],
 ['青釉金纹球','orb-detail.png','detail','球体近景釉面斑点和金纹细节，不改变运动。'],
 ]) {
  await dialog.getByRole('button',{name:entity,exact:true}).click();
  const selector=dialog.getByRole('combobox',{name:'当前会话素材',exact:true});
  await selector.locator('option').filter({hasText:name}).first().waitFor({state:'attached'});
  const value=await selector.locator('option').filter({hasText:name}).first().getAttribute('value');
  await selector.selectOption(value);
  await dialog.getByRole('combobox',{name:'参考视角 / 类型',exact:true}).last().selectOption(role);
  await dialog.getByLabel('参考用途',{exact:true}).last().fill(purpose);
  await dialog.getByRole('button',{name:'绑定到此实体',exact:true}).click();
  await page.waitForFunction(()=>{const d=document.querySelector('[role=dialog]');const a=d.querySelectorAll('textarea');return a.length>0&&a[a.length-1].value==='';});
 }
 await dialog.getByLabel('播放时间',{exact:true}).fill('2');
 await page.screenshot({path:path.join(out,'web-scene-atlas.png')});
 await dialog.getByRole('tab',{name:'镜头',exact:true}).click();
 await dialog.getByRole('button',{name:'4.00s',exact:true}).click();
 await dialog.getByLabel('相机位置（米） X',{exact:true}).fill('3');
 await page.screenshot({path:path.join(out,'web-scene-camera.png')});
 const before=await api();
 if(before.runtime?.status!=='idle'&&before.runtime?.status)throw new Error('Editing unexpectedly ran a job');
 await dialog.getByRole('button',{name:'保存场景',exact:true}).click();
 await page.waitForTimeout(1400);
 const saved=await api();fs.writeFileSync(path.join(out,'saved-graph.json'),JSON.stringify(saved,null,2));
 const action=saved.graph.nodes.find(n=>n.actionDefinitionId==='creative_media.render_proxy_scene_control_pack');
 if(saved.graph.edges.filter(e=>e.to===action.nodeId&&e.bindingKey).length!==4)throw new Error('Reference bindings lost');
 }
 await page.reload({waitUntil:'networkidle'});
 await page.getByRole('button',{name:'整理布局',exact:true}).click();
 await page.getByRole('button',{name:'显示全部',exact:true}).click();
 if((await api()).runtime?.status!=='succeeded')await page.getByRole('button',{name:'运行全部',exact:true}).click();
 await page.waitForTimeout(1000);
 const runPanel=page.getByRole('button',{name:'确认运行',exact:true});
 if(await runPanel.count())await runPanel.click();
 let completed;
 for(let i=0;i<90;i++){completed=await api();if(['succeeded','failed','cancelled'].includes(completed.runtime?.status))break;await page.waitForTimeout(1000);}
 if(completed.runtime?.status!=='succeeded')throw new Error('Local render did not succeed: '+JSON.stringify(completed.runtime));
 await page.waitForTimeout(2500);await page.getByRole('button',{name:'显示全部',exact:true}).click();
 const preview=page.locator('[data-canvas-node] video').first();
 await preview.waitFor({state:'visible'});
 await page.waitForFunction(()=>{const v=document.querySelector('[data-canvas-node] video');return v&&v.readyState>=2&&v.videoWidth>0;});
 await preview.evaluate(async video=>{video.muted=true;await video.play();});
 await page.waitForTimeout(400);
 if(!await preview.evaluate(video=>video.currentTime>0))throw new Error('Control pack preview did not play');
 await preview.evaluate(video=>video.pause());
 await page.screenshot({path:path.join(out,'web-scene-rendered.png')});
 await page.reload({waitUntil:'networkidle'});await page.screenshot({path:path.join(out,'web-scene-reload.png')});
 fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({level:'real-local-browser-engine',externalProvider:false,errors,graph:completed,sourceFiles},null,2));
 console.log(JSON.stringify({status:'passed',errors,output:out,sourceFiles}));
}catch(error){await page.screenshot({path:path.join(out,'web-scene-failure.png')});throw error;}finally{await browser.close();}
