# Extensions 生产候选独立复验：09dfcc3c

**原来源/乱序目标/并发pending/footer阻断在生产UI边界复验通过；完整体验验收仍有缺口。** 当前版本结构化来源错误显示 `[object Object]`；stdio参数表单不修改往返会丢边界空格和空参数。前者协调已修在integration `08e74283`，尚未在此冻结生产实例复验；后者已向Extensions负责人报告。

候选 `09dfcc3cd231d1717d3e712ad2270750c4c1fe31`，22825为生产standalone，作者确认最终产品源码一致/工作树clean并冻结。构建在commit前，不能声称构建内嵌该commit。作者交接BUILD_ID `thJVYuOhK1Mqvf23zCP0d`，实际启动模式/源码与冻结由交接记录绑定。此实例没有新Engine进程，所有业务API使用独立fixture；auth仅走公开合成账户的22825原入口。脚本拒绝外域，未声明API503，未执行真实安装/命令/远端部署。

## 通过的原用户行为

| 场景 | 独立输入与观察 |
|---|---|
| X01默认/手动切源 | 新浏览器上下文默认国际；手动国内后reload保留；502不自动切源；手动切回国际可继续 |
| X03搜索乱序 | old请求明确挂起→new已呈现→old再返回，当前仍new；使用可控事件，不靠请求延迟概率 |
| X02 MCP目标 | A详情明确挂起，关闭开B，B先就绪，再释放A；**刻意让fetch的AbortSignal失效**，实际提交仍`author/beta`和`author/beta-candidate`；结果为配置保存待连接，不冒称健康 |
| X04并发安装 | A安装请求pending时打开B并安装；A回包后B仍禁用且详情未变；B只提交一次，B回包显示“已是此版本”，不伪报新增安装 |
| X05长文与反馈 | 合成100段README，明色1440×900与暗色390×844，安装/继续操作按钮和receipt状态均在viewport内，查看说明时状态也可见 |
| X09局部失败 | health503后已加载管理区仍能编辑合成导入文本，错误说明可见；没有提交该命令 |
| X11来源身份 | 国际beta收据不能让同ID国内beta显示已安装；国内reload只有1个domestic目录请求，没有international目录请求。不是整个最终整合包preload/preflight验收 |

本文关闭的是这些场景在**真实前端对合成API边界**上的失败；安装收据/ZIP/权限/业务服务可达的真正后端真相仍需协调分层核验。没有重新执行或冒领作者的真实ZIP、Node/Python安装证据。

## 新失败及首个偏离

| ID / 分项 | 反例与实际结果 | 归属与状态 |
|---|---|---|
| X10 / 便利性 P2 | GET目录返回502 `{detail:{message:"Independent fixture source offline"}}`；role=alert文本为 `[object Object]` | shared `admin-client-cache.ts`在进入store catch前对detail对象String化。协调08e74283已修源码并自测，但09dfcc3c本生产UI仍FAIL，等明确新实例复验 |
| F03 / 功能保真 P2 | 真实 `mcpFormFromServerConfig`→`buildMcpFormPayload`，输入stdio args `['--label','  spaced value  ','']`，未编辑往返变`['--label','spaced value']` | `formatMcpArgsText`去空值，`parseMcpArgs`trim/filter再损失信息。**53fd9ac已存在的继承缺陷**，不是本候选新引入；但不满足本轮精确配置往返要求。应保留未改字段字节语义，改动/JSON编辑另遵循明确合同 |

表单层另外独立执行2个PASS：HTTP endpointRef/headerRefs和unknown嵌套/false/0/空数组不变；显式清endpoint/header引用并替换为新合成endpoint，unknown与原base对象保留。它们不等于完整BFF凭据存储/CAS/真实连接验收。

## 性能、视觉和限制

Chrome153.0.8010.36生产UI，warm详情打开30样本，click→配置详情可操作→两帧：median118.15ms、P95159.8ms；源数据含100段说明但lazy文档没有打开，不能与首轮“长README立即渲染”的Chromium140/dev基线比较，也不是模型/网络/Phone性能结论。

明暗截图已独立查看：正文滚动、header/footer固定，390窄屏操作改为通栏且状态在底部仍可见。该Extensions分支仍带旧Admin shared shell，最终与新Admin导航/字体/圆角/错误owner合并后必须再验统一性；不能从本分支截图宣称全局一致性完成。触屏软键盘/200%缩放、更多安装条件/超长名称/大量列表未在此切片穷尽。

浏览器最终合并9项检查：8PASS/1FAIL；表单层2PASS/1FAIL，0未解决HARNESS_ERROR。分别报告而不加权抵消。三个browser run请求43/15/36，合成提交3/2/0，pageerror均0。0pageerror不能覆盖X10显示错误和F03内容损失。

首轮harness遇到错误文案不匹配后未切回国际，导致后续以国际标签查国内列表的定位失败；已将“源未切换”和“错误可读”分成独立断言，并用真实手动按钮规范每项前置状态。一次B重复次数错误地包含前面的MCP请求（相同payload也有skillId），改为当前Skills事务的提交范围后独立定向重跑；原B pending/目标/幂等oracle未削弱。这些修正不计产品回归。

## 可重放材料

- 浏览器：`verify-extensions-candidate.py`，可控asyncio事件，信号失效注入仅针对detail请求；fixture只使用源码声明的API形状，不执行作者断言。
- 表单：`verify-extension-form-boundary.mjs`，从指定Git对象AST提取生产函数原文，在独立VM执行合成输入；不是复制一份实现。
- 结果：`reports/extensions-09dfcc3c-review.json`、`reports/extensions-form-09dfcc3c.json`。
- 截图与原始合成记录：experience工作树 `tmp/experience-9131/extensions-09dfcc3c-final/`、`-focused/`、`-performance/`。原误定位run独立保留在`extensions-09dfcc3c/`。

```powershell
python scripts/experience/9131/verify-extensions-candidate.py --out tmp/experience-9131/extensions-09dfcc3c-final
python scripts/experience/9131/verify-extensions-candidate.py --out tmp/experience-9131/extensions-09dfcc3c-focused --only X04,X11
python scripts/experience/9131/verify-extensions-candidate.py --out tmp/experience-9131/extensions-09dfcc3c-performance --only X12
node scripts/experience/9131/verify-extension-form-boundary.mjs
```

运行前必须确认22825实际版本与冻结；脚本不会替服务器切换commit。现有浏览器已关闭，并提前释放冻结。未改生产代码、未push/tag/共享Preview；后续integration生产包复验08e74283及共享预加载修复，或负责人明确交付的新修片。Phone原反例/实体测试不受此次Extensions通过项影响。
