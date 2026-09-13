# Admin 生产候选独立复验：d951d9cc

**原5项失败与G05全局节点能力在本次生产UI边界复验通过；Admin整体仍未放行。** 新增确定失败为节点菜单焦点恢复、空白返回镜头过渡；全页观察另外发现安全页读取失败仍只显示spinner，以及治理页双标题/未翻译错误key。

产品构建提交 `d951d9cc2f1feb893a76160ece2992b798b12c79`；交接HEAD `b7ef1af26765dddc699573de904f26f0c6a48b19` 只增加测试（通过Git diff独立核对）。作者确认Next16.2.10生产Webpack构建、standalone在22828启动并冻结。实际加载资源hash随报告保存；不把hash当应用自行证明版本。shared tgz仍product-ui0.0.15/session-realtime0.0.45，Admin实测的是fallback token消费，未覆盖协调最终pack/全部消费者一致性。

## 原失败项复验

| 原项 | 本次独立结果 |
|---|---|
| 模型Escape丢草稿 | PASS：修改modelId，Escape触发明确放弃询问；拒绝后保留 `unsaved-independent-model`。新“调整”直接进入编辑器，harness只适配实际入口 |
| 图谱pending v1吞v2 | PASS：受控挂起请求期间目标字段不可编辑；v1返回后不可能丢掉期间未被接收的v2。结论是必要字段锁定，不伪称v2持久化通过 |
| 节点菜单Escape无效 | PASS：菜单关闭。焦点恢复单列新FAIL，不能以关闭成功抵消 |
| 菜单底部越界 | PASS：1440×900与390×844均top64/h560；1024×600时h520，完整footer在视口内；明暗截图确认 |
| 圆角实际未消费12/8 | PASS：供应商卡12px、管理/调整按钮8px，明暗/窄屏一致；root字体放大时仍是同像素语义。没有把root放大称完整200%浏览器缩放 |
| G05 global-only→workspace | PASS：从只在global图里的 `global-node-1` 建关系，未选择workspace前按钮禁用；显式选B后实际合成POST subject正确/workspaceKey=b，GET回读可见；global和A数据均未变 |

配置未知嵌套/false/0/占位往返、503留草稿、读失败重试、三个尺寸SaveBar、窄屏导航、Help focus/Escape与实际touch emulation、subagent query入口复测亦通过。真实Engine写入、真实secret轮换与后端权限不在此层。

## 新增失败与页面发现

| 分项 | 输入→实际结果 | 源码因果与处理边界 |
|---|---|---|
| 便利性 P2：焦点丢失 | 键盘选择Workspace A→shared→菜单关闭按钮→Escape；1440/390/低高度均回到BODY | background先focus节点trigger，再setSelected(null)使节点按钮卸载；应将焦点交给仍存在的cluster列表或Canvas，待DOM稳定后验证 |
| 交互 P2：空白返回突跳 | 实际Canvas选中A半径149.79，点击空白后首帧约7.9ms已是61.52；后续12帧都61.52；中间半径帧0 | selected变化重建effect，resize无条件把camera赋overview；应保留相机/过渡状态与唯一时钟；用真实帧序列复验，不能靠多加CSS过渡 |
| 可用性 P2：安全页读失败无恢复 | S01向未声明 `/api/safety/dashboard?limit=80` 回503；出现uncaught error；主区无标题/操作，只剩spinner | loadConfig Promise.all无catch，finally设loading=false后 `loading || !envelope`仍spinner。需明确失败和retry；不把503说成模型或安全能力不存在 |
| 视觉/文案 P2：治理页重复标题 | `/admin/runtime-governance`同时两个h1：运行治理、运行时治理工作台；失败toast显示 `components.runtime.RuntimeGovernanceWorkbench...` key | 页面和嵌入workbench双标题owner；错误文案未完整解析。截图 `route-sweep-21.png` |

Provider详情没有标题/控件的观察暂属fixture覆盖缺口（其细分请求未全提供），不记产品失败。全页观察是渲染/状态候选，未对每个配置域实际保存。

## 动效、资源和视觉证据

- 真实生产Canvas绘制：可见未暂停时1.8s内54帧；global中心固定，workspace位置变化；该观察样本星团圆最小间隙30.64px。没有宣称完整公转周期/100工作区几何验收。
- 暂停后600ms为0新增帧；hover拉近约1.45倍，停靠后350ms为0新增帧；移出后恢复。reduced motion持续帧0。
- headless Chrome切另一个tab不会变document.hidden。以**合成hidden/visibilitychange信号注入**验证处理函数停止/恢复，明确不是实际后台/锁屏实测。
- 实际graph→preferences→graph切换20轮，每次离开后Canvas0、RAF0、ResizeObserver0、IntersectionObserver0、MutationObserver1恒定；首尾离页观察无新增graph请求与绘制。只说明所测资源回落，不等于进程堆/内存/所有后台活动已全面测量。
- Canvas绘制回调稳定段73样本，median约0.20ms、P95约0.60ms；带透明计时包装开销，仅本机稳定段，不是端到端FPS/INP或与旧版比较。Chrome153.0.8010.36不同于首轮Chromium140，禁止比较百分比。
- 模型明暗、390px和节点菜单明暗/390px/600px高度实际截图已检查：卡片/按钮圆角一致，菜单表面/文字可读；节点菜单现在位于视口中。纯布局通过不覆盖键盘焦点失败。

## 执行范围与复测

汇总19项PASS、2项FAIL、1项OBSERVED、0未解决HARNESS_ERROR；OBSERVED内含29个非Extensions dashboard路由＋11 Memory tab，共40张生产快照。共有三次run：完整原项＋新增资源、定向菜单/焦点/镜头、全页观察。逐次请求/合成写/错误统计在 `reports/admin-d951d9cc-review.json`。S01有2条pageerror，其余两run无pageerror；所有未声明API均显式503/409，外域abort，真实交互只在合成auth和fixture数据边界。未写真实Engine、未改生产文件。

`graph-observation.mjs`透明包装Canvas clear/arc和RAF、原生Observer并保留原调用，记录绘制与生命周期；没有替换产品渲染算法或读取React内部状态。安装/运行服务器仍由Admin负责人拥有。node close的真实label为“关闭节点菜单”，一次错label的harness timeout已修，定向重跑后不计产品FAIL。

```powershell
node scripts/experience/9131/verify-admin-candidate.mjs --candidate d951d9cc2f1feb893a76160ece2992b798b12c79 --runtime production
node scripts/experience/9131/verify-admin-candidate.mjs --candidate d951d9cc2f1feb893a76160ece2992b798b12c79 --runtime production --only G03-keyboard,G03-menu-focus,G02
node scripts/experience/9131/verify-admin-candidate.mjs --candidate d951d9cc2f1feb893a76160ece2992b798b12c79 --runtime production --only S01
```

脚本只应连接交接的实际版本；`--candidate`不负责切服务器。截图和原始合成输出在experience工作树 `tmp/experience-9131/admin-candidate-d951d9cc/`。此轮浏览器已关闭并通知Admin解除冻结。等待明确修正commit再验新增问题；Extensions09dfcc3c将单独复验。无push/tag/共享Preview，Phone实体仍未验证。
