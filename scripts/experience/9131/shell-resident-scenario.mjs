// Actual Electron surface buttons, keyboard selection and wheel-created scroll.
// Caller supplies the real pages and the previously captured synthetic baseline.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

export async function verifyResidentRoundTrips({web,admin,app,out,baseline,adminBaseline}, rounds=30) {
  const observations=[];
  const filename=path.join(out,'resident-rounds.json');
  for(const page of [web,admin])await page.evaluate(()=>{
    window.__experienceSwitchEvents=[];
    document.addEventListener('click',event=>{
      const label=event.target.closest('button')?.textContent.trim();
      if(['聊天','控制台'].includes(label))window.__experienceSwitchEvents.push({label,at:performance.timeOrigin+performance.now()});
    },true);
  });
  const readWeb=()=>web.locator('textarea').first().evaluate(e=>({
    text:e.value,start:e.selectionStart,end:e.selectionEnd,
    scroll:document.querySelector('.v8-chat-viewport-surface').scrollTop,
    doc:window.__experienceDoc,timeOrigin:performance.timeOrigin,
  }));
  try{
    for(let i=0;i<rounds;i++){
      await web.getByRole('button',{name:'控制台',exact:true}).click();
      await admin.waitForFunction(()=>window.__surfaceVisible===true);
      const adminIdentity=await admin.evaluate(()=>({doc:window.__experienceDoc,timeOrigin:performance.timeOrigin}));
      assert.deepEqual(adminIdentity,adminBaseline,'Admin document must remain resident');
      const began=performance.now();
      await admin.getByRole('button',{name:'聊天',exact:true}).click();
      await web.waitForFunction(()=>window.__surfaceVisible===true);
      const nodeElapsed=performance.now()-began;
      const paintedAt=await web.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.timeOrigin+performance.now())))));
      const clickedAt=await admin.evaluate(()=>window.__experienceSwitchEvents.at(-1).at);
      const actual=await readWeb();
      const row={index:i,actual,buttonToTwoFramesMs:paintedAt-clickedAt,automationReturnMs:nodeElapsed};
      observations.push(row);fs.writeFileSync(filename,JSON.stringify({observations},null,2));
      assert.equal(actual.doc,baseline.doc,'Web document identity changed');
      assert.equal(actual.timeOrigin,baseline.timeOrigin,'Web document was reconstructed');
      assert.equal(actual.text,baseline.text,'Composer draft changed on surface return');
      assert.equal(actual.start,baseline.start,'Selection start lost');assert.equal(actual.end,baseline.end,'Selection end lost');
      assert.ok(Math.abs(actual.scroll-baseline.scroll)<1,`Reading scroll drifted: ${actual.scroll} vs ${baseline.scroll}`);
    }
    const sizes=await app.evaluate(({webContents,BrowserWindow})=>({
      contents:webContents.getAllWebContents().map(w=>({id:w.id,origin:w.getURL().startsWith('http')?new URL(w.getURL()).origin:'startup',destroyed:w.isDestroyed()})),
      windows:BrowserWindow.getAllWindows().length,
    }));
    assert.equal(sizes.windows,1);assert.equal(sizes.contents.filter(x=>['http://127.0.0.1:9527','http://127.0.0.1:9528'].includes(x.origin)).length,2);
    const sorted=observations.map(x=>x.buttonToTwoFramesMs).sort((a,b)=>a-b);
    const result={status:'PASS',rounds:observations.length,documentDraftSelectionScrollPreserved:true,sizes,
      buttonToTwoFrames:{medianMs:(sorted[14]+sorted[15])/2,p95Ms:sorted[Math.ceil(sorted.length*.95)-1],samples:sorted},
      qualification:'Actual Electron buttons → IPC surface activation → two destination animation frames. Includes test synchronization; no baseline/INP/model performance claim.',observations};
    fs.writeFileSync(filename,JSON.stringify(result,null,2));
    await web.screenshot({path:path.join(out,'resident-rounds-web.png')});
    return result;
  }catch(error){fs.writeFileSync(filename,JSON.stringify({status:'FAIL',error:String(error),observations},null,2));throw error;}
}
