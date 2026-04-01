# NETWORK SUPERVISOR RUNTIME

## 这个 runtime 是干什么的

`NETWORK SUPERVISOR RUNTIME` 是 V8 的组网层。  
它负责让一个 V8 节点去发现、信任、唤醒，并把任务显式委派给另一个 V8 节点。

首版只做最关键、最稳定的那一小圈能力：

- 用 UDP multicast 做局域网发现
- 用手动 bootstrap peers 做广域网接入
- 用 peer token + public key 做 trust
- 支持 directed wake
- 支持显式 `peerId + task` 远程委派
- 让本地 `runtime_events / workflow_ledger / run metadata` 继续做真相源

它不是新的执行引擎。  
远端真正执行任务时，仍然走对方节点本地的 `chat` runtime。

## 应该怎么理解它

把网络想成一小队 V8 节点：

- 每个节点都有自己的 runtimes、memory、ledger 和 tools
- 节点之间可以互相发现
- 节点之间可以建立 trust
- 一个节点可以唤醒另一个节点
- 一个节点可以请另一个节点执行子任务
- 但本地节点仍然拥有“主 run 叙事”

所以它的定位很明确：

- 执行可以分布式
- 真相必须本地优先

## 首版边界

首版故意只做受控、显式、可观测的网络协作：

- 做发现
- 做 challenge / join
- 做 directed wake
- 做显式 delegation
- 不做自动选 peer
- 不做 broker-first 队列
- 不做完整网络拓扑台

## 协议长什么样

每一条网络消息都走同一套版本化 JSON envelope，至少包含：

- `version`
- `messageId`
- `messageType`
- `sentAt`
- `expiresAt`
- `fromPeerId`
- `toPeerId`
- `nonce`
- `signature`
- `trace`
- `payload`

其中 `trace` 至少要把本地运行上下文带上：

- `sourceRunId`
- `sourceSessionId`
- `workflowId`
- `delegationId`

这样远端回来的 `accepted / progress / result / failed` 才能重新对上本地 run story。

## 安全模型

首版安全模型固定为：

- Ed25519 做节点身份与消息签名
- HTTPS / WSS 做传输
- peer token + challenge / response 做 enrollment
- nonce + timestamp + expiry 防止重放

最重要的一句是：

> 发现不等于信任。

你在局域网里看到了一个 peer，只能说明：

- 它存在
- 它发过 discovery packet
- discovery packet 的签名是对的

这不代表你已经可以放心把任务交给它。  
真正的 trust 仍然要经过 join / challenge 和显式注册。

## 远程委派怎么跑

首版委派链路固定是：

1. 本地节点选择一个 trusted peer
2. 本地节点创建 outer `network_supervisor` run
3. 本地节点发送 `delegation.request`
4. 远端节点验签、验 trust，然后创建自己的 outer `network_supervisor` run
5. 远端节点再创建一个 inner 本地 `chat` run
6. 远端持续回传 `accepted / progress / result / failed`
7. 本地节点把这些状态投影回自己的 ledger 和 runtime events

这条链的核心规则只有一句：

> 允许远端执行，不允许把本地真相源交出去。

也就是说，真正给用户看的那条主 run，仍然必须由本地节点掌握。

## Admin 页应该告诉用户什么

`NETWORK SUPERVISOR RUNTIME` 的 Admin 页不用把协议细节全倒出来。  
它只需要清楚回答这几个问题：

1. 这个 runtime 开没开？
2. 当前发现到了哪些 peers？
3. 当前 trust 了哪些 peers？
4. 现在能不能做 delegation？
5. 我能不能在一个地方完成 challenge、wake 和 delegation 诊断？

所以首版 Admin 页应该是：

- 配置页
- 状态页
- 诊断页

而不是完整拓扑控制台。

## 最后一句话

`NETWORK SUPERVISOR RUNTIME` 的意义，不是“多一层传输”，而是让 V8 真正具备多节点 Supervisor 协作能力。

它必须把五件事做好：

1. 发现 peers
2. 安全建立 trust
3. 定向唤醒 peers
4. 把任务委派到其他节点
5. 仍然把本地 runtime 真相保持完整
