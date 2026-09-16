import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'tmp/resident-web-evidence'

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='msedge',headless=True)
        context=await browser.new_context(viewport={'width':1440,'height':900});page=await context.new_page()
        state={'cursor':0,'requests':0,'maxBatch':0,'fail':False,'inputs':[],'running':True,'large':False,'inflight':0,'peak':0,'hold404':False,'held':False,'identityRequests':0}
        await context.add_init_script("const originalFetch=window.fetch;window.fetch=(url,options)=>originalFetch(url,String(url).includes('/bg_processes/')?{...options,signal:undefined}:options)")
        await context.add_init_script("window.v8osShell={isShell:true,onSurfaceVisibilityChange(cb){window.__setVisible=cb;cb({visible:true});return()=>{}},onNavigateSession(){return()=>{}},onAdminSessionLockChange(){return()=>{}},getWindowState:async()=>({}),onWindowStateChange:()=>()=>{},getAdminSessionLock:async()=>({locked:false})}")
        await context.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{readText:async()=>{if(window.__clipboardDenied)throw Error('fixture denied');return window.__clipboardText||''},writeText:async value=>{window.__copiedText=value}}})")
        total=52*1024*1024;chunk='0123456789abcdef'*4096
        errors=[];page.on('pageerror',lambda e:errors.append(e.message))
        async def route(r):
            u=urlparse(r.request.url)
            if u.netloc!='127.0.0.1:22827':return await r.abort()
            if not u.path.startswith('/api/'):return await r.continue_()
            body={}
            if u.path=='/api/auth/session':body={'user':{'id':'fixture-owner','email':'fixture@example.invalid'},'expires':'2099-01-01'}
            elif u.path=='/api/client/instance':
                state['identityRequests']+=1
                if state['identityRequests']==1:return await r.fulfill(status=503,json={'detail':'fixture first readiness read unavailable'})
                body={'instanceId':'fixture-instance'}
            elif u.path=='/api/ui-preferences/theme':body={'theme':'light'}
            elif '/bg_processes/' in u.path:
                if r.request.method=='POST':
                    if u.path.endswith('/input'):state['inputs'].append(r.request.post_data_json['input_text'])
                    if u.path.endswith('/terminate'):state['running']=False
                    return await r.fulfill(json={'ok':True})
                if state['fail']:return await r.fulfill(status=503,json={'error':'fixture outage'})
                if state['hold404'] and u.path.endswith('/fixture-process'):
                    state['held']=True;state['hold404']=False;await asyncio.sleep(1)
                    return await r.fulfill(status=404,json={'error':'late old owner'})
                state['inflight']+=1;state['peak']=max(state['peak'],state['inflight'])
                cursor=int(parse_qs(u.query).get('cursor',['0'])[0]);limit=total if state['large'] else 1024
                output=chunk[:min(65536,max(0,limit-cursor))];state['maxBatch']=max(state['maxBatch'],len(output));state['cursor']=cursor+len(output);state['requests']+=1
                body={'status':'success','output':output,'outputCursor':state['cursor'],'outputGeneration':u.path.split('/')[-1],'outputHasMore':state['cursor']<limit,'outputTotalBytes':limit,'is_running':state['running']}
                await r.fulfill(json=body);state['inflight']-=1;return
            else:
                errors.append(f'Undeclared fixture API: {u.path}')
                return await r.fulfill(status=500,json={'error':'undeclared_fixture_api'})
            return await r.fulfill(json=body)
        await context.route('**/*',route)
        try:
            await page.goto('http://127.0.0.1:22827/terminal-fixture',wait_until='domcontentloaded',timeout=120000)
            await expect(page.get_by_text('已连接',exact=True)).to_be_visible(timeout=60000)
            assert state['identityRequests']==2,'initial readiness failure should recover without creating a second terminal'
            await page.locator('.xterm').evaluate('e=>e.dataset.fixtureIdentity="original"')
            for _ in range(20):await page.get_by_role('button',name='New projection',exact=True).click()
            assert await page.locator('.xterm').get_attribute('data-fixture-identity')=='original'
            await page.evaluate('window.__setVisible({visible:false})')
            await page.wait_for_timeout(350)
            hidden_requests=state['requests']
            await page.wait_for_timeout(550)
            assert state['requests']==hidden_requests,'hidden terminals must stop polling'
            await page.evaluate('window.__setVisible({visible:true})')
            await expect(page.get_by_text('已连接',exact=True)).to_be_visible()
            assert await page.locator('.xterm').get_attribute('data-fixture-identity')=='original'
            state['fail']=True
            await expect(page.get_by_text('连接中断',exact=True)).to_be_visible(timeout=5000)
            assert await page.get_by_role('button',name='终止',exact=True).is_visible()
            state['fail']=False;await page.get_by_role('button',name='重连',exact=True).click()
            await expect(page.get_by_text('已连接',exact=True)).to_be_visible()
            assert await page.locator('.xterm').get_attribute('data-fixture-identity')=='original'
            state['large']=True
            await page.locator('.xterm-helper-textarea').focus();await page.keyboard.press('Control+c');await page.keyboard.press('Control+c')
            await page.evaluate("window.__clipboardText='terminal clipboard fixture'")
            await page.locator('.xterm-screen').click(button='right')
            for _ in range(900):
                if state['cursor']>=total:break
                await page.wait_for_timeout(100)
            assert state['cursor']==total,state
            assert state['inputs'].count('\x03')==2,state['inputs']
            assert any('terminal clipboard fixture' in value for value in state['inputs']),state['inputs']
            await page.evaluate("window.__clipboardDenied=true")
            await page.locator('.xterm-screen').click(button='right')
            await expect(page.get_by_text('无法读取剪贴板，请使用 Ctrl+V 或允许剪贴板访问。',exact=True)).to_be_visible()
            await page.evaluate("window.__clipboardDenied=false")
            assert state['peak']==1 and state['maxBatch']<=65536
            state['large']=False;state['hold404']=True
            for _ in range(30):
                if state['held']:break
                await page.wait_for_timeout(50)
            assert state['held']
            await page.get_by_role('button',name='Switch terminal',exact=True).click()
            await page.wait_for_timeout(1300)
            await expect(page.get_by_text('已连接',exact=True)).to_be_visible()
            await page.get_by_role('button',name='终止',exact=True).click()
            await expect(page.get_by_text('已退出',exact=True)).to_be_visible()
            assert not errors,errors
            result={'level':'ACTUAL_XTERM_SYNTHETIC_HTTP','projectionUpdatesRetainRenderer':True,'hiddenStopsPolling':True,'disconnectNotExit':True,'resumeRetainsRenderer':True,'late404CannotExitNewTarget':True,'outputBytes':total,'maxBatch':state['maxBatch'],'peakRequests':state['peak'],'doubleCtrlCTransmitted':True,'rightClickPasteTransmitted':True,'clipboardDenialVisible':True,'errors':errors}
            OUT.mkdir(parents=True,exist_ok=True);(OUT/'terminal-ui.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
        except Exception:
            print(json.dumps({'errors':errors,'state':state},ensure_ascii=True))
            raise
        finally:
            await page.screenshot(path=str(OUT/'terminal-last.png'));await browser.close()

asyncio.run(main())
