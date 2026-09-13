# Network 交付链验收记录

目标版本：`v8-os-v2026.09.12.1`。验证日期：2026-09-12 至 09-13。
开发基线：`03e75009`（9.11.1）。本记录不代表发布 CI 或 Linux 物理机已经通过。

## 根因与修改边界

- 可选 trace 字段签名前后的 null 归一化不同，真实 Ed25519 验签失败。现在签名以最终 NetworkEnvelope 为准；HTTP 200 还必须通过对端、公钥、请求 ID、trace 和业务 ID 校验。
- 默认 Engine loopback 地址被传播给邻居。Admin 现在提供有限的 peer 路由，仍由签名与 peer token 认证；无需把整个 Engine 暴露到局域网。地址建议来自实际启用网卡，排除代理 fake-IP。
- 收件、任务和 wake 入队未原子化；重复投递或并发同 ID 不同正文可能污染已接受任务。现在使用既有 SQLite 事务和业务 ID，冲突不产生部分记录。
- 任务入队时过早写入尚未创建的 run 外键，导致模型执行前失败。由 Runtime 创建 run 后绑定。
- 审批、问答等待被当作成功或失败终态，进程恢复可能重复副作用。现在保留等待态，跟随原 run 的 canonical 正文回传；过期执行 lease 明确失败，已有结果只重送。失败回传有界重试，筛选可处理记录后再分页，避免历史记录遮挡。
- 兼容 API 曾在 Safety 批准前外发 tool call、恢复时丢失外部工具合同，或使用旧请求的强制 tool choice。现在依赖当前持久化 handoff 证明，对原 run 恢复完整工具身份，只清除上次请求的选择条件。
- 兼容 API 待回传工具曾与发现状态共用 JSON，陈旧写回会丢掉等待或复活已消费结果。现由独立 SQLite 表在既有数据库中原子创建/领取，首次迁移保留旧等待及消费记录；错误模型名、请求格式或限流先返回，不消耗合法等待。完整结果回执与预览分开保存。
- ACP 旧实现没有可用的 Node CLI 入口，内容块、问答和取消有断点。改为复用 Admin 本机客户端、可信项目、ERC 和实时事件；删除未使用的旧 surface 投影。
- 用户明确调整跨工作区语义：普通 Supervisor 原生文件/命令访问，manual/reduced 走原 Safety 审批，minimal 按既有免审语义；批准绑定动作、解析目标、cwd 和参数摘要，不扩大 workspace 或复用宽泛 allowlist。OS/V8 核心、密钥、显式 sandbox、Capsule/writeSet 和版本冲突仍保留独立边界。

没有新建另一套身份、审批、运行状态库或任务 Planner。网络配置修改使用 config_broker 原有 prepare/commit/rollback，配对和凭据继续由原 owner 管理。

## 真实 provider 与本机链路

以下是同开发机上实际配置的 MiniMax M3 的单次样本，不是跨平台性能基准。

| 链路 | 验证结果与时间 |
| --- | --- |
| 签名 HTTP 邻居 | 配对 500 ms；Supervisor 读 → 写 → 读回 → 签名结果及 ACK 47,125 ms；重复业务 ID 无第二次文件写入；测试 Engine 完整停止。对端为验签 HTTP fixture。 |
| OpenAI-compatible | 真实非流式请求 22.94 s；外部工具请求 6.83 s，7k 工具正文回传 5.19 s；错误 key/thread 与重复结果拒绝；ask_user 原 run 恢复。 |
| Anthropic-compatible | 真实 SSE 41.69 s；tool_use 16.75 s，8k tool_result 回传 9.25 s；错误 key、重复结果拒绝；ask_user 原 run 恢复。 |
| 兼容 API 原生审批 | 24.969 s 到真实 waiting_approval；Owner 批准 641 ms；同 run 进入 waiting_external_tool；专用外部工具结果回传 6.812 s 后 completed。审批前无工具外发；API key 不能代替 Owner 自批准。 |
| 工具回执持久化 | M3 工具请求 12.562 s；错误 alias 16 ms、错误 schema 46 ms，均未消耗等待；修正后完整结果 8.000 s 继续交付，持久回执正文与实际 delivery run 匹配。 |
| ACP stdio | 实际 `v8os acp` 子进程经 Admin；纯文本首更新 781 ms、结束 9.516 s；ask_user 首更新 1.578 s、结束 14.453 s，答案只出现一次且 load 后存在；取消 1.657 s、Engine 确认为 cancelled。manual 同工作区审批 35.938 s：权限请求 → 批准 → 同 run 恢复，临时文件确实删除，正文不重复、load 一致。 |

兼容 API 真实请求通过隔离 Engine HTTP；ACP 经实际 Admin。前者不能冒充外部客户端或 Admin 反代实测。协议中的工具结果使用无敏感内容的 fixture，模型和 Runtime 不是 mock。流式审批分支若模型未实际触发，不计为 live 通过。

ACP 审批反例曾只收到 `done(waiting_approval)` 而没有审批事件；桥接现在从同 run 的权威 pending 列表恢复权限请求。早期跨工作区 fixture 被路径边界拒绝，不能将 `finished` 或答案中的口令算作副作用成功。

跨工作区新语义已在重启后的真实 Engine 验证：正式 `v8os acp` → Admin → M3，manual 模式持久化。批准正例 34.328 s，外部临时文件实际删除、原 run completed、正文单次、history 一致；两张卡分别对应 Remove-Item 与后续 Test-Path，已回查不同 toolCall/approval ID。拒绝反例 10.125 s，文件仍在，ACP 返回 refusal，Engine 保持 waiting_input 等待进一步指令，未假报完成。fixture 均由脚本本轮专门创建。

## 对抗与 UI

- 真实签名、真实 SQLite：丢 ACK 重试、篡改/错误身份、事务中断、并发同 ID 不同正文、迟到 waiting、过期 lease、取消、原 run 恢复、失败/缓存结果回传。
- 分页反例：55 个较新的已处理失败或仍在等待的 run，不能挡住较旧的可回传结果。
- 等待通知断网：waiting_input/approval × 原 run 已/未结束 × ACK 已/未落盘，共 8 种故障组合；补发等待通知后继续跟随原 run 最终正文，不重新执行模型。
- 兼容工具回执：两个真实独立进程竞争只能领取一次，重开实例仍不能复活消费；陈旧其他 Network 域写回不覆盖等待；模型/schema/限流失败不会吞掉待处理调用。整批领取回滚保留原记录。
- 两个不同 checkpoint 的工具结果不能合并恢复为一个 run：整批拒绝且不消耗，可分别继续；同 run 多工具和普通无 checkpoint 批次仍可用。480KB 全文回执与 metadata 分列，状态查询和写事务不会解码历史正文。
- 跨工作区的原生 read/write/grep/command 覆盖三个审批模式、批准/拒绝/取消、目标/参数/cwd/symlink 漂移；权限请求仍用当前 ToolCall 身份。审批请求使用内容摘要，不重复复制文件正文。
- Admin 生产构建和 Preview 重建后，13 项本机浏览器 fixture 通过：入口建议/探测/保存、邀请、失败重试、130 条历史合并及新增第 131 条、切换设备时迟到请求隔离、协作设备自己的工作目录保存。无页面错误。fixture 不等于 Linux 对端实测。
- Canvas UI patch 在应用前核对原文件 hash；破坏性反例证明旧缓存会在错误偏移写 JSX，现实现拒绝。Research 的篇幅/站点数量等是建议评分，实际读取、引用和独立验证仍需证据。
- 浏览器媒体本机实测按同一时间轴核对三帧像素、音轨区间和频率、字幕及播放恢复。该样本未调用 STT 或视觉模型。真实抖音只能证明页面可打开；未取得可验证视频元素，不能宣传视频理解已通过。

## 未覆盖及下一次物理验收

- Linux 测试机已由用户关闭。本轮未重做 Linux 9.12.1 安装、LAN 配对、双向读写或退出测试。
- 没有部署或实测外部 Cloudflare Relay、Tunnel、Tailscale/Headscale 网络。Admin HTTP peer 路由可用于这些可达入口；普通 Next HTTP 路由不提供 WebSocket 升级。Relay 的 published 只表示中继接收，不能显示为对端已收或任务完成；消息离线保留仍受 envelope 到期约束。
- ACP 本轮是标准 stdio 客户端验收，未启动第三方编辑器。未支持的图片/音频输入及外部 MCP 清单明确拒绝，不静默丢弃。
- 兼容工具回执持久化不等于模型执行和 SQLite 的跨系统事务。领取后若执行进程中断，保留完整回执和原/实际 delivery run 供查明恢复，不能自动重放可能已产生的副作用或宣称完整自动恢复。
- 下次使用两台实际安装包：双方保存可达入口并探测 → 用便携邀请配对 → 各自选本机目录 → 本机为主下发只读与专用文件写入 → 核对两端正文/任务状态 → 断线重试与一次审批恢复。不能用本轮 loopback 替代。

复现入口在 `tests/scripts/README.md`；live 必须显式 `--live`。原始配置、密钥、登录态和用户聊天未加入本报告或测试夹具。

发布前联合回归：Network 全组 + 新旧 Safety/工作区 + config_broker + Canvas + Research harness，**686 passed，61 subtests passed**；ACP **30 passed**；Admin **170 passed**；CLI **97 passed，1 个 POSIX 用例在 Windows 跳过**；发布/DMG合同 **41 passed**。这些集合有部分重叠，不相加宣传总数。后续小修只补对应定向用例；完整安装包仍由 tag 发布流水线构建。
