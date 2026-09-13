# Experience 首轮基线（2026-09-13）

结论：**首轮验收材料已建立，9.13.1 尚未获得独立体验验收通过。** 本轮没有生产代码修改。实施候选尚未合入；以下都是 `53fd9aceb4b9f33694032e6bfa1162cb1c7435c4` 的基线结果。

## 交付与事实面

- `acceptance-matrix.md`：全部 Admin 路由能力去向、Web/Phone 用户入口、子视图、通用浮层；功能/便利性/视觉/性能各自判据及 49 项行为反例（含端口、身份、保存、dirty、失败/取消、资源与性能规格）。
- `reports/surface-matrix.json`：Admin 34 / Web 7 / Phone 13 个源码入口，各带文件 hash；所有四分项默认 NOT_RUN，不把路由枚举计成通过。
- `reports/admin-source-summary.json`：Admin 121 TSX、20 一级导航项、1219 静态 control sites，包含 dynamic import 与父 layout 闭包。它不是实际可见/可操作控件数。
- `fixtures.py`：合成 input/queue/profile/config/source/README/规模与故障时序；不读用户状态，安装包与真实 runtime 验证仍需候选 API 适配及受治理入口。

共享视觉合同已记录为协调确定的 DESIGN_TARGET：100/140/180ms、ease-out(.22,1,.36,1)、12px panel / 8px control / 1040px settings、Admin 48px topbar、非滚动末行 AdminSaveBar 复用原 handler。不能将目标值写成本基线已达标。

## 独立故障反例

| ID | 层级 | 实际结果 | 首个偏离与影响 |
|---|---|---|---|
| P01 | 生产 AST 回调 + 合成 state setter | 同 `session-1` 重选，未发文本→空，附件→[]，selection→0/0，插件→[] | `handleSelectConversation` 在判断同目标之前清 composer；现有动效测试不能发现 |
| P07 | 生产 AST 函数 + Android SecureStore reject double | caller resolved | `setStoredValue` catch 后不传播失败；上层可能误认 profile 保存成功；没有真实 native 存储实测 |
| X02 | 生产 openMcpDetail 与 installMcp 回调 + deferred fetch | selected=B，detail=A，实际提取 install 请求 body.id=A | A 慢回包覆盖 B，再从错 detail 形成安装意图；未调用真实安装 |
| X03 | 生产 loadStore 回调 + deferred fetch | current 搜索后被 old 覆盖 | 旧请求提交无当前 query 校验 |
| X04 | 生产 installSkill 回调 + 两个 pending fetch | B 仍在途但 A finally 把 busy 清空 | 一个 busyId 不能表示同时安装 A/B |
| P05-message | 生产 CREATE/INSERT SQL + 内存 SQLite | 同 session/message ID 写 A 再 B，库中只剩 ONLY-B | 数据表不能同时表达两个身份的同 ID 记录；不是实际多设备隐私事件的证明 |
| P05-cursor | 同上 | 只剩 cursor-B | sessionId 单键覆盖；native service 层未在本轮执行 |

`boundary-baseline.mjs` 5 FAIL、`sql-baseline.py` 2 FAIL 都是有意保留的产品基线失败，命令退出 1。没有改判据以获取全绿。源码形态不匹配的 HARNESS_ERROR 必须与产品 FAIL 分开；新候选需要适配新 owner 后保持同样的输入与 oracle。

既有 `cross-client-motion-behavior.test.mjs` 与 `motion-interaction-behavior.test.mjs` 共 10 PASS；仅证明被测纯函数的 hidden controls、跟尾/拖动、frame 合并、reduced-motion 策略等。Node 提示 typeless package 导致 ESM reparsing，非用例失败。

## 浏览器与便利性/视觉基线

Windows 11 10.0.26200、Node 22.22.0、Python 3.12.10、Python Playwright 1.55.0、Chromium 140.0.7339.16、实际 Next 16.2.10，Admin dev Webpack；1440×900/DPR1，减少动态开启。另做 390×844 resize，**未启用 touch emulation，不是真手机**。

真实 Admin 源码在 22824 启动。每次新建 `tmp/experience-9131/.../isolated-home-*`，child USERPROFILE 与 V8_AGENT_OS_HOME 同时隔离；配置 Engine HTTP/WS 为 loopback discard port 9，无 Engine 进程。浏览器外域全部 abort；未声明 API GET 记录并 503，非允许写请求 409；`bg_processes` 两入口同样被截断。仅合成 owner bootstrap/登录写入本次隔离状态，经原 managed auth 入口生成测试密钥。没有读取真实配置、token、数据库或访问真实 9530。

全页 sweep 覆盖 34 路由＋11 Memory query tabs，共 45 次导航，另长详情/Escape/窄屏 3 条，总计 48 观察记录。216 次 fixture API 请求，12 条 uncaught synthetic-unavailable 错误；此版只累计这些 pageerror，未逐条归因具体页面。全记录为合成错误/部分数据场景，不是完整正常配置验收。

| 分项 | 观察结果 | 证据与限制 |
|---|---|---|
| 便利性 X05 | 100 段 README 详情打开后安装按钮 y=5079.5、h=35；viewport h=900；dialog y=54、h=792，scrollHeight=5081 | 浏览器真实组件已复现 footer 初始不可达；不是仅从 CSS 猜测。`ui-all/long-detail.png`、JSON 矩形 |
| 便利性 A07 | 17 个导航快照仍含 spinner，包括 system-base、rpa、projects、safety、cron、stability 等 | 单次 post-networkidle 观察，不据此宣称永久 spinner；源码候选与候选版须用完成响应后的恢复状态复验 |
| 便利性 | 长详情 Escape 后 dialog 数量0 | 只验证干净详情关闭；dirty guard、focus restore、触摸仍未验 |
| 视觉 | 基线 root 14px；侧栏 280px、内容1518px/可视约798px；窄屏无侧栏浏览入口 | 真实截图/矩形；共享 topbar 基线约35px，不等于新目标48px已实现；不是用户研究 |
| 视觉 | 390px focused 截图可读，未发现 document 横向 overflow | 不等于每个控件触屏/200%文字/暗色通过；dev工具角标不是生产产品元素 |
| 视觉/动效 | 首次全页 sweep 的窄屏截图可能处于退出动画中；focused 复测等 dialog hidden 后截图 | 修复测试同步后以 `ui-perf/narrow.png` 为有效窄屏图；不把暂态灰层归为主题故障 |

截图与完整合成记录留在 checkout 的 `tmp/experience-9131/ui-all/` 与 `ui-perf/`（Git忽略），可按脚本重建。可提交的去敏摘要在 `reports/admin-browser-baseline.json`。控件计数包含已参与布局但在滚动区外的元素，不能称为“全部可见控件”。

## 性能

固定合成100段 README、重复打开/关闭，先预热，再从真实 click capture 时刻到内容挂载并经历两帧计时，n=30：median **107.35ms**，P95 **142.3ms**。全部原始 timing 数字见 `reports/admin-performance-baseline.json`。该 focused run 38 次 fixture API 请求，0 pageerror。

它包括 fixture 请求及测试同步，仅可与同机/相同 fixture/浏览器/dev Webpack/减少动态状态的候选比较；不是生产 INP，不是 Phone JS/UI 帧，不说明模型 TTFT，也不能直接称为“提升”。并行构建带来的宿主负载需在候选对比时控制；目前没有可据以判定10%回归的候选样本。

## 环境与覆盖缺口

开发 baseline、test map、test quality 三个共享脚本均因 `.git.is_dir()` 拒绝 worktree 的 `.git` 文件。没有改生产、没有 git init；使用 `git rev-parse/status/describe/ls-files` 与本lane collector取得同等只读基线。HEAD/分支如上；latest tag `v8-os-v2026.09.12.1`；无 upstream。Git跟踪测试文件计数（`tests/` 下含 `.test.`）：Admin40/Web37/Phone8；CI workflows 为 ci/desktop-preview/phone-build/release，只是入口地图。

默认 dev Turbopack 在外部 node_modules junction 上报 filesystem-root error；改由 test-only launcher 复用 managed-auth 入口并选 Webpack。Next缓存仅写本worktree `.next`，依赖只读复用，没有更改主仓目录或共享构建输出。首次观察脚本误点不可点击标题导致详情 wait timeout；修正为实际“展开详情”按钮后全页与focused两次均完整完成，不计那次 harness error 为产品缺陷。

Phone ADB 已探测：没有连接的物理设备。协调确认有 Pixel_8 / Small_Phone AVD，可由 Phone lane 分配测试条件；本轮未启动模拟器。peer 验收限既有授权 link 与本机 neighbor 会话；独立 profile 可切完整本机会话，不当通用远端会话隧道。

仍未验证：整合候选、正常配置读写/secret往返、真实安装/收据、所有主题/200%文字/focus/touch、星系完整CRUD/运动/权限、Web/Shell往返/真实PTY/媒体像素、Phone模拟器与实体Android/iPhone、生产构建/Preview/安装包/live、最终CI。以上不能因本轮材料完整而记通过。

## 复测命令

从分配的 experience worktree 运行（实际依赖匹配 package/lock）：

```powershell
python scripts/experience/9131/collect.py --repo . --out scripts/experience/9131/reports/surface-matrix.json
python scripts/experience/9131/fixtures.py --out tmp/experience-9131/fixtures.json
node scripts/experience/9131/boundary-baseline.mjs --repo . --out scripts/experience/9131/reports/boundary-baseline.json
python scripts/experience/9131/sql-baseline.py --repo . --out scripts/experience/9131/reports/sql-baseline.json
node --test apps/v8-agent-os-web/tests/cross-client-motion-behavior.test.mjs apps/v8-agent-os-phone/tests/motion-interaction-behavior.test.mjs
python scripts/experience/9131/observe_admin.py --repo . --out tmp/experience-9131/ui-all --all-admin
python scripts/experience/9131/observe_admin.py --repo . --out tmp/experience-9131/ui-perf --routes /admin/extensions/store --warm-samples 30
python scripts/experience/9131/summarize_observations.py --input tmp/experience-9131/ui-all/observations.json --out scripts/experience/9131/reports/admin-browser-baseline.json
python scripts/experience/9131/summarize_observations.py --input tmp/experience-9131/ui-perf/observations.json --out scripts/experience/9131/reports/admin-performance-baseline.json
```

脚本完成后精确结束自身启动的进程树；22824 已无监听。未调用共享 Preview/9527/9528/9530，也未操作样例21829。没有 push/tag/CI；仅提交本lane验收文件。协调送来候选后继续独立复验，不以作者自报将本基线FAIL改成PASS。
