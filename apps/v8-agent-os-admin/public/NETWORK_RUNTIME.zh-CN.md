# Network Runtime 接入教程

Network Supervisor Runtime 用来把多个 V8 Agent OS 节点连成可观察、可审批、可恢复的协作网络。它不是裸远程 shell，也不会自动修改 VPN、路由、DNS 或系统防火墙。

## 推荐理解

- **连接档案**：Phone、Admin、Engine 或 peer 使用哪条地址访问当前节点，例如 LAN、Tailscale、Headscale 或手动 URL。
- **推荐地址**：V8 根据当前网络状态算出的可用地址，只做建议，不会自动切换。
- **候选节点**：从 LAN / Tailscale / Headscale 发现到的节点，只能填入表单，不能自动变成可信节点。
- **可信节点**：完成 token、public key、challenge 后，被当前节点允许唤醒或委派任务的 V8 节点。
- **一次性入网 Key**：Headscale 的短期 single-use preauth key，只显示一次，用完即失效。

## 普通 LAN 接入

LAN 是默认稳定路径。只要 Phone 与 Admin/Engine 在同一局域网，优先使用 LAN。

1. 确认 Admin 地址可从手机访问，例如：

```text
http://192.168.1.10:9528
```

2. Phone 连接页保存这个地址作为连接档案。
3. 进入 Network Runtime 页面，保持 LAN discovery 可用。
4. 不需要 Tailscale、Headscale 或 WireGuard 时，可以完全忽略 Mesh Provider。

LAN profile 不会因为 Tailscale 在线而自动降级或被替换。

## Tailscale 接入

Tailscale 适合跨网络访问自己的 Admin/Engine。

1. 在运行 V8 Engine/Admin 的机器上登录 Tailscale。
2. 在 Admin 的 Remote Link / Network Runtime 页面刷新诊断。
3. 复制 V8 推荐的 Tailscale Admin 地址，例如：

```text
http://your-node.tailnet.ts.net:9528
```

4. 在 Phone 连接页新增一个 Tailscale 连接档案。

注意：V8 只读取 Tailscale 状态并生成推荐地址，不会自动切换 active profile，也不会修改 Tailscale 路由、DNS、MTU 或密钥。

## Headscale 接入

Headscale 适合自托管控制面。V8 只把它作为可选 Mesh Provider。

1. 在 Remote Link 的 Headscale 区填写控制面地址。
2. 在 Admin 中输入 API Key。密钥只存 Engine Secret Store，不进入 config.json、日志、ToolMessage 或模型上下文。
3. 使用连接测试查看用户、节点和预授权 key。
4. 需要新节点入网时，创建短 TTL、single-use 的一次性入网 Key。
5. 路由、Exit Node、ACL、节点删除等危险操作必须在 Admin 控制面二次确认。

Agent 不会获得裸 Headscale 管理能力。

## Phone 连接

Phone 只消费连接档案和 manifest。常见方式：

- 同网段：LAN URL。
- 异地访问：Tailscale / Headscale URL。
- 临时调试：手动 URL。

所有远程连接仍需要 V8 登录认证。处在 Mesh 内网并不等于自动可信。

## Peer 候选到可信节点

Tailscale / Headscale 节点会进入 **候选节点**，但不会自动加入可信节点。

标准流程：

1. 在候选节点中点击“填入 peer 表单”。
2. 补齐 peer token、public key、允许 scope 和 workspace。
3. 保存后运行 challenge。
4. challenge 成功后，才把它当作可唤醒、可委派的可信节点。

手机节点会被标记为需要审批，因为普通手机并不天然具备 V8OS peer 能力。只有经过专门 V8 Phone peer 支持、并完成 token/public key/challenge 的手机节点，才允许加入。

## 外部兼容 API

Network Runtime 提供 OpenAI / Anthropic 兼容入口，常见路径如下：

```text
/api/network-supervisor/openai/v1/chat/completions
/api/network-supervisor/anthropic/v1/messages
```

这些入口走 Admin relay。外部工具仍由外部客户端执行；V8 不会把外部工具偷偷替换成本机文件或 shell 工具。

## 产物预览

产物跟随当前连接入口。

- Phone 通过 LAN 连接时，产物链接走 LAN Admin origin。
- Phone 通过 Tailscale / Headscale 连接时，产物链接走当前 Mesh Admin origin。
- 产物内容仍通过 Admin client artifact proxy 读取：

```text
/api/client/artifacts/{artifactId}/content
```

V8 不会因为 active mesh profile 把所有 LAN 产物链接全局改写成 Mesh 地址。

## 常用操作

- 复制 compat URL：给第三方 OpenAI / Anthropic 客户端使用。
- 复制推荐 peer URL：给另一个 V8 节点配置 peerBaseUrl。
- 测试连接：确认 Admin/Engine/Peer 是否可达。
- 从候选填入 peer：只填表单，不自动 trust。
- Challenge：确认 token、public key 和路由是否正确。
- Wake：唤醒可信 peer。
- Delegate：向可信 peer 委派任务。

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

### Challenge 失败

- 检查 peer token。
- 检查 public key。
- 检查 peerBaseUrl 是否能从当前节点访问。
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
