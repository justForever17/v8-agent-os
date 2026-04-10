# OpenClaw 4.8 PluginHost 接管说明

## 这份文档解决什么问题

OpenClaw 4.8 与旧版本相比，最容易让 V8 接管链失真的地方有 4 个：

1. `openclaw.json` 不再是完整宿主状态数据库，宿主会把它投影写回成极简形状。
2. 插件如果只是落在 `~/.openclaw/extensions`，会进入 **global auto-discovery 漂浮态**，而不是 canonical install/trust 主链。
3. 动态插件工具不能再只靠 manifest `tools` 字段发现，必须接受 gateway RPC、durable inventory cache 和日志恢复目录多源并存。
4. 渠道入站如果字段合同不完整，就会出现“表面接管了，但线程、附件、提及、交互卡、历史识别仍然是残的”。

所以新版接管链的重点不是“怎么 patch `openclaw.json`”，而是：

1. 保持 `openclaw-v8-bridge -> /v1/plugin-host/inbound -> plugin_host runtime` 的 canonical inbound envelope 完整。
2. 把 bridge readiness 的真相迁到：
   - `openclaw plugins inspect/list`
   - bridge `/status`
   - launcher env
   - channel runtime state
3. 把工具目录的真相迁到：
   - gateway RPC
   - durable inventory cache
   - 日志恢复目录
   - manifest 仅作兜底

## 当前 canonical 运行纪律

### 1. 路径纪律

- V8 主工作区 / 项目工作区仍然是用户可见产物的 canonical 真相面。
- `plugin_host` 的 **入站附件自动下载** 继续进入：

```text
<V8 workspace>/downloaded_media/plugin_host/...
```

- `plugin_host` 的 **渠道出站语音/附件暂存** 进入 OpenClaw 允许根：

```text
~/.openclaw/media/outbound/v8-agent-os/plugin_host/...
```

- 这不是“把 V8 工作区挂钩到 OpenClaw 工作区”，而是：
  - V8 工作区继续负责下载与用户产物
  - OpenClaw 只负责渠道出站暂存

### 2. 渠道音频纪律

- `openclaw-weixin`
  - 当前 canonical 为 `mp3` 附件发送
  - 不要求 native voice
- `feishu`
  - 当前 canonical 为 `native_voice`
  - 必须满足 OpenClaw 4.8 的本地媒体允许根

### 3. 字段合同纪律

bridge 入站必须至少稳定提供：

- `channelId`
- `conversationId`
- `messageId`
- `accountId`
- `chatType`
- `threadId`
- `senderId`
- `senderName`
- `text`
- `mentions`
- `attachments`
- `eventKind`
- `eventSubtype`
- `accountScope`
- `actionPayload`
- `rawPayloadRef`

如果这些字段缺失，Admin 的 PluginHost 页应视为 **高风险假接管**，而不是“勉强可用”。

## 工具树为什么经常只剩两个 built-in

OpenClaw 4.8 下，像 `openclaw-lark` 这类插件往往是 **动态注册工具**，而不是 manifest 里静态声明 `tools`。

因此工具目录的优先级应固定为：

1. `gateway_rpc tools.catalog`
2. durable inventory cache
3. CLI inventory 刷新
4. OpenClaw 日志恢复目录
5. manifest 静态字段兜底

如果你只看 manifest，就会得到：

- `gateway.message`
- `gateway.sessions_list`

然后误以为“飞书插件没有工具”。

## provenance / trust 为什么必须收口

如果 `openclaw-v8-bridge`、`openclaw-lark`、`openclaw-weixin` 只是从：

```text
~/.openclaw/extensions
```

被自动发现，而没有：

- install record
- `--link` provenance
- `plugins.allow`

那它们虽然“看起来能跑”，但本质上仍是漂浮态：

- bridge readiness 会漂
- tool inventory 会漂
- trust 判断会漂
- 别人复现接入时会反复踩坑

推荐主链：

```bash
openclaw plugins install @v8-agent-os/openclaw-v8-bridge
openclaw plugins install @larksuite/openclaw-lark
openclaw plugins install @tencent-weixin/openclaw-weixin
```

开发机可用：

```bash
openclaw plugins install --link <repo>
```

并把可信插件显式 pin 进：

```json
plugins.allow
```

## 给别人交付时，不要只发 bridge 包

新版 OpenClaw 4.8 接管链，最小可复制交付应包含：

1. `openclaw-v8-bridge`
2. `plugin_host` doctor / repair
3. Admin 接管向导
4. launcher handoff env 注入
5. provenance / trust 检查
6. 渠道登录验证
7. claim / handoff / tools / media 自检

如果只给一个 bridge 包，用户仍然很容易卡在：

- `plugins.allow` 为空
- bridge route 404
- `operator.read` scope 不足
- channel 已登录但 claim 没发生
- OpenClaw 自己把 `openclaw.json` 又写回极简壳

## 最后一句

OpenClaw 4.8 接管的正确心智模型不是“修好一个插件”，而是：

> 维护一条从渠道入站、桥接 handoff、工具目录、媒体暂存到 provenance/trust 都统一可诊断的 runtime 主链。
