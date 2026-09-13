"""Actual PersonalizationProvider image/video playback and screenshot color checks."""
import asyncio
import io
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
from PIL import Image
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/"tmp/resident-web-evidence"

async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    video=OUT/"fixture-2s.mp4"
    if not video.exists(): subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-f","lavfi","-i","color=c=blue:s=320x180:d=2:r=24","-c:v","libx264","-pix_fmt","yuv420p",str(video)],check=True)
    buffer=io.BytesIO();Image.new("RGB",(320,180),(210,40,70)).save(buffer,format="WEBP",lossless=True);image=buffer.getvalue()
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel="chrome",headless=True)
        context=await browser.new_context(viewport={"width":1440,"height":900})
        appearance={"webBackground":{"revision":1,"enabled":True,"imageDurationMs":1000,"order":"ordered","items":[{"id":"video","media":"/user-assets/background/fixture.mp4","kind":"video","fit":"cover","position":"50% 50%"},{"id":"image","media":"/user-assets/background/fixture.webp","kind":"image","fit":"cover","position":"50% 50%"}]}}
        (ROOT/'tmp/resident-web-ui/fixture-appearance.json').write_text(json.dumps(appearance),encoding='utf-8')
        await context.add_cookies([{"name":"fixture-appearance","value":quote(json.dumps(appearance,separators=(',',':'))),"url":"http://127.0.0.1:22827"}])
        await context.add_init_script('''window.__timeline=[];window.__visibleCallback=null;window.v8osShell={isShell:true,onSurfaceVisibilityChange(cb){window.__visibleCallback=cb;cb({visible:true});return()=>{}},onNavigateSession(){return()=>{}},onAdminSessionLockChange(){return()=>{}},getWindowState:async()=>({}),onWindowStateChange:()=>()=>{},reportActiveSession(){},getAdminSessionLock:async()=>({locked:false})};document.addEventListener('ended',e=>{if(e.target.tagName==='VIDEO')window.__timeline.push({event:'ended',at:performance.now()})},true);new MutationObserver(()=>{const kind=document.documentElement.dataset.v8WallpaperKind;if(kind&&window.__timeline.at(-1)?.kind!==kind)window.__timeline.push({kind,at:performance.now()})}).observe(document,{subtree:true,attributes:true,attributeFilter:['data-v8-wallpaper-kind']});''')
        page=await context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(e.message))
        cdp=await context.new_cdp_session(page);await cdp.send('Runtime.enable');cdp.on('Runtime.exceptionThrown',lambda payload:errors.append(payload['exceptionDetails']))
        async def route(r):
            u=urlparse(r.request.url)
            if u.netloc!='127.0.0.1:22827':return await r.abort()
            if u.path=='/api/user-media':
                is_video=parse_qs(u.query).get('src',[''])[0].endswith('.mp4')
                data=video.read_bytes() if is_video else image
                range_header=r.request.headers.get('range')
                if range_header:
                    start,end=range_header.removeprefix('bytes=').split('-');start=int(start);end=min(int(end) if end else len(data)-1,len(data)-1)
                    return await r.fulfill(status=206,body=data[start:end+1],headers={'Content-Type':'video/mp4' if is_video else 'image/webp','Accept-Ranges':'bytes','Content-Range':f'bytes {start}-{end}/{len(data)}'})
                return await r.fulfill(body=data,content_type='video/mp4' if is_video else 'image/webp')
            if u.path.startswith('/api/'):
                body={}
                if u.path=='/api/auth/session':body={'user':{'id':'fixture-owner','email':'fixture@example.invalid'},'expires':'2099-01-01'}
                if u.path=='/api/conversations':body=[]
                if u.path=='/api/client/instance':body={'instanceId':'fixture-instance'}
                return await r.fulfill(json=body)
            return await r.continue_()
        await context.route('**/*',route)
        try:
            await page.goto('http://127.0.0.1:22827/chat',wait_until='domcontentloaded',timeout=120000)
            await page.wait_for_function("document.querySelector('video')?.currentTime>0.15",timeout=20000)
            assert await page.locator('video').count()==1
            assert not await page.locator('video').evaluate('(v)=>v.loop')
            await page.wait_for_function("document.documentElement.dataset.v8WallpaperKind==='image'",timeout=10000)
            await page.get_by_role('button',name='暂停背景',exact=True).click()
            first=await page.screenshot()
            pixels=Image.open(io.BytesIO(first)).convert('RGB')
            assert all(abs(a-b)<=2 for a,b in zip(pixels.getpixel((5,500)),(210,40,70))),pixels.getpixel((5,500))
            await page.evaluate("document.documentElement.classList.remove('light');document.documentElement.classList.add('dark')")
            await page.wait_for_timeout(100)
            second=Image.open(io.BytesIO(await page.screenshot())).convert('RGB')
            assert pixels.getpixel((5,500))==second.getpixel((5,500))
            styles=await page.locator('.v8-sidebar-surface').first.evaluate('(e)=>{const s=getComputedStyle(e);return {background:s.backgroundColor,image:s.backgroundImage,blur:s.backdropFilter}}')
            assert styles=={'background':'rgba(0, 0, 0, 0)','image':'none','blur':'none'},styles
            await page.get_by_role('button',name='播放背景',exact=True).click()
            await page.wait_for_function("document.querySelector('video')?.currentTime>0.2",timeout=10000)
            await page.evaluate("window.__visibleCallback({visible:false})")
            await page.wait_for_timeout(100)
            before=await page.locator('video').evaluate('v=>v.currentTime')
            await page.wait_for_timeout(500)
            after=await page.locator('video').evaluate('v=>v.currentTime')
            assert abs(after-before)<0.05
            timeline=await page.evaluate('window.__timeline')
            assert any(item.get('event')=='ended' for item in timeline),timeline
            await page.screenshot(path=str(OUT/'background-dark.png'))
            result={'level':'ACTUAL_PROVIDER_SYNTHETIC_MEDIA','videoEnded':True,'singleVideo':True,'hiddenPaused':True,'lightDarkSidebarPixel':list(pixels.getpixel((5,500))),'sidebar':styles,'timeline':timeline,'errors':errors}
            (OUT/'background-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
            assert not errors,errors
        finally:
            (OUT/'background-debug.json').write_text(json.dumps({'errors':errors,'profile':await page.locator('#fixture-profile').text_content(),'text':await page.locator('body').inner_text(),'media':await page.locator('video').evaluate_all('(items)=>items.map(v=>({src:v.src,paused:v.paused,time:v.currentTime,ready:v.readyState,error:v.error?.code}))'),'timeline':await page.evaluate('window.__timeline')},ensure_ascii=False,indent=2),encoding='utf-8')
            await page.screenshot(path=str(OUT/'background-last.png'))
            await browser.close()
            (ROOT/'tmp/resident-web-ui/fixture-appearance.json').unlink(missing_ok=True)

asyncio.run(main())
