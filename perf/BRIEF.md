# 性能冲刺简报：V8 Agent OS 桌面版/Web/Admin/Phone 全域性能治理

## 你的职责

负责 V8 Agent OS 桌面版（Electron 43 Shell）、Web 聊天主站（`/chat`）、Admin 控制台（`/admin/*`）及 Phone 移动端打开和交互响应速度。按照 Anthropic《How we made claude.ai 3x faster in two weeks》方法论：**定义「打开到能用」-> 找确定性可数指标 -> 逐层剥离首屏关键路径巨石 -> 用基线和棘轮锁住收益**。

---

## 核心操作与「能用」的定义

| 操作 | 对应页面/端 | 起点 | 终点（真的能用） | 判定表达式（JavaScript / DOM） |
|---|---|---|---|---|
| **桌面端冷启动至可聊天** | 桌面版 Shell (`/chat`) | Electron 窗口创建 / 导航开始 | React Hydration 完成，样式就绪，输入框可点击、可聚焦、无遮挡 | `Boolean(document.querySelector('textarea[data-v8os-chat-composer="true"]:not([disabled])')) && document.querySelector('[data-v8os-style-probe="true"]')?.getAttribute('data-v8os-hydration') === 'ready'` |
| **首屏打开聊天主页** | Web 浏览器 (`/chat`) | 页面 `navigationStart` | 会话树与第一条消息可见，输入框可以打字且不丢字 | `document.querySelector('textarea[data-v8os-chat-composer="true"]:not([disabled])')?.offsetParent !== null` |
| **Admin 路由切换** | Admin 后台 (`/admin/*`) | 侧边栏点击目标菜单项 | 目标面板数据与操作按钮完成水合且不处于整页 loading | `Boolean(document.querySelector('.admin-content')) && !document.querySelector('.admin-content .animate-spin')` |
| **消息流式渲染响应** | Web / 桌面 (`/chat`) | 接收到流式输出第一包 token | 增量内容平滑上屏，主线程长任务 < 16ms，滚动与打字不掉帧 | `performance.getEntriesByType('longtask')` 增量为 0 且帧率稳定保持在 60fps/120fps |
| **Phone 端冷启进入会话** | Phone App | App 启动 `index.tsx` | 脱离 `booting` 加载态，首屏会话列表与对话完成上屏 | `status === 'authenticated' && activeConversationId !== null` |

---

## 不能动的东西（护栏约束）

1. **视觉与交互规范**：保持现有 UI 布局、暗黑/明亮主题、动画质感与设计标准不变；
2. **安全与治理契约**：保留 Engine 9530 的凭据管理、Session Realtime 的事件投影与鉴权机制，不绕过身份认证；
3. **功能完整性**：3D GLTF 预览、Mermaid 流程图、代码高亮、多端配对、桌面宠物等能力必须完好，优化手段应为按需懒加载（Dynamic Code-splitting）而非阉割功能；
4. **状态与版本兼容**：本地工作区、配置与历史会话数据结构不受影响。

---

## 目标与关键指标

- **首屏初始 JavaScript Chunk 体积**：从目前的数兆级别降至 500KB 以下（减少 70%+）；
- **打开到能用（TTI / Hydration）耗时**：p75 降低 50% 以上；
- **打字与流式渲染的主线程长任务**：彻底消除单次长于 50ms 的 JavaScript 卡顿，打字延迟降至 < 8ms；
- **Admin 页面切换并发请求**：消除重复和非必要的连环 API 请求，利用内存缓存实现秒级切页；
- **Phone 端网络握手**：优先命中活跃局域网/本机端点，避免离线 Candidate 造成的串行探测超时。

---

## 上线与回滚策略

- **发布门禁**：通过黄金测试、单元测试、打包体积对比和端到端冒烟验证；
- **回滚机制**：每个性能优化专项独立 Commit，发现任何视觉或功能回归立即 revert。
