# c03b7ebb Admin 与 Extensions 组合生产验收

**同一生产包的 shell、关键操作和 graph 回归通过，但390px扩展管理页的“刷新扩展”被裁切，组合验收仍有一项操作可达性阻断。** 已通知root接手窄CSS修复并提前释放冻结。没有用其它通过项抵消这个故障。

候选 `c03b7ebb72dfa0a80bb072ab56e5f638669e963d`，22938，BUILD_ID `LwPhR5Q4--pMNfvE5JoWr`，managed start standalone PID27556/run8a7a6ee0-e584-4dbd-8311-2355b316a1a8。root确认构建前后产品源码一致并冻结；本lane从Git独立核对Admin消费product-ui0.0.16/realtime0.0.46，浏览器为Chrome153。archive/全部consumer integrity来自协调验证，不改写为本lane独立重跑。

首次设置只在新 `integration-ui-state` 创建指定公开合成owner。除精确auth/bootstrap路径外，所有业务API走独立合成边界；未声明写409、未声明读503、外域abort。Engine目标127.0.0.1:1；未访问root模型live专用22928。两脚本新增显式`--url`，组合端口仅接受22938，保留原lane22828/22825。没有读用户状态或真实秘密。

| 检查 | 实际结果与限制 |
|---|---|
| 系统设置保存与恢复 | 浅/深×1440/1024/390共6组合，SaveBar可视；合成POST保存后reload、unknown/false/0/占位保留；503不清未保存输入；窄屏导航可打开并关闭 |
| 模型页与共享shell | 两主题×桌面/窄屏；48px topbar、1040px内容上限、实际provider12px/按钮8px；同包Admin/商店/管理页2个已交付CSS的SHA256完全一致。文字放大仅root font，不是浏览器200%zoom |
| 扩展关键操作 | 手动来源与失败不切源、旧query迟到、abort失效后MCP仍提交B/candidateB、A完成不清B pending、结构错误可读、stdio空参数/首尾空格及unknown字段精确保存均通过 |
| 扩展详情footer | 100段说明，浅/深×1440/390；操作与“已是此版本”结果始终可视。仅合成安装receipt，不证明真实CLI安装 |
| 商店→管理实际导航 | 两主题/两尺寸，topbar几何、颜色、字体与tokens一致；移动导航点选后关闭。这个判据只证明shell一致，不能证明每个页面内动作可达 |
| X15扩展管理头部 | 390×844浅/深均FAIL：“刷新扩展”x371、宽105、右端476，祖先clip右边390，86px裁切且中心在屏幕外；状态文字被挤成竖列。1440两个主题均可达 |
| graph写入与权限UI | 失败保留输入、正确workspace回读、pending输入保护、global直接只读、global-only实体可显式选择workspace创建关系均通过。后端权限由合成响应控制，未声称Engine权限验收 |
| graph交互与资源 | 深色/390菜单不越界；关闭焦点回稳定Workspace A按钮；镜头有15个中间帧、约367ms归位；中心固定、hover约1.45倍、pause/reduced不持续绘制；20次卸载Canvas/RAF/Resize/Intersection归零，Mutation1稳定 |

X15来自实际截图复核。document根的scrollWidth没有增加，因为外层overflow-hidden已裁切，因此原根overflow判据不能杀死这个错误。新增按每个header按钮与viewport及祖先clip交集的判据，在同一冻结产物上复现两主题故障。源码 `extensions/page.tsx` 向AdminPageHeader传入的内层actions是`flex items-center gap-3`；Header外层wrap不能拆这个内层单组。此前未验证此页完整窄屏动作行，不能断言故障由这次整合新引入。

X05新增独立准备阶段第一次遇到摘要和footer同时有“已是此版本”，严格locator匹配两项导致HARNESS_ERROR；准备就绪等待改为第一项，打开说明后的原按钮/结果可视判据不变，独立补跑4组合全部通过。原记录保留，不伪称首跑全绿。

五个有效/保留run的请求数为37、155、83、15、25；各run均0 uncaught pageerror。原始汇总为Admin6PASS、graph9PASS、Extensions主run7PASS/1已修harness、footer补跑1PASS、X15独立1FAIL。完整数据/截图hash见 `reports/integration-c03b7ebb-review.json`；同目录 `summarize-integration-review.py`只归约合成输出并核对CSS一致，完整原记录仍在worktree tmp。所有浏览器已关闭，冻结已提前释放。

```powershell
node scripts/experience/9131/verify-admin-candidate.mjs --candidate c03b7ebb72dfa0a80bb072ab56e5f638669e963d --runtime production --url http://127.0.0.1:22938 --build-id LwPhR5Q4--pMNfvE5JoWr --only A01,A02,A03-config,A06,V01,V02
node scripts/experience/9131/verify-admin-candidate.mjs --candidate c03b7ebb72dfa0a80bb072ab56e5f638669e963d --runtime production --url http://127.0.0.1:22938 --build-id LwPhR5Q4--pMNfvE5JoWr --only G03-write-failure,G03-global-readonly,G03-pending,G03-keyboard,G03-menu-focus,G02,G04,G05
python -X utf8 scripts/experience/9131/verify-extensions-candidate.py --candidate c03b7ebb72dfa0a80bb072ab56e5f638669e963d --url http://127.0.0.1:22938 --fixture-account admin --build-id LwPhR5Q4--pMNfvE5JoWr --out tmp/experience-9131/extensions-candidate-c03b7ebb-header --only X15
```

尚待新冻结CSS候选对X15及受影响header/save/footer定向复验；不重跑全部40routes保存。Canvas回调77样本median0.2ms/P950.3ms只是单个受观察器包装的片段，不是FPS/INP或改前性能对比；后台是document.hidden信号注入，没有实际锁屏证明。Phone新APK独立复验另报，Web/Shell、真实模型live、安装包/最终发布不由本报告批准。无产品代码修改、push或tag。
