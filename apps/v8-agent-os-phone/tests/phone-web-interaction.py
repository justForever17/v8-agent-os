"""Explicit synthetic Phone Web-runtime acceptance; never a physical-phone benchmark.
Start tests/fixtures/phone-api-fixture.cjs on 22836 and Expo web on 22826 first.
"""
import json
from pathlib import Path
import statistics
import sys
import time
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding="utf-8")
OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "artifacts"
OUTPUT.mkdir(parents=True, exist_ok=True)
SEED = r"""() => {
 if(localStorage.getItem('fixture-seeded')) return;
 const profiles=['A','B'].map(id=>({id,label:'Laboratory '+id,instanceId:'fixture-'+id,principalId:'fixture-owner',
  adminBaseUrl:'http://127.0.0.1:22836/'+id,adminUrls:['http://127.0.0.1:22836/'+id],credentialRef:'synthetic-'+id,
  user:{id:'fixture-owner',login:'fixture',email:'fixture@invalid',name:'Test owner',role:'ADMIN'},lastUsedAt:'2026-09-13T00:00:00Z'}));
 localStorage.setItem('v8.phone.profiles.v2',JSON.stringify(profiles));
 localStorage.setItem('v8.phone.activeAdminConnectionProfileId','A');
 for(const profile of profiles) {
  const id=profile.id,authority=JSON.stringify(['fixture-'+id,'fixture-owner',id]);
  localStorage.setItem('synthetic-'+id,JSON.stringify({accessToken:'synthetic-access-'+id,refreshToken:'synthetic-refresh-'+id}));
  localStorage.setItem('v8.phone.view.v2.'+authority,JSON.stringify({conversationId:'session-1',draftId:'new-'+id}));
  for(const session of ['session-1','session-2']) {
   const key=JSON.stringify([authority,'fixture-'+id,session]);
   localStorage.setItem('v8.phone.draft.v2.'+key,JSON.stringify({revision:1,composerRevision:1,values:{input:id+' '+session+' draft',selection:{start:1,end:3},files:[],plugins:[]}}));
  }
 }
 localStorage.setItem('fixture-seeded','1');
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page.add_init_script("(" + SEED + ")()")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://localhost:22826/chat", wait_until="domcontentloaded", timeout=60000)
    editor = page.locator("textarea").first
    expect(editor).to_have_value("A session-1 draft", timeout=30000)
    editor.fill("中文未发送 A v2")

    def navigate(label):
        page.get_by_role("button", name="导航", exact=True).click()
        page.get_by_role("button", name=label, exact=True).last.click()

    navigate("工作区")
    page.get_by_text("A task 1", exact=True).click()
    expect(editor).to_have_value("中文未发送 A v2")
    navigate("工作区")
    page.get_by_text("A task 2", exact=True).click()
    expect(editor).to_have_value("A session-2 draft")
    editor.fill("session 2 retained")
    navigate("工作区")
    page.get_by_text("A task 1", exact=True).click()
    expect(editor).to_have_value("中文未发送 A v2")

    navigate("连接与设备")
    page.get_by_role("tab", name="当前连接的 Supervisor", exact=True).click()
    page.get_by_text("Supervisor 000", exact=True).wait_for()
    page.get_by_role("textbox", name="搜索设备", exact=True).fill("Supervisor 099")
    page.get_by_text("Supervisor 099", exact=True).wait_for()
    page.screenshot(path=str(OUTPUT / "phone-peers-search.png"), full_page=True)
    page.get_by_role("tab", name="已配对连接", exact=True).click()
    page.get_by_role("button", name="Laboratory B", exact=True).locator("..").get_by_role("button", name="切换", exact=True).click()
    expect(editor).to_have_value("B session-1 draft")
    editor.fill("B retained independently")
    navigate("连接与设备")
    page.get_by_role("button", name="Laboratory A", exact=True).locator("..").get_by_role("button", name="切换", exact=True).click()
    expect(editor).to_have_value("中文未发送 A v2")

    samples = []
    for _ in range(30):
        navigate("连接与设备")
        page.get_by_role("button", name="Laboratory A", exact=True).wait_for()
        start = time.perf_counter()
        page.get_by_role("button", name="V8 Agent OS", exact=True).click()
        expect(editor).to_have_value("中文未发送 A v2")
        samples.append((time.perf_counter() - start) * 1000)
    page.screenshot(path=str(OUTPUT / "phone-chat-retained.png"), full_page=True)
    page.reload(wait_until="domcontentloaded")
    expect(editor).to_have_value("中文未发送 A v2", timeout=30000)
    assert not errors, errors
    report = {"platform": "Chromium Phone Web runtime, synthetic API", "physicalPhone": False,
              "samples": len(samples), "returnMsMedian": statistics.median(samples), "returnMsP95": sorted(samples)[28],
              "samplesMs": samples, "checks": ["same-session no-op", "A session1-session2-session1", "profile A-B-A", "peer search", "30 navigation cycles", "reload draft restore"],
              "note": "Playwright end-to-end timings include automation overhead; not native JS/UI frame timings."}
    (OUTPUT / "phone-web-interaction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samplesMs"}, ensure_ascii=False))
    browser.close()
