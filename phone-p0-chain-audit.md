# Phone P0 链路审查与窄修

审查范围是当前 `codex/9131-integration` 的 Phone production owner：`PhoneTransport`、`AppSessionProvider`、`ChatScreen`、`phone-api` 与已安装的 Expo fetch 边界。没有启动服务、访问 AVD、读取用户凭据或修改 Engine/core/runtime storage。

## 首个偏差

首个会把可恢复网络错误变成重复副作用的偏差在 PhoneTransport 的统一 401 分支：所有请求收到 401 后先 refresh，再以同一请求重放。聊天提交、审批响应、队列 promotion/cancel 等都是 POST；若 Engine 已接受动作而中间层只在响应阶段返回 401，自动重放会产生重复提交或错误目标动作。这不是普通网络重试，副作用的接收状态不可由客户端推断。

同一审查还确认两个相邻边界：

- 设备切换期间，`activeConversationIdRef`/transition token 和 transport dispose 是实际目标围栏；迟到快照、终端 400/404、上传和流事件在切换后必须拒绝写入新设备。
- 审批 UI 原来只有 `sending`（聊天提交状态）和最近完成 ledger；审批本身没有同 ID 在飞锁。两个快速点击可能同时调用 `approvePendingItem`，而事件的最近完成过滤只在第一次请求返回后生效。

## 已实施修复

提交 `b5bcf71c`：

- `PhoneTransport.requestEndpoint` 对 GET/HEAD 继续执行实例验证、401 单飞 refresh 和同 endpoint 读重试；对 POST/PUT/PATCH 先消费 401 响应、单飞 refresh 凭据，然后抛出 `status=401, acceptanceUnknown=true`，绝不自动再次发送原 body。PhoneDraft 的既有 `pendingIntent` 会把提交保存为 `acceptance_unknown`，保留 fingerprint、session 和 clientMessageId，用户显式重试时才允许同一意图继续。
- `ChatScreen.handleApprovalResolve` 增加按 approval/interaction ID 的 in-flight set；同 ID 并发点击只进入一次真实 API 调用。调用前捕获 conversation ID 和 transition token；若设备已切换，真实结果仍可完成，但旧请求不能清理新会话的 approvals/UI。finally 只释放该 ID 的锁。
- 保留已有 active transport、authority、401 refresh singleflight、队列状态、snapshot/realtime 恢复 owner；没有增加第二个 Planner、全局重试或跨设备并发。

## 可执行反例与结果

`apps/v8-agent-os-phone/tests/phone-transport-boundary.test.cjs` 直接 transpile/load production TypeScript，并使用真实 `Response`、ReadableStream 和本地 HTTP server：

- 旧逻辑 mutant（POST 401 refresh 后重放）在“服务端第一次已记录 write，随后 401”反例中产生两次 write；修复后 write=1、refresh=1，错误带 `acceptanceUnknown`。
- 错误 alias 的 `/api/client/instance` 不匹配时不发送 Bearer、refreshToken 或业务请求；真实 HTTP 307 不抵达未验证目标。
- 实际有限请求在 body EOF、cancel、JSON decode error、timeout 和排队 abort 后释放 permit；响应体未结束时始终不超过两个 finite reads。
- 8 个并发 LAN 恢复调用共享一个正在进行的 identity check；错误 instance、错误 principal、后台/切换后的迟到结果都不能发布 endpoint。
- 生产 AppSessionProvider 在离线时保留 cached user、session 和 draft，不调用 signOut；实际 ChatScreen process poll 的 failure/stale 空结果保留已有 process surface，隐藏状态不发请求，迟到 A 结果不能覆盖 B。
- `handleApprovalResolve` 实际回调的双击反例只有一次 `approvePendingItem`；切换 conversation 后不调用旧 approvals 的 UI 清理。

本轮完整 Phone lint/typecheck/i18n 和 73 项 Node tests 均通过。此前 49db 的独立 A/B/P05/P10A 结果、原生播放器修复、native alias/body/长流检查仍是已有分层证据；本轮没有重跑 AVD、截图或 root 服务。

## 运行中链路审查结论

正常提交先 flush draft，再写 pendingIntent=submitting，构造稳定 clientMessageId/fingerprint；运行中若可排队，先写本地 queued row，再走同一个 authorized POST。接受后才把 pendingIntent 标记 accepted、按 composer revision 清理输入；失败/断线从 optimistic surface 移除并标记 acceptance_unknown，之后同 fingerprint 使用同 clientMessageId。这样断线不会静默重发，用户仍能明确继续。

实时双向协作仍由 `session-realtime` 事件归一化和 snapshot/runtime-events 恢复提供：详细流/摘要流各自有 lane，旧 lane abort 后通过当前 authority/transition guard 投影；401 流恢复只重开无副作用的读流。审批请求按稳定 approval ID upsert，最近解决 ledger 防止重放事件；现在提交动作另有在飞锁和会话围栏。队列取消/promotion 失败保留本地条目并显示错误，成功以服务端返回 queuedMessage 更新；没有把 HTTP 200 或 onclose 当作副作用证明。

## 限制与未覆盖

此修复把“401 后不自动重放副作用”作为安全边界；若服务端确实在 401 前拒绝且用户想继续，用户需显式再次操作。未更改 Engine 的幂等存储合同，也未宣称网络层能证明远端已接受/未接受。审批 endpoint 当前依赖稳定 approval ID 和 Admin authority；真实 provider、跨蜂窝/LAN 物理切换、Android/iOS 硬件和完整 realtime 长时压力未在本轮重跑。

根 P0 需要协调任务按现有发布流程整合 `b5bcf71c`，先完成 CI，再由 root 决定 push/tag/Preview。当前 integration 工作树保留其他协作者的变更所有权；本提交只包含上述 Phone 三个文件。
