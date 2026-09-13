# Admin 首切片独立复验：ccbcd590

**未通过本轮交付验收。** 存在两处未保存输入丢失、节点菜单键盘/视口问题和圆角合同未消费；全局节点创建工作区关系的旧能力另有缺口。通过的局部检查不能抵消这些问题。

候选 `ccbcd59017759fd0ad8f5a43e3c8dd73860250f5`，为 `fffd09165da290b5abd8acc1071fac74225207b4` 加3文件菜单/相机增量。Admin负责人确认此时源码clean、22828冻结。实际为 **Next dev / Turbopack**，不是已构建生产版。源码通过experience工作树Git对象读取；没有改变任一生产文件、重启服务或使用真实Engine。

## 确定失败

| ID / 分项 | 可打败错误实现的操作 | 实际观察与首个偏离 | 后续验收 |
|---|---|---|---|
| A03 / 功能 P1 | 模型“调整”→详情“编辑”→modelId改为 `unsaved-independent-model`→Escape→重开 | 恢复成 `sample-chat`，未获得明确放弃选择。Dialog直接关闭并从旧model重新初始化表单 | Escape取消关闭时草稿保持，或明确放弃后才清；关闭/切目标/保存失败都同样检查 |
| G03-pending / 功能 P1 | 图谱提交 `submitted-v1`，外部请求保持pending；此时输入 `unsent-v2`；再释放v1回包 | 字段仍可编辑；v1结果回来后新输入清空。mutate成功分支无草稿版本比较就setTarget空 | pending锁定本次必要字段，或用版本保留新草稿；成功/失败/取消/重复回包不得吞v2 |
| G03-Escape / 便利性 P2 | 打开节点菜单，焦点放“管理全部关系”，按Escape | region仍为1。Escape只在Canvas处理，菜单内没有对应关闭/dirty/focus合同 | 干净关闭并返回焦点；有草稿先保留或明确放弃；不能借全局监听取消别的弹层 |
| G03-bounds / 便利性与视觉 P2 | A/shared新建关系成功后列表12条，1440×900 | menu `(x=1069.5,y=100.5,w=360,h=804)`，bottom904.5越界；footer底部裁切。固定section仍受父space-y间距与独立max-height组合影响 | 不滚动找操作；菜单header/footer都在viewport内，body滚动；390/低高度/长错误/字体放大复测 |
| V02 / 视觉 P2 | 明/暗主题读取真实供应商卡与管理/调整按钮computed style | 卡7px、管理7px、调整5px；合同为12px/8px。Card仍rounded-lg，Button small仍rounded-md，root14px使rem尺寸缩小；仅定义新token没有形成实际消费 | 原语/卡片消费同一语义token，明暗/不同字号下复测。不是要求Phone强用同尺寸 |

图谱菜单位置的失败发生在成功创建后GET回读新关系、B作用域数据未变等断言之后。该行总体失败来自可达性，**不表示合成API写入/回读路径失败**。这一层证明真实前端对外部边界的处理，没有证明Engine持久化、后端scope权限或数据库事务。

全局节点能力缺口（G05，SOURCE + 当前UI观察）：原 `53fd9ac` GraphViewer以 `subject: selectedNode.id`、`workspaceKey: selectedWorkspaceKey` 创建关系；读图包含global背景，因此全局实体可作为指定工作区关系的端点。`ccbcd590` 用 `scopeKind==='global'` 提前退出mutation并隐藏全部新建入口。此前G03-global-readonly通过只说明界面没有直接全局写按钮，**不能代表该旧功能保留**。正确目标是从global-only节点明确选择允许的workspace建立本地关系；global relation仍不可删除。下一版须用global-only实体（不在workspace预览节点内）重放，不能只用三个cluster都有的 `shared`。

## 已通过的局部范围

- System保存栏在1440×900、1024×768、390×844，首次不滚动即可操作；坐标见JSON。
- 有状态合成配置API：更改WS字段，未知嵌套/false/0/空数组及secret占位 `***` 原样回传；重新加载读取新值。只证明未修改占位与未知字段保留，不证明真实secret轮换。
- 配置写503：草稿保留、失败提示在视口内、合成已保存数据不变。
- 配置读503：页面保留标题、主内容spinner归零；点击重试后恢复编辑。
- 390px导航抽屉→模型页可达，点击后抽屉关闭。
- Help键盘focus可打开、Escape可消除；Chromium触摸模拟下首次tap观察到expanded=true与一个tooltip。源码疑似focus/toggle冲突**本次没有复现**，不能因此断言全设备通过或按猜测修补。
- `chat-runtime?tab=subagents` 显示合成专家；只验证query直达，没有测试browser back，不能将此项名字解释成完整back合同。
- 图谱失败保留目标；有状态fixture创建关系后GET返回新增关系并显示；B cluster不变；全局菜单不显示直接写动作。这不替代前述G05功能保留。
- 明/暗与390px单条合成对象布局无document横向overflow，实际settings页宽1040px、Admin topbar token48px；motion取到 .1s/.14s/.18s。V02独立失败，不能据此说视觉整体通过。

## 执行、证据及判据校正

最终合并15个局部检查：10 PASS、5 FAIL、0未解决HARNESS_ERROR。数量只便于追踪，不是总分。第一完整run109个fixture API请求、5次合成写、0pageerror；定向修正run11个请求、0写、0pageerror。共47个已加载JS资源SHA256辅助记录，不把资源hash冒称应用自行证明commit。实际版本根据Git对象、负责人的freeze声明与此资源摘要绑定。

宿主Windows，Node22.22.0，浏览器使用已安装Chrome **153.0.8010.36**，不是首轮baseline的Chromium140。因此本轮没有做跨版本性能比较，也未重跑30样本宣称速度提升。实际交互包括reduced-motion、一个独立touch emulation context、明暗与窄屏；最后的root-font 14→28只检查布局，不是真实浏览器200%缩放。持续公转/hover/空白镜头/后台资源、真实Phone、生产build、Preview、全站未覆盖状态仍未验证。

使用作者冻结fixture的**数据形状**，不运行其断言作为通过。独立harness将config/graph改为有状态外部边界，加入503与可控pending；生成/更新/查询均在测试内存，不使用真实Engine。所有未声明API显式503或409，外域abort；本机合成auth走22828原入口。没有关闭/重启作者进程。交互结束后已通知Admin解除冻结。

本轮修正了三处harness定位，保留原用户行为oracle：首次summary实际是Sidebar导航，改用具名“服务联通”；模型调整当前先开详情，按实际“编辑”入口进入表单；错误alert缩到Admin内容，避免匹配Next route announcer。修正后相应边界重新执行，前两次误定位不计产品失败。模型多层dialog便捷性仍需实施端改进。

完整可提交结果：`reports/admin-ccbcd590-review.json`；脚本：`verify-admin-candidate.mjs`；截图与原始合成证据：experience工作树 `tmp/experience-9131/admin-candidate-ccbcd590/`。重点图为 `A03-model-dirty-escape.png`、`G03-write-failure-scope-and-readback.png`、`G03-pending-edit-retention.png` 及 `visual-*.png`，均只含合成数据。

```powershell
node scripts/experience/9131/verify-admin-candidate.mjs --candidate ccbcd59017759fd0ad8f5a43e3c8dd73860250f5
node scripts/experience/9131/verify-admin-candidate.mjs --candidate ccbcd59017759fd0ad8f5a43e3c8dd73860250f5 --only A07,V02
```

复测必须先由负责人与协调给出**实际服务的新commit/fixture与冻结窗口**；脚本读取指定Git fixture并不会自动让22828切换版本。节点/模型入口变化可改适配层，但不改保存、身份和未发内容保留判据。没有push/tag/CI/生产代码提交；首轮7646ea01的Phone/Extensions七个功能反例和安装footer阻断项仍未由此次Admin局部复验关闭。
