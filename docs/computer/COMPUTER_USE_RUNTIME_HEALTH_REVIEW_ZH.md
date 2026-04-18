# `computer_use runtime` 健全性深读审计报告

## 一、执行摘要

当前 `computer_use` 更接近一个 **Windows UIA/SendInput 语义路由 + 单帧视觉兜底** 的桌面自动化 runtime，而不是成熟的跨层多模态 Computer Use 平台。

基于当前主链代码与真实实测问题，可以给出下面的总体判断：

- 原生 Windows 应用的基础能力已经进入可用区间，尤其是唤起应用、焦点切换、坐标点击、基础文本输入等动作。
- 浏览器与自绘应用场景明显偏弱，当前更多还是沿用桌面自动化思维在处理，而没有专用高精度通道。
- 视觉校验与时序理解偏弱。当前系统具备的是“动作后等待稳定”和“单帧候选裁判”，不具备真正的多帧时序理解。
- 浏览器专用高精度通道缺失。当前 `computer_use` 内没有 CDP/DOM/Playwright 级浏览器执行主链。
- 对外叙事与能力口径中存在“声明先于落地”的现象，例如 `supportsKeyframeVisualFallback` 已出现在能力结构里，但主链上还没有真正的多帧视觉裁判能力。

一句话总结：**当前系统不是不能用，而是只在原生桌面基础动作上进入可用区间，离成熟 Computer Use 平台还有明显结构差距。**

---

## 二、当前主链事实

### 2.1 runtime 主链闭环已经存在，但成熟度有限

当前 `computer_use` 的主链闭环在代码里是明确存在的。`runtime.py` 把自身描述为一个带有 `observe -> act -> verify -> decide` 闭环的 specialized runtime，并声明会产出 `desktop observations / artifacts / trace runs`。  
事实源：

- `apps/v8-agent-os-engine/runtimes/computer_use/runtime.py:198`
- `apps/v8-agent-os-engine/runtimes/computer_use/runtime.py:202`

这说明它不是简单的工具集合，而是已经被纳入 runtime 主线。

### 2.2 Windows 当前主驱动是 UIA + SendInput + window_message 组合

Windows 路径的能力总结很明确：当前优先走结构化语义路径，输入与点击使用 `pywinauto`、`SendInput`、`window_message` 等多种退化链组合。  
事实源：

- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:246`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:971`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:1056`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/contracts.py`

这意味着当前能力的核心不是视觉端到端，而是：

1. 先用 UIA/Win32 找结构化目标。
2. 找不到或不稳定时，再用坐标/SendInput 补。
3. 最后用较薄的视觉校验兜底。

### 2.3 supervisor 默认拿到的是高层包装能力，而不是任意桌面原语

`computer_use` 对 supervisor 默认暴露的是高层工具面，不是低层原语全开放。低层桌面能力默认在排除名单里，高层能力则单独列在允许集合中。  
事实源：

- `apps/v8-agent-os-engine/core/computer_use_tool_surface.py:5`
- `apps/v8-agent-os-engine/core/computer_use_tool_surface.py:40`

这件事本身是合理的，它体现了 runtime 治理边界；但副作用也很明显：**一旦高层包装不够聪明，supervisor 并不容易通过低层原语临时绕过。**

### 2.4 浏览器当前只是 app profile，不是浏览器专项 runtime lane

当前浏览器场景在 `computer_use` 内只是 `browser_checkout` 这类 app profile，被赋予一些地址栏 selector、浏览器窗口类名和按钮语义。  
事实源：

- `apps/v8-agent-os-engine/runtimes/computer_use/app_profiles.py:206`
- `apps/v8-agent-os-engine/runtimes/computer_use/app_profiles.py:213`
- `apps/v8-agent-os-engine/runtimes/computer_use/app_profiles.py:218`

同时，在 `runtimes/computer_use` 与相关 `core/computer_use*` 代码范围内，并没有看到真正的 CDP/Playwright/Puppeteer/DOM 执行主链。这意味着当前浏览器只是被当成“桌面窗口中的一种特殊应用”，而不是“拥有专用自动化控制通道的浏览器 runtime lane”。

---

## 三、四个毒点的代码级诊断

### 3.1 输入法污染：当前 Windows 输入路径缺少 IME/键盘布局治理主链

你实测的“打开浏览器输入网址时，不自知地使用中文输入法”不是偶发异常，更像是当前 Windows 输入路径的结构性缺口。

当前代码里能看到的输入策略主要是：

- direct text（值模式或 `set_edit_text`）
- `send_keys`
- `SendInput`
- `window_message`

事实源：

- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:971`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:1056`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:2450`

但在 `runtimes/computer_use` 范围内，没有看到以下类型的治理逻辑：

- 输入法状态探测
- 键盘布局切换
- IME 模式显式归一化
- 中文/英文输入模式确认

当前系统对浏览器输入区的判断更多是“这是浏览器/动态输入区域，所以要保守输入”，而不是“先把输入法状态治理正确，再输入”。因此结论固定为：

> **当前 Windows 路径缺的不是某个特殊 case，而是输入法/键盘布局治理主链本身。**

### 3.2 视觉裁判缺少时间观念：现在只有稳定性等待，不是真正的时序理解

当前视觉裁判链路有三个层次，但都不具备真正的时间语义：

1. `visual_judge` 当前接收的是单张 scope 裁剪图，任务是在候选里判断哪个更像目标元素。  
   事实源：`apps/v8-agent-os-engine/runtimes/computer_use/visual_judge.py:78`
2. `post_action_visual_check` 本质只看 `matchCount + readText`。  
   事实源：`apps/v8-agent-os-engine/runtimes/computer_use/post_action_visual_check.py`
3. `_wait_for_post_action_stability` 会轮询桌面观察直到签名稳定若干轮，但它比较的是 `window_title / tree_hash / screen_hash / focused_element_id` 稳定性，不是多帧语义解释。  
   事实源：`apps/v8-agent-os-engine/runtimes/computer_use/runtime.py:3965`

这三层叠加起来，当前系统具备的是：

- 动作前后做轻量稳定性等待
- 对单帧截图做候选判断
- 对动作后的区域做轻量文本匹配

但它**不具备**下面这些能力：

- 让视觉模型理解“刚才发生了什么”
- 判断按钮点击前后状态推进关系
- 基于连续帧建立时间感

因此结论固定为：

> **当前系统最多具备“稳定性等待”，不具备“时序理解”。**

### 3.3 浏览器自动化缺少专用通道：当前仍主要靠桌面自动化思维处理

浏览器相关事实可以概括成两条：

1. 当前代码知道“浏览器输入区比较特殊”，会把某些浏览器输入标记成 `review_required_dynamic_input`，避免误以为普通编辑框。  
   事实源：
   - `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:2356`
   - `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:2450`
2. 当前浏览器场景被包装成 `browser_checkout` app profile，包含地址栏 selector、确认/支付按钮等。  
   事实源：
   - `apps/v8-agent-os-engine/runtimes/computer_use/app_profiles.py:206`
   - `apps/v8-agent-os-engine/runtimes/computer_use/app_profiles.py:218`

但当前代码里没有看到：

- CDP
- DOM 读写
- JS evaluate
- Playwright/Puppeteer
- Electron/WebView2 专项控制面

这意味着当前浏览器自动化并不是“浏览器 API 优先”，而仍是“桌面自动化优先，浏览器只是特殊桌面窗口”。在真实复杂场景里，这种做法会带来三个直接后果：

1. 地址栏与网页输入的可靠性弱。
2. 页面内容理解只能靠 UIA/视觉兜底。
3. 浏览器/Electron 类应用最该拿到的高精度专用通道缺席。

因此结论固定为：

> **浏览器仍主要靠桌面自动化思维处理，是当前最明显的能力短板之一。**

### 3.4 对外文案与实现落差：能力口径已经前置，但主链尚未补齐

当前实现里已经有一些“更成熟能力”的口径信号，例如：

- `supportsKeyframeVisualFallback`
- `preferredRouteOrder`
- `supportsSemanticRoute`
- `supportsVisualRoute`

事实源：

- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/contracts.py:138`
- `apps/v8-agent-os-engine/runtimes/computer_use/drivers/windows_uia.py:246`

但从主链看，真正落地的仍然是：

- 单帧视觉裁判
- 轻量 post-action 文本核对
- 桌面结构化路径 + 坐标降级

因此这里的审计结论是：

> **当前存在“能力声明或能力口径先于完整实现”的情况，尤其体现在视觉时序、浏览器专项通道、跨层调度成熟度上。**

---

## 四、`web-access-main` 对 `computer_use` 的启发

### 4.1 它不是当前 `computer_use` 已接入的能力

当前 `web-access-main` 是独立存在于 `E:\Projects\v8chat\web-access-main` 的浏览器能力样本，不是 `computer_use` 主链的一部分。报告必须以此为前提，不把它误写成已接入能力。

### 4.2 它代表的是“浏览器 API/CDP 优先、GUI 兜底”的高精度控制平面

从 `README.md` 与 `SKILL.md` 可以明确看出，`web-access-main` 的核心不是“多几个联网工具”，而是建立了浏览器专用的控制平面。其已存在能力包括：

- 远程调试/CDP  
  事实源：`E:\Projects\v8chat\web-access-main\README.md:36`, `README.md:102`
- DOM 与 JS evaluate  
  事实源：`E:\Projects\v8chat\web-access-main\SKILL.md:65`, `SKILL.md:109`
- 真实鼠标点击与文件上传  
  事实源：`E:\Projects\v8chat\web-access-main\README.md:37`, `SKILL.md:122`, `SKILL.md:125`
- 多 tab 管理  
  事实源：`E:\Projects\v8chat\web-access-main\README.md:36`, `SKILL.md:95-133`
- 站点模式经验  
  事实源：`E:\Projects\v8chat\web-access-main\README.md:39`, `SKILL.md:215-246`
- 视频截帧  
  事实源：`E:\Projects\v8chat\web-access-main\README.md:40`, `SKILL.md:157`

### 4.3 对 `computer_use runtime` 的真正价值

`web-access-main` 对 `computer_use` 的价值，不是“给 `computer_use` 多加几个网页技能”，而是提供了一个非常明确的新思路：

> **浏览器/Electron/WebView2 类应用应该拥有浏览器专项执行通道，而不是继续只用桌面自动化思维处理。**

从 runtime 设计角度看，它提供的是下面这些启发：

1. **浏览器应先走 API/CDP，再考虑 GUI。**
2. **DOM/JS 是页面理解和交互的主真相，不该完全依赖截图和坐标。**
3. **视频、媒体、懒加载、shadow DOM、iframe 这类浏览器特有结构，应该在浏览器平面解决，而不是丢给桌面单帧视觉兜底。**
4. **站点经验与模式记忆应是浏览器控制平面的组成部分。**

### 4.4 结论

审计结论固定如下：

- `web-access-main` 适合作为后续 browser-specialized lane 的参考模型。
- 这份报告**不主张**直接把这个 skill 粗暴塞进 `computer_use`。
- 更合理的方向是：为浏览器/Electron/WebView2 建立专项控制平面，再由 runtime route 决定何时走这条高精度通道。

---

## 五、对照审计：你给出的 Computer Use 文案到底哪些成立

| 主张 | 当前实现判断 | 代码/结构依据 | 审计结论 |
|---|---|---|---|
| 三大系统共通闭环 `感知 -> 决策 -> 执行 -> 校验` | 当前代码里确实已有 `observe -> act -> verify -> decide` 闭环表述，且 `computer_use` 已纳入 runtime 主线 | `runtimes/computer_use/runtime.py:198`; `docs/V8_AGENT_OS_API_REFERENCE_ZH.md` 中 `computer_use` 作为当前 runtime 主线 | 部分具备 |
| 权限依赖、无障碍与截屏能力是基础 | 能力结构里已经显式区分 accessibility / screenshot / automation 等能力面 | `runtimes/computer_use/drivers/contracts.py`; `runtimes/computer_use/drivers/windows_uia.py` capability summary | 已具备 |
| 原子化操作集高度标准化 | 高层工具面和 primitive 已存在，click/type/hotkey/scroll/wait 等都被标准化包装 | `core/computer_use_tool_surface.py`; `runtimes/computer_use/runtime.py` primitive/action 主链 | 已具备 |
| API 优先，GUI 兜底 | 当前更多停留在理念与 route order 口径，真正成熟的 API-first 路径只在少数结构化 UIA 里成立；浏览器并无专用 API 主通道 | `drivers/windows_uia.py:246`; 浏览器无 CDP/DOM 执行主链 | 表述过度 |
| 多模态感知技术底座已通用可复用 | 当前视觉链主要是单帧候选裁判 + 轻量 post-action 文本核对，时序能力尚缺 | `visual_judge.py`; `post_action_visual_check.py`; `runtime.py:3965` | 部分具备 |
| 自绘应用可优先调用应用专属 API/脚本接口 | 当前代码里几乎没有真正的应用专属自动化 API 整合主链 | 在 `runtimes/computer_use` 范围内未见 Photoshop/Blender/CAD 类专属 API 路由 | 明显不足 |
| 自绘应用可通过系统无障碍映射获得元素级控制 | 这只对“已适配无障碍的自绘应用”成立；当前 runtime 没有系统性区分和显式策略矩阵 | 当前更多是通用 UIA/视觉降级，没有自绘专门分层策略 | 部分具备 |
| 完全无无障碍适配的自绘应用可由纯视觉兜底 | 当前确实有视觉兜底，但它是单帧、轻校验、低时序理解版本，复杂自绘场景风险仍高 | `visual_judge.py`; `post_action_visual_check.py` | 部分具备 |
| 动态插桩与渲染钩子是特殊场景方案 | 当前仓库主链中几乎没有这类能力 | 在 `computer_use` 主链代码中未见 Frida/hook/render hook 方案 | 明显不足 |
| 浏览器是适配最成熟、稳定性最高的场景 | 这不适用于当前实现。当前浏览器只是 `browser_checkout` app profile，没有 CDP 主通道 | `app_profiles.py:206`; 浏览器专项执行主链缺失 | 表述过度 |
| 浏览器首选 CDP/Playwright/Puppeteer | 当前实现没有这条主链 | 在 `runtimes/computer_use` 与相关 `core/computer_use*` 中未见 CDP/Playwright/Puppeteer 执行链 | 明显不足 |
| 次选系统级 UI 自动化接口 | 这条在当前实现里真实存在，尤其是地址栏与窗口层面 | `windows_uia.py`; `app_profiles.py` 地址栏 selector | 已具备 |
| 兜底纯视觉像素级操作 | 当前确实存在，但视觉能力较薄 | `visual_judge.py`; `post_action_visual_check.py` | 部分具备 |
| 原生控件应用是最适配阵营 | 这与当前实现最接近，原生 Windows 应用基础能力确实最成熟 | `notepad` / `explorer` profile；UIA 主驱动 | 已具备 |
| 浏览器内核混合应用可用浏览器专属渠道精准控制 | 当前并未落地，Electron/WebView2 仍主要被当成桌面窗口/浏览器窗口处理 | 浏览器/Electron 无 CDP 主通道；只有浏览器输入特殊判断 | 明显不足 |
| 特殊封闭应用几乎无法控制 | 这类判断符合行业现实，也与当前实现能力边界一致 | 当前 runtime 没有强突破封闭应用的特殊手段 | 已具备 |

这一节的核心结论可以压缩成四句：

1. “API 优先，GUI 兜底”目前在 `computer_use` 内还是理念，不是完整落地。
2. 自绘应用的“应用专属 API / 插桩 / 渲染钩子”当前几乎没有真正实现。
3. 浏览器类应用的 CDP 主通道当前缺失，因此“浏览器适配最成熟、接近 100%”这类说法不能套到当前实现上。
4. 原生控件应用是当前最接近真实可用的阵营。

---

## 六、后续改进方向

下面不是实现方案细节，而是基于本次审计得出的整改优先级。

### P0 现实短板

1. Windows 输入法/键盘布局治理
2. 浏览器地址栏与网页输入场景的输入可靠性
3. 多帧视觉裁判最小闭环

### P1 主链补全

1. browser/Electron/WebView2 专项通道
2. `computer_use` 与 `desktop-live` 的时序观察协同
3. 视觉校验从单帧升级到短序列

### P2 长期能力

1. 自绘应用分层策略
2. 应用专属自动化接口整合
3. 真正的跨平台能力矩阵

最后的路线判断固定为：

> 当前系统不是“完全不能用”，而是“只在原生桌面基础动作上进入可用区间，离成熟 Computer Use 平台还有明显结构差距”。后续整改如果继续围绕单帧视觉、浏览器坐标点击和输入法偶然成功去打补丁，收益会越来越差；真正值得投入的方向，是浏览器专项控制面、输入治理主链和短序列视觉校验。
