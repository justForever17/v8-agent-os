# Network Runtime 接入教程

Network Supervisor Runtime 用来把多个 V8 Agent OS 节点连成可观察、可审批、可恢复的协作网络。它不是裸远程 shell，也不会自动修改 VPN、路由、DNS 或系统防火墙。

## 两台设备开始协作

两端都需要运行 V8OS。Phone 配对是另一条连接流程，手机不因登录 V8OS 就变成任务执行节点。

1. 两端打开 Admin 的 Network Runtime 页面，开启设备协作。
2. 在“设备连接”选择检测到的 Admin 地址，例如 `http://192.168.1.10:9528`。跨网络可用已登录的 Tailscale 地址，或指向 Admin 的稳定 HTTPS 隧道地址。
3. 点击“检查连接”，再“保存地址”。探测只证明本机能访问该路由；还需完成对端配对，不能把探测成功当成远端任务成功。
4. 一端“生成连接码”，复制“连接邀请”；另一端粘贴邀请，确认来自你信任的设备，再点击“信任并连接”。邀请带短码、地址、公钥与有效期，不含设备 token。跨网段或组播不可用时也可用这一路径，不必手填 peer ID、公钥和密钥。
5. 选中已连接设备，按需要设置“本机执行目录”。这只决定收到任务后在本机哪个目录运行，不会设置或开放另一台机器的真实目录。
6. 在任务区描述任务，选择设备并发送。以任务结果为准：“对端已接收”只证明消息送达，不代表任务执行完成。

邀请过期就重新生成。已发现的局域网设备仍可展开“对已发现设备使用短码”；发现本身不建立信任。

## 连接地址与认证

- 通常只设置一个 **Admin 入口**。Engine 可以继续监听本机回环地址，无需将普通 Engine API 暴露到局域网或公网。
- Admin 的 `/v1/network-supervisor/peer/*` 是受限的设备 HTTP 代理。Engine 独立核对 peer token、签名、目标和有效期；配对另校验短码。它不依赖或转发浏览器的 Admin cookie，也不会把 Admin 登录替换成设备认证。
- 不向其他设备公布 `127.0.0.1`、`localhost` 或 `0.0.0.0`。建议地址尚未经对端验证；多网卡、VPN 和防火墙仍可能影响可达性。
- LAN 发现使用同网段组播。Tailscale/Headscale 通常不转发这种发现包；使用连接邀请即可，V8 不会修改 VPN 路由、DNS、MTU 或防火墙。
- Admin 这条代理只支持 HTTP，不支持 WebSocket 升级；单独部署静态 Web 页面也不能提供 peer 或 WebSocket 服务。高级 WS 地址仅用于已单独配置的 Engine WebSocket 路由，普通邻居配对、消息和任务无需填写。

## Headscale 接入

Headscale 适合自托管控制面。V8 只把它作为可选 Mesh Provider。

1. 在 Remote Link 的 Headscale 区填写控制面地址。
2. 在 Admin 中输入 API Key。密钥只存 Engine Secret Store，不进入 config.json、日志、ToolMessage 或模型上下文。
3. 使用连接测试查看用户、节点和预授权 key。
4. 需要新节点入网时，创建短 TTL、single-use 的一次性入网 Key。
5. 路由、Exit Node、ACL、节点删除等危险操作必须在 Admin 控制面二次确认。

Agent 不会获得裸 Headscale 管理能力。

## Phone 连接

Phone 继续使用自己的扫码/粘贴配对与连接档案。这里的邻居邀请不能用来登录 Phone，Phone 的登录凭据也不能当作 peer token。处在同一 LAN 或 VPN 不会自动授予任何设备信任。

## Cloudflare Tunnel 与 Relay 的区别

**Tunnel** 把一个稳定 HTTPS 域名转到本机 Admin，例如 `https://v8.example.com`。另一台设备仍直接调用本节点的 peer 路由，不增加消息邮箱。Cloudflare Access 若拦截该路径，浏览器登录态无法供 peer 请求使用；需由用户配置专门的 peer 路由策略，并保留 V8 的设备签名认证。临时 `trycloudflare.com` 地址不用于持久设备邀请。

**V8 Relay** 是独立的消息邮箱服务，两端都连接它。它适合无法直接互访的节点，不会替代设备配对，也不能把 Worker 地址直接当作 Admin 地址。

## 公网连接 / V8 Relay

V8 Relay 用来在两台设备不在同一 LAN / Mesh 时传递邻居消息。它不是 Phone 登录码，也不是 OpenAI / Anthropic 兼容 API。它只转发 V8 signed envelope；真正的信任仍由短码配对、peer token、public key、nonce、expiry 和本机 Safety 处理。

### 什么时候需要

- 两台 V8 设备无法直连，但都能访问同一个公网 Relay。
- 希望在消息有效期内暂存离线消息，不依赖 WebSocket 一直在线。

如果 LAN / Tailscale / Headscale 已稳定可达，优先用直连。

### 架构关系

1. Engine 发邻居消息时先写本地 `network_relay_outbox`。
2. Relay Transport 调用当前 adapter 的 `POST /v1/relay/publish`。
3. Relay Worker 将 signed envelope 存入目标设备 mailbox。
4. 目标 Engine 通过 `GET /v1/relay/mailbox/{peerId}?cursor=...` 增量拉取。
5. 目标 Engine 校验 envelope 后交给邻居消息池。
6. 处理成功后调用 `POST /v1/relay/ack`。
7. WebSocket 只做在线推送提示；断线后仍靠定时 pull 恢复。

### Cloudflare 适配器准备

需要你在自己的 Cloudflare 账号中准备：

- Worker：公网 HTTP / WebSocket 入口。
- Durable Object：每个 peer mailbox / room 的有状态协调与消息索引。
- Durable Object storage：保存可拉取消息、cursor、ACK 状态。
- Queue：仅用于延迟重试和 dead-letter，不作为 mailbox 真相。
- 可选自定义域名：作为 Relay 公网地址。

### 部署模板

Engine 仓库提供模板：

- `apps/v8-agent-os-engine/runtimes/network_supervisor/relay_templates/cloudflare_worker.mjs`
- `apps/v8-agent-os-engine/runtimes/network_supervisor/relay_templates/wrangler.toml.example`

推荐流程：

1. 将模板复制到 Cloudflare Worker 项目。
2. 按 `wrangler.toml.example` 创建 Durable Object 和 Queue 绑定。
3. 使用 Wrangler 部署 Worker。
4. 访问 `https://<relay-domain>/.well-known/v8-relay`，应返回 `v8-relay.v1`。
5. 回到 Admin 的“公网连接（V8 Relay）”卡片，选择 Cloudflare Relay。
6. 填入 Relay 公网地址、Worker 名称、Queue 名称、Durable Object 命名空间。
7. 保存配置。
8. 两台设备仍需先通过可达路由完成连接邀请配对；Relay 不会自动建立信任。

### 自托管适配器

选择“自托管 Relay”时，只要求服务实现同一组 HTTP / WebSocket 端点：

- `GET /.well-known/v8-relay`
- `POST /v1/relay/publish`
- `GET /v1/relay/mailbox/{peerId}?cursor=...&limit=...`
- `POST /v1/relay/ack`
- `GET /v1/relay/ws?peerId=...`

### 验证方式

- Admin 状态显示 Relay 为可用。
- `queued` 表示本机等待发送，`published` 仅表示中继已接收，均不证明对端已接收或任务已完成。
- 目标设备能在邻居对话时间线看到消息。
- 目标设备 ACK 后 Relay mailbox 不重复投递。
- 断开 WebSocket 后，定时 pull 仍能收到消息。

离线保留同时受 Relay TTL 和签名 envelope 的 `expiresAt` 限制，较短者生效。当前协议不能保证任意时长离线后继续执行；过期应明确失败，检查对端结果后再决定重新发送，不延长旧签名来假装消息仍有效。

### 常见错误

- `relay_disabled`：Relay 开关未开。
- `runtime_disabled`：Network Runtime 未启用。
- `active_adapter_not_configured`：当前 adapter 缺少 Relay 公网地址。
- `missing_target_peer_id`：发布请求里缺少目标 peer。
- `Envelope signature verification failed`：两端配对、公钥或消息被篡改。
- 消息进 dead-letter：过期、无法解析、目标未配对或重复失败。

### 安全边界

- Relay 只能转发 signed envelope，不能替设备建立信任。
- Relay 不执行本机文件、shell 或 workspace path。
- 远端传来的 workspacePath 只能作为来源元信息；执行路径由本机 workspace resolver 决定。
- Cloudflare token 不保存进 V8 config；Admin Relay 卡片只保存公网入口和 adapter 元信息。

## 外部兼容 API

Network Runtime 提供 OpenAI / Anthropic 兼容入口，常见路径如下：

```text
/api/network-supervisor/openai/v1/chat/completions
/api/network-supervisor/anthropic/v1/messages
```

这些入口走 Admin relay。外部工具仍由外部客户端执行；V8 不会把外部工具偷偷替换成本机文件或 shell 工具。

在页面的统一接入卡启用兼容 API、创建访问密钥，再把页面显示的地址和密钥填入客户端。不要把邻居连接码或 Admin 登录密码当作 API key。

客户端需要支持暂停时，可读取响应中的 `v8os_run`。`waiting_input` 表示需要回答问题，`waiting_approval` 表示请在 V8OS 审批界面处理；均不是任务完成。使用同一访问密钥，向原协议端点提交以下扩展即可查询原 run，不重发任务：

```json
{"v8os_control":{"runId":"响应中的 runId","action":"status"}}
```

`action` 还支持 `answer`（附 `interactionId` 和非空 `answer`）及 `cancel`。API key 不能通过这个扩展自行批准本机 Safety 操作。工具结果须携带原调用 ID，并沿原线程/会话提交；错误身份和已消费结果会被拒绝。

## 本机 ACP 接入

先启动本机 V8OS 并完成 Admin 初始化，再在支持 ACP 的编辑器中把 Agent 启动命令设为 `v8os acp`。默认连接本机 Admin，无需复制 API key；自定义本机端口可通过 `V8OS_ADMIN_URL` 指定。当前自动认证仅支持 loopback 地址。

编辑器传入的工作目录成为该会话的本机可信项目，不改变全局工作区。支持文本、内嵌文本资源、流式更新、会话加载、取消、问答和权限请求；图片/音频及外部 MCP 清单未支持时明确报错。编辑器需实现对应权限和问答能力才能完成交互，不能把暂停显示为完成。

## 产物预览

产物跟随当前连接入口。

- Phone 通过 LAN 连接时，产物链接走 LAN Admin origin。
- Phone 通过 Tailscale / Headscale 连接时，产物链接走当前 Mesh Admin origin。
- 产物内容仍通过 Admin client artifact proxy 读取：

```text
/api/client/artifacts/{artifactId}/content?sessionId={sessionId}
```

`sessionId` 是必填资源权限边界；缺失或不匹配当前 Session/Workspace 的请求会被拒绝。短期 signed URL 的签名覆盖完整路径和该查询参数。

V8 不会因为 active mesh profile 把所有 LAN 产物链接全局改写成 Mesh 地址。

## 常用操作

- 复制 compat URL：给第三方 OpenAI / Anthropic 客户端使用。
- 复制连接邀请：让另一台 V8OS 设备确认配对。
- 检查连接：确认本机到指定 peer HTTP 路由，不替代配对和任务验收。
- 加载更早消息：查看最新 100 条以外的记录。
- 重试发送：重发原消息身份，不重复创建新任务。已执行但结果未知时，先检查对端记录。

## 故障排查

### 手机打不开 Admin

- 确认手机和 Admin 是否在同一 LAN / Mesh 网络。
- LAN 模式下不要使用 `127.0.0.1` 或 `localhost`。
- Tailscale 模式下确认双方都在线。
- WireGuard full-tunnel 可能覆盖 DNS 或路由，V8 只提示风险，不改配置。

### 找不到 peer

- 确认 Network Runtime 已启用。
- LAN discovery 需要同网段和组播可用。
- Mesh 候选只表示网络上能看到节点，不代表它已经是可信 V8 peer。
- 没有候选时直接粘贴连接邀请，无需等待组播发现。

### Challenge 失败

- 检查对端 Admin 入口、监听地址、防火墙或 Tunnel 的 peer 路径。
- 两端版本应匹配；身份已改变时撤销旧连接再配对，不手工覆盖未知公钥。
- 查看失败分类：`peer_unreachable`、`route_conflict`、`auth_failed` 或 `mesh_provider_unconfigured`。

### 产物无法预览

- 确认当前 Phone 使用的 Admin origin 可达。
- 确认登录状态有效。
- 使用当前连接入口重新打开产物，不要混用 LAN 页面中的 Mesh 链接或相反。

## 安全边界

- V8 不安装 VPN。
- V8 不修改 WireGuard / Tailscale 路由、DNS、MTU 或密钥。
- Headscale API Key 只存在 Secret Store。
- 候选节点不会自动信任。
- 远程任务派发必须经过 token、public key、challenge 和 Safety 边界。
