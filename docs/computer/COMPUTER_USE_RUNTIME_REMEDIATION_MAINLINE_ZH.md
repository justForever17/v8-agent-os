# `computer_use runtime` 整改主线拆解

## 一、整改总目标

本轮整改不是继续给 `computer_use` 补零散能力，而是把它从“Windows UIA/SendInput + 单帧视觉兜底”的可用原型，推进到一个**更统一、可恢复、可观测、分层清晰**的 runtime 主链。

整改后的目标状态固定为：

1. **原生桌面输入更可靠**
   - 浏览器地址栏、网页输入框、普通编辑框的输入行为可预测
   - Windows 输入法/键盘布局不再成为隐性破坏因素
2. **浏览器场景不再继续硬塞进桌面自动化思维**
   - 浏览器、Electron、WebView2 建立专项执行 lane
   - 先走高精度控制面，再考虑 GUI 兜底
3. **视觉校验从单帧判断升级为短序列判断**
   - 至少建立三帧级别的“动作前 / 动作中 / 动作后”时间观念
   - 不再把“稳定等待”误当成“时序理解”
4. **能力口径与真实实现重新对齐**
   - 文档、能力声明、运行时诊断与真实主链保持一致
   - 不再让 capability flag 先于实现长期漂浮

一句话目标：

> 让 `computer_use` 成为一个分层清晰的桌面/浏览器执行 runtime，而不是把所有复杂场景都推给 UIA、坐标点击和单帧视觉兜底。

---

## 二、整改原则

### 2.1 固定原则

1. **runtime-first**
   - 关键逻辑优先收口到 `apps/v8-agent-os-engine/runtimes/computer_use` 与相关 `core` 主链
   - 不把新的核心语义放到 Admin / Web / 临时脚本
2. **API/结构化控制优先，GUI 兜底**
   - 这条原则不能再只是文案，应真正落实到路由层
3. **观察面与执行面分离**
   - `desktop-live`、视觉裁判、动作后验证、trace/report 必须围绕统一观察契约
4. **浏览器不是普通桌面窗口**
   - 浏览器/Electron/WebView2 必须拥有专项 lane，而不是继续只靠 app profile 特判
5. **输入可靠性优先于动作丰富度**
   - 没有可靠输入治理前，不应继续扩桌面复杂任务种类

### 2.2 不做的事

1. 不在这条主线里直接把 `web-access-main` 粗暴嵌入 `computer_use`
2. 不继续把多帧视觉理解伪装成“stable rounds”
3. 不在 capability flag、注释、页面文案里提前声明主链尚未落地的能力

---

## 三、整改主线分阶段

整改固定拆成 **P0 / P1 / P2** 三层，按顺序推进，不允许跳阶段先做“看起来更炫”的部分。

### P0：主链止血与执行可靠性

这是当前最优先的现实短板层，目标是先把“容易炸、容易误判、容易看着能跑但实际不稳”的问题收住。

#### P0-A 输入主链治理

目标：

- 为 Windows 输入建立显式治理链，解决输入法污染、布局不一致、浏览器地址栏输入不稳定的问题。

实施边界：

1. 在 `windows_uia.py` 与 `windows_sendinput.py` 之间增加**输入前治理层**
   - 探测当前前台窗口与目标控件输入环境
   - 记录并归一化键盘布局/输入模式
   - 必要时切到英文输入，再执行输入
2. 区分三类输入目标
   - 普通原生编辑控件
   - 浏览器地址栏/网页动态输入区
   - 文件接收区/粘贴接收区
3. 所有输入动作都要回写统一元数据
   - 使用了哪种输入策略
   - 输入前后输入环境是什么
   - 是否发生布局/IME 归一化

验收标准：

1. 地址栏输入网址不会再因中文输入法而 silently 失败
2. 网页输入区与普通编辑框的策略路径可从 trace 里看清
3. 输入失败时能明确知道是焦点问题、IME 问题、控件问题还是发送策略问题

#### P0-B 视觉裁判最小闭环升级

目标：

- 在不重写整套视觉系统的前提下，把当前单帧裁判升级为最小可用的短序列裁判。

实施边界：

1. 把当前 `visual_judge` 的输入契约从单张图升级成短序列
   - 固定至少 3 帧：`pre_action` / `mid_action` / `post_action`
2. 新增统一 observation bundle
   - 当前截图
   - 前一帧
   - settle 结束帧
   - 关键 bounding boxes / selector / visual scope
3. `post_action_visual_check` 不再只看 `matchCount + readText`
   - 至少增加“状态是否推进”“区域是否变化”“目标是否从不可用转为可用”等时序判断
4. `_wait_for_post_action_stability` 保持存在，但降级为 observation sampling 阶段的一部分，不再被表述成“已经具备视觉时间观念”

验收标准：

1. 动作后视觉验证能解释“发生了什么变化”
2. 点击按钮后页面未推进时，不再仅因为 screen hash 稳定就误判成功
3. trace/report 中能看到三帧或短序列证据，而不是只有单张截图

#### P0-C 能力口径校正

目标：

- 让 capability / docs / trace / runtime diagnostics 不再夸大现状。

实施边界：

1. 对 `supportsKeyframeVisualFallback` 这类声明做一次清点
   - 未落地主链前，不再用容易误导的成熟能力表述
2. 在 `desktop capabilities`、trace metadata、runtime report 中区分：
   - 已实现
   - 部分实现
   - 路由口径/未来能力
3. 对外叙事只保留当前主链真实能力

验收标准：

1. 不再出现 capability 显示支持，而主链实际没有对应能力
2. 另一个工程师只看 runtime diagnostics，就能知道系统到底会什么、不会什么

---

### P1：浏览器专项 lane 与时序观察协同

这一层是 `computer_use` 真正从“桌面原语 runtime”走向“分层 runtime”的关键。

#### P1-A Browser/Electron/WebView2 专项 lane

目标：

- 为浏览器类应用建立专项执行平面，不再继续只靠 UIA/坐标/单帧视觉。

设计约束：

1. lane 是 `computer_use` 的专项执行通道，不是把 `web-access-main` 整体嵌进去
2. 仍由 runtime route 决定是否进入 browser-specialized lane
3. 浏览器 lane 先服务这些对象：
   - Chrome / Edge / Firefox
   - Electron
   - WebView2 宿主应用

优先能力：

1. 地址栏与导航控制
2. DOM / JS evaluate
3. 元素点击与文件上传
4. 标签页管理
5. 页面级媒体/视频信息读取

与 `web-access-main` 的关系：

- 把它当作浏览器控制平面的参考样本
- 借鉴其：
  - CDP first
  - DOM/JS 主真相
  - 视频截帧
  - 站点模式经验
- 不直接把 skill 文件夹或交互哲学混入 `computer_use` runtime 代码

验收标准：

1. 浏览器场景优先走专项 lane，而不是落回普通 `browser_checkout` profile
2. 页面交互不再只能通过桌面输入与坐标点击完成
3. 浏览器/Electron 类任务的成功率与可解释性显著高于纯桌面路径

#### P1-B `computer_use` 与 `desktop-live` 的时序观察协同

目标：

- 让 `desktop-live` 不再只是直播/旁路能力，而成为短序列观察的支撑面。

实施边界：

1. 定义统一的时序观察契约
   - runtime 动作前后从同一观察平面取样
   - 与 `desktop-live` 帧流或关键帧采样对齐
2. 为视觉裁判、post-action verify、trace/report 共用观察 bundle
3. 把“动作证据”从散装截图升级成统一 observation artifact

验收标准：

1. `computer_use` 的视觉证据与 `desktop-live` 不再是两条割裂链
2. 同一个动作的前中后状态可被统一追踪、复盘与调试

---

### P2：长期能力矩阵

这是 `computer_use` 从“可用的 Windows 桌面 runtime”走向更完整 Computer Use 平台时才进入的长期层。

#### P2-A 自绘应用分层策略

目标：

- 明确原生控件应用、浏览器内核混合应用、自绘应用、封闭应用的不同控制层级。

实施边界：

1. 不再把所有桌面应用都按统一桌面窗口处理
2. 为不同阵营定义：
   - API/脚本接口优先级
   - Accessibility 使用策略
   - 视觉兜底策略
   - 风险和可恢复性约束

#### P2-B 应用专属自动化接口整合

目标：

- 对专业桌面应用逐步引入专属自动化接口，而不是继续只靠 GUI 模拟。

实施边界：

1. 只在有明确价值的应用上做
2. 必须遵守 runtime-governance 与恢复纪律
3. 不允许为了追求能力表面完整度，把大量 app-specific 逻辑堆回 `runtime.py`

#### P2-C 真正的跨平台能力矩阵

目标：

- 让 Windows / macOS / Linux 的 `computer_use` 路径不只是接口相似，而是能力与降级策略也可比较。

实施边界：

1. 以 `drivers/contracts.py` 为 capability 真相面
2. capability 只能反映真实落地，不提前宣称
3. 每个平台都必须区分：
   - 输入能力
   - accessibility 能力
   - pointer/viewport 能力
   - 视觉校验能力
   - 浏览器专项通道能力

---

## 四、主链拆分后的模块责任

### 4.1 继续留在 `computer_use runtime` 的部分

这些能力应该继续由 `apps/v8-agent-os-engine/runtimes/computer_use` 主链持有：

1. 桌面动作原语与高层包装动作
2. app profile / selector memory / muscle memory
3. 动作前后观察与验证
4. trace / report / runtime diagnostics
5. browser-specialized lane 的调度入口

### 4.2 不应继续堆进现有 runtime 的部分

这些能力不应继续伪装成 runtime 内的简单补丁：

1. 浏览器 DOM/CDP 深控制
2. Electron/WebView2 专项控制适配
3. 自绘应用专属 API 适配
4. 未来若有的视频/多帧重视觉模型服务

这些能力应被设计成：

- 专项 lane
- 专门 adapter
- 或明确的 sidecar/service

而不是继续往 `runtime.py` 里堆 if/else。

---

## 五、推荐实施顺序

固定实施顺序如下，不建议调换：

1. **先做 P0-A 输入治理**
   - 因为这是当前最直接破坏成功率的路径
2. **再做 P0-B 最小多帧视觉闭环**
   - 因为当前很多“看似成功”的动作，其实缺的是时序校验
3. **再做 P1-A browser-specialized lane**
   - 因为浏览器现在是最明显的结构性短板
4. **随后做 P1-B 与 `desktop-live` 协同**
   - 让时序观察从临时拼装过渡到统一观察面
5. **最后推进 P2 长期矩阵**

如果顺序反过来，先做浏览器 lane 而不先治理输入与视觉时序，会导致：

- runtime 变复杂
- 但基础可靠性仍然不够
- 浏览器和桌面路径会一起背历史包袱

---

## 六、每阶段最小验收场景

### P0 场景

1. 浏览器地址栏输入网址
2. 网页表单输入文本
3. 原生记事本输入文本
4. 点击按钮后页面/窗口状态推进验证

### P1 场景

1. Chrome/Edge 普通网页导航
2. Electron 应用中的 Web 页面交互
3. WebView2 宿主应用内表单与按钮控制
4. 页面内视频/图片/懒加载内容读取

### P2 场景

1. Flutter / Qt Quick 类自绘应用
2. 专业桌面软件的脚本/API 控制
3. Windows/macOS/Linux capability matrix 对照

---

## 七、默认假设与落地方式

1. 当前整改主线只优先落在 `v8-agent-os` 主仓，不同步改 `v8-agent-os-site` 和 `v8-bridge`
2. `web-access-main` 默认只作为浏览器专项 lane 的参考样本，不直接合并
3. P0 优先解决真实成功率问题，不以“表面能力更多”为目标
4. 新增能力必须遵守当前 runtime-governance、trace、artifact、resume/retry 纪律
5. 后续若继续写文档，应把每个阶段拆成独立实施文档，而不是回到一份泛泛 roadmap

---

## 八、结论

`computer_use` 的整改主线不该再围绕“给桌面多加几个动作”展开，而应固定沿三条主轴推进：

1. **输入治理**
2. **时序观察**
3. **浏览器专项 lane**

只有这三条真正进入主链，`computer_use` 才会从“能做一些桌面动作的 runtime”提升为“可信的桌面/浏览器执行 runtime”。否则继续叠 patch，只会让现有结构更难维护，也更难解释为什么一次能成、下一次又炸。

