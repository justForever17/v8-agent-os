# Engine 身份与客户端边界

本轮工作基于 `v8-os-v2026.09.15.1` 后的集成候选。以下运行接口由源码和对应测试定义；真实客户端、安装包和 Linux 服务验收须单独记录，不能从本文推断已发布。

## 当前迁移合同

Engine 是实例 Owner、Phone 配对票据、设备、刷新凭据与撤销的唯一写者。Admin 只呈现配置、帮助与诊断，通过 Engine 管理 API 操作。`packages/session-realtime` 继续承担客户端投影，不因为移除 Admin 的传输职责而删除。

| 入口 | 使用者 | 身份与作用 |
| --- | --- | --- |
| `/v1/client-identity/*` | 本机 CLI、Web BFF、Shell、Admin 配置页 | 服务凭据或私有 Unix socket；不能使用 Phone token 初始化 Owner、增加配对或修改服务密钥 |
| `/api/client/*` | Phone、Web、桌宠 | 已验证 Phone 设备，或本机服务身份；session/run/resource 继续校验归属 |
| `/v1/*` 私有控制接口 | 受信本机服务 | 验证实例内部凭据；用户提交的 email/role 不建立身份 |
| 同进程 Phone gateway | 已配对 Phone | 精确路由、MIME/字节限制、流控与速率限制；剔除内部身份头；无 Admin 上游 |
| 既有 peer 路由 | 完整 Supervisor 对等设备 | 原签名 envelope/peer token；不接受 Phone 凭据替代 peer 授权 |
| 模型兼容接口 | 已授权外部模型客户端 | 专用受管理 API Token；OpenAI/Anthropic 协议直达 Engine，Token 签发和管理仍为本机控制操作 |

桌面端和桌宠免用户登录：启动器与本机 BFF 自动完成服务身份交换，界面不要求 Admin 密码或 Phone 二次扫码。此体验不等于匿名 HTTP；服务密钥不得发送到页面脚本、Phone 或日志。关闭 Admin 不应退出 Web、桌宠或取消 Engine 中的任务。

新 Phone access 为 15 分钟，校验算法、实例 issuer、专用 audience、用户与设备绑定。刷新保留 device ID、轮换 refresh token；设备撤销同时影响新请求和已有流。一次刷新以 `rotationId` 标识，60 秒内相同请求可取回同一加密结果；不同请求重放已消费凭据撤销该设备。Phone 在派发前把 rotationId 与原凭据保存在 SecureStore，存储新凭据失败不能显示刷新成功。

`systemBase.remoteLink.phoneGateway` 的 `enabled`、`port` 管理同进程 listener；`publicBaseUrl` 和各 transport profile 的 `phoneBaseUrl` 明确指定 Phone 可达地址。旧字段名 `adminBaseUrl` 只保留 wire 兼容语义，不能据它推导仍需 Admin 服务或自动把旧 Admin URL 当成新 gateway。

既有 peer HTTP 和模型协议共享此 Engine 网关的精确入口，各自保留独立凭据校验。OpenAI base URL 为网关地址加 `/v1/network-supervisor/openai`，Anthropic 为网关地址加 `/v1/network-supervisor/anthropic`；不能再将本机 Admin 地址作为远端服务地址发布。原 peer WebSocket 仍使用其原生受鉴权入口，当前窄 HTTP 网关不宣称支持该升级协议。

签名资源 URL 是短期读取能力，绑定路径、实例、用户、设备与到期时间。原生图片/视频读取仍检查撤销，支持 Range/ETag；短期签名不落用户资料，也不作为 Phone 缓存版本。用户媒体使用唯一文件名，工作区资源继续使用 session/workspace 权限。HTTP access log 不记录包含签名的原始 URL。

迁移读取已有 `users.json`、实例 ID 与有效 Mobile 凭据，禁止把损坏文件当空安装。旧身份写者停止后，Engine 使用原 `state.db` 记录配对、设备和刷新状态；旧 JSON 是一次性导入源。开放新写入后不能用旧 JSON 覆盖撤销/轮换结果；恢复应使用迁移状态检查、凭据恢复和前向修复。

## 最小远程执行器接口预留

**设计预留，尚未上线。** 此次迁移不实现 ESP32 固件、Android 原生控制，也不建立返回伪成功的执行端点。对应开发指导为 `v8os-remote-executor` 的 Engine direct prerequisites 和 protocol state contract。

| 角色 | 身份目标 | 允许动作 | 不继承的权限 |
| --- | --- | --- | --- |
| Phone 人类客户端 | `human_phone` / `v8-client` | 对话、审批、管理连接、使用现有受授权的 Engine 功能、表达启用执行器的意图 | 不能凭聊天 token 冒充 executor、绕过 runtime 治理或自授设备控制权限 |
| 本机客户端 | `local_client` / `v8-local` | 本机免登录交互与已授权配置 | 不可经远程 Phone gateway 复用本机 token |
| Supervisor peer | 既有 peer 身份合同 | 任务协作和已授权远端执行 | 不因“从设备”标签获得本机配置写权 |
| 最小执行器 | 预留 `executor` / `v8-executor` | 固定能力观察、结构化动作、回执、停止 | 无 LLM、Memory、自由脚本、Agent 注册或继续委派 |

同一手机的人类和执行器身份通过 `linkedHumanDeviceId` 关联，但持有独立凭据、撤销记录、grant revision 与控制 authority。切换聊天 profile 不切换原生执行器控制主机。关闭执行器只停设备控制，保留人类对话；撤销整台设备可明确同时撤销两角色。

预留的路由族在实现前不注册：

| 候选接口 | 调用方与合同 |
| --- | --- |
| `POST /api/client/executor-enrollments` | 已配对人类手动启用；签发一次登记票据，绑定实例、目标设备、能力与授权意图；不复制聊天 refresh token |
| `POST /api/executor/v1/enrollments/consume` | 原生端消费票据，建立独立 executor credential；并发消费一次成功 |
| `GET /api/executor/v1/channel`（WSS） | 执行器主动连接指定 authority；校验 audience/device/grant，每帧核权限版本与大小 |
| `POST /api/executor/v1/receipts` | 上报不可变 commandId/digest 与 received/started/succeeded/unknown_outcome；接收成功不代表业务成功 |
| `GET /api/executor/v1/commands/{id}/receipt` | 有权主体对账；查询旧回执不得重新执行旧动作 |
| `POST /api/executor/v1/media-handles` | Engine 签发绑定 device/operation/MIME/bytes/hash/expiry 的上传句柄，无任意路径或 URL |

设备命令最少绑定 `authorityId/deviceId/bootId/controlSessionId/leaseEpoch/grantRevision/commandId/commandDigest/deadline/capabilityRevision`。设备在 apply 前复核；队列和重连不能重置 deadline。同 commandId 不同 digest 必须冲突，同 ID 已终态只回历史。掉电后不能证明副作用时记 `unknown_outcome`，禁止自动重放点击或继电器脉冲。

设备本地 Stop、人工抢占与物理限位独立于网络和 Engine。授权交集为服务端 grant、设备本地许可、当前 lease 和 OS/硬件能力；手机系统权限仍需用户手工开启。同 APK 两角色不宣称操作系统级隔离。遥测仅更新最新结构化状态，低频合并通知；控制回执与大图/视频分通道限流，避免逐帧唤醒 Supervisor。

正式接入必须增加以下反例：Phone token 访问 executor 路由被拒；A/B authority 相同 epoch 不混用；旧 boot/control session 拒绝 mutation；本地 Stop 后重连不能自动 arm；取消与迟到成功均保留事实；receipt 重放不重复副作用；配置分发不能顺带提升设备 grant。

## Phone 多设备管理与配置分发预留

此部分是静态评估后的设计边界，尚无已上线的一键分发接口。Phone 的默认连接仅决定下次打开的聊天实例；它不是控制权转移，也不把其余实例自动变成从设备。完整 Supervisor peer 继续沿既有双向配对协作；executor 只接受其独立 authority 的固定能力命令。

连接、消息、草稿、待发送队列与资源缓存继续绑定 `(instanceId, principalId, profileId, servingInstanceId, sessionId)`。切换前持久化草稿并结算旧凭据写入，切换后取消旧请求与订阅；迟到结果不能写入新实例。激活某 profile 不得改变 executor authority 或清空其他设备的数据。

配置分发应复用每个目标 Engine 的 Config Broker prepare/commit/rollback。源实例仅输出有版本的非秘密模板，目标始终是自身配置与权限的唯一写者。Phone 只提交人类意图、查看差异和结果，不直接写多台设备的 config.json。

| 内容 | 分发策略 |
| --- | --- |
| 主题、语言、经过 schema 筛选的交互与运行偏好 | 可选择分发；目标校验能力、范围与当前 revision |
| 模型角色与模型参数 | 先映射目标已有 provider/modelRef；缺依赖显示待配置，不悄悄创建明文凭据 |
| 工作区、程序路径、端口、硬件与系统权限 | 默认本机专属；必要时逐目标显式映射，不按源机器值覆盖 |
| Owner、instanceId、Phone/peer/executor token、内部服务密钥、OS 凭据、grant/lease | 不进入模板；不能以“一键同步”扩大授权 |

候选流程：选择已授权目标 → 获取 capability/schema/revision → 各目标 prepare 并展示 diff/缺依赖/restartRequired → 一次确认选定计划 → 各目标按 planDigest 和 base revision commit → 汇总 succeeded/conflict/offline/unknown。每台目标产生独立不可变 receipt；没有分布式事务时不宣称全局原子成功。离线任务要有到期时间，恢复后重做 prepare；不得重放已经结果不明的写入。回滚逐目标使用原 transactionId 与 CAS，不能覆盖其后的人工修改。

未来管理接口可预留 `/api/client/config-distributions` 的 plan、confirm、status、cancel 语义，但本轮不注册路由。真正开放前必须验证部分成功、目标离线、重复确认、权限撤销、schema 不一致、冲突、确认后改配置、取消后迟到回执，以及 A/B 同名实例不串写。Phone 页面只为活动实例保持实时订阅，其他设备用缓存摘要和按需刷新；分发进度合并展示，不逐设备逐事件唤醒 Supervisor。

## 核验依据

JWT 校验边界参考 [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html)；刷新凭据的轮换和重放检测参考 [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)。V8OS 使用自己的实例配对协议，不宣称实现 OAuth Device Authorization Flow。
