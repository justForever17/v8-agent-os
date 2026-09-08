# 多 Agent 协作技术债台账

本台账只记录多 Agent 消息合同、Episode Fabric、handoff、实时事件与恢复链中已知但未在当前迭代彻底消除的兼容或临时实现。新增条目必须写清事实边界、可观测入口、偿还条件和计划周期；不能把它当作长期保留兼容分支的理由。

时效说明：001–004 是 2026-08-17 的历史登记，本轮未逐项复核，不能直接当作当前源码事实。005–006 来自 9.7.1 之后候选树的实际剖析；发布与实体安装验收另行记录。

## TD-ORCH-001：原子 episode delivery 尚未覆盖全部终结路径

- 优先级：P1
- 登记日期：2026-08-17
- 当前事实：主 executor 终态、损坏合同终态、直接 delegation 终态和 parent join failure 已使用单事务 `commit_runtime_episode_delivery`。部分 `waiting_input / waiting_child / waiting_external / retry`、deadline/exception 以及 graph parallel finalize 路径仍可能分两步写 handoff 与 episode 状态。
- 风险：进程在两步之间退出时会留下可诊断但未绑定 `resultRef` 的交付，恢复器需要额外 reconciliation，不能假设 episode state 等同于交付完成。
- 可观测入口：`runtime_episode_handoffs`、episode `resultRef`、`runtimeDeliveryDiagnostics` 与 `handoff.ref.created`/episode terminal event 的顺序。
- 偿还计划：下一次 Episode Fabric 迭代将剩余终结路径逐个迁移到原子 API，并为每条路径增加 `after_handoff` fault injection、stale fence 与重启恢复测试。
- 完成条件：生产终结路径不再存在 handoff/terminal state 两事务窗口；故障矩阵在双 DatabaseManager 并发与进程重启场景均通过。

## TD-ORCH-002：Web 与 Phone 暂时各自保存 realtime identity ledger

- 优先级：P2
- 登记日期：2026-08-17
- 当前事实：两端使用行为相同的 2048 项 `identity -> seq` 有界 ledger；snapshot 只清理已覆盖 identity，会话切换清空 authority。当前两份实现内容 hash 相同，但位于两个客户端源码树。
- 风险：后续单端修改可能造成 live/history/reload 去重语义漂移；超过 2048 个尚未被 snapshot 覆盖的乱序 identity 时，最旧 identity 会被淘汰。
- 可观测入口：Web `run-activity`、Phone `resource-resilience`、shared event-sequence 与 Admin delivery contract tests。
- 偿还计划：下一次 `session-realtime` 包正常版本升级时将 ledger 移入共享包，四端统一依赖，并增加超过容量后的 authoritative snapshot recovery 测试。
- 完成条件：Web/Phone 不再维护复制实现；四端只消费共享导出；锁文件与 tgz integrity 一致，live/history/reload parity 通过。

## TD-ORCH-003：legacy v1 handoff 仍保留只读兼容投影

- 优先级：P2
- 登记日期：2026-08-17
- 当前事实：缺少 v2 envelope digest 的历史 handoff 会保留为 `legacy_unverified`，仅供诊断，不能作为 current delivery 或 completion evidence；只有持有当前 producer fence/expected state 的原子 replay 才能升级。
- 风险：兼容读取增加 resolver 分支和迁移维护成本；长期保留会扩大恢复测试矩阵。
- 可观测入口：`deliveryIntegrity.status=legacy_unverified`、`current_handoff_integrity_unverified` 与对应 recovery action。
- 弃用计划：先增加 legacy 命中量指标和迁移提示，连续两个完整迭代观察调用量；降至低于 0.1% 后删除普通读取兼容，只保留离线迁移工具。
- 完成条件：所有活跃会话 handoff 均为 v2；迁移文档与工具可恢复历史记录；删除分支后 durability/runner/reload 测试通过。

## TD-ORCH-004：协作里程碑 retention 仍依赖显式 topic 分类

- 优先级：P2
- 登记日期：2026-08-17
- 当前事实：retention 会保留 episode、handoff、delegation、subagent acceptance 等语义里程碑，允许 progress/delta 被清理；分类表需要随新 topic 同步维护。
- 风险：新增协作 topic 未登记时可能在长期 retention 后只剩 canonical ledger，失去原 event identity/seq 的历史投影。
- 可观测入口：storage retention dry-run、`test_storage_retention_collaboration_history.py` 与 snapshot/history reload parity。
- 偿还计划：在下一次事件契约迭代把 retention class 变成事件 schema 的显式字段，由发布测试检查每个新 topic 的 retention 语义。
- 完成条件：topic 注册缺少 retention class 时构建失败；迁移后不再维护独立白名单。

## TD-ORCH-005：辅助环境探针与执行进展须分离

- 优先级：P2；若再次造成租约失效或重复执行，提升为 P1。
- 登记日期：2026-09-08；源码基准 `fa3fe2ee3d7764cce41af79483949dc9d52d6f57` 后本轮候选修复。
- 已核实：Windows 单测剖析中，两次同步写后摘要刷新累计约 40 秒，占测试调用约 69%；瓶颈为 Git 子进程，文件 marker 扫描仅约 0.001 秒。真实联测中工程上下文准备占住异步循环，同一 episode 在约 75 秒租约到期后重复领取 6 次，没有进入子代理模型调用。
- 本轮修复：写后仅登记脏状态；native 摘要扫描移出全局锁并按缓存身份和变更版本条件发布，保留驱逐与并发写保护。Git 辅助事实只由一个无队列的后台探针补入同一缓存，首次/失败明确 pending/unverified；不把未知仓库写成无 Git 或 clean。主机负载同样不在 prompt 入口同步等待 GPU 进程，采样完成才开始计算有效期。
- 工程只读准备移到线程，复用既有异步心跳与取消机制，返回后再次检查取消和租约；准备开始/结束用同一个进度节点投影。等待节点只因实际进展延长空闲窗口，不能仅凭心跳永久续命，且不越过绝对 deadline；等待过期不是执行已失败。单 episode 取消不得终止队列服务，真正服务退出仍清理其子任务。
- 剩余边界：明确需要的 Git 隔离/操作证明仍读取真实环境，辅助摘要不能替代。`subprocess` 的通信 timeout 不等于 Windows 进程创建及终止的完整 wall deadline；取消等待也不能保证立刻停止宿主只读子进程。最多一个后台探针和失败冷却限制资源，不能因此伪装成功或关闭权限检查。
- 可观测入口：`workspace_context` 进度、episode `attempt_count / lease_generation / last_heartbeat_at`、缓存 `snapshotVersion / mutationVersion / refreshPending / gitProbeStatus`、模型 `contextPreparationMs` 与既有 provider TTFT，以及独立记录的浏览器时间。各计时不得相互冒充。
- 偿还计划：下一次工程性能迭代测量冷/热工作区及慢进程启动的实际分布；只在证据证明必要时改进有界探针执行器或复用策略，不再把环境发现塞回每次写入。
- 完成条件：同一 episode 在慢准备期间保持租约；取消/租约丢失后无晚到派发；多工作区不被全局扫描锁串行阻塞。若进一步声称首次响应加速，还须提供真实冷/热端到端计时，不能仅用写入函数变快作为证明。

## TD-ORCH-006：保存证据读取与执行权限、历史提示分离

- 优先级：P1；本轮最小修复和联合验收，后续一轮内复核残余提示来源。
- 登记日期：2026-09-08；已提交基准同 005。
- 已核实：新会话 Supervisor 默认看不到 Research broker，读取已有答案被迫绕向旧 Memory；委派指导又只允许 `tool_observation_detail`，使有合法保存 bundle ID 的验证者无入口。子任务诚实返回 BLOCKED，父级改为自己补表；执行结束和 Web 一致不能证明独立验证完成。
- 本轮修复：基线工具投影只读 `research_broker` 的保存答案/原始来源操作，Supervisor、直接子代理和终止型孙代理共用相同执行 schema；完整 Research run/资料删改仍来自原工具组。显式 allowlist/no-tools 不被扩权。指引区分实际 `toolobs://` 与保存 pack/bundle ID，重读来源返回原始 claimId，去除临时阅读计数冒充原编号的可能。不增加第二套研究执行器、目录或持久化 owner。
- 授权入口：broker 复用持久 session owner、scope binding 与 workspace authority，ledger 在限量前过滤并在 ID 修改时复查。无工程目录的会话仍能读取自己的资料；跨会话只在同用户、同可信物理工作区复用，项目别名不单独划权限。新包保留最小来源上下文；旧包仅在原 session/run 能恢复授权时读取，来源不明的旧 global 包留给 Admin 治理，不自动共享。拒绝结果不能被 Surface 按原 ID 二次回读。
- 回归入口：`test_runtime_tool_access.py` 的三层实际读取/禁止 mutation 反例；`test_research_answer_read.py` 的公共工具与 Agent Surface 分页/引用原编号；`run_supervisor_runtime_skill_live_audit.py --case saved_research_verification --live` 的当前 pack 指针、独立 worker 引用证明、父级接受和 Web reload 对照。
- 剩余边界：旧 Memory/用户自定义 Agent 指引仍可能引用已过时的工具 URI 或流程；不能批量覆盖用户记录。对真实失败先查实际注入，再按来源 owner 修正或由受治理配置入口修订。分页 transport 的已读范围按返回 `nextOffset`，不得把请求的 `maxChars` 或 Agent 自称“全文已读”当证据。
- 完成条件：新的保存答案读取与委派在同一 run 真正闭环；已显式选中的证据能被子/孙 Agent 实读并保持原引用；旧提示不再要求重新调研才能读取。全量跨 provider、任意历史包与任意登录站点仍须分别验收。

## TD-ORCH-007：扩展目录刷新与每轮任务筛选分离

- 优先级：P2；若持有失效 MCP session 或已撤销 grant，按 P1 处理。
- 登记日期：2026-09-08；源码基准同 005。
- 已核实：Skill 冷索引和变更扫描曾进入前台准备；候选缓存只比工具名/授予 ID，MCP 重连或同 ID grant 收窄后可能复用旧对象。通用 `read/create/review` 词与短词子串也会产生无关候选。部分被误认为预筛运行的活动卡实际是普通工具完成诊断。
- 本轮修复：目录由原后台 owner 刷新，前台脏项明确 pending 并排除；任务只在已有目录上筛选。结果缓存无时间到期强制重筛，但键涵盖工具 schema/描述、session 对象身份、授权内容及任务上下文。MCP `tools/list_changed` 合并通知、仅发布当前 session 的完整工具表；失败保留上次完整表并标记 stale，退出取消并等待刷新任务。普通诊断不再投影成扩展运行卡。
- 回归入口：`tests/extensions/test_extensions_route_cache.py`、`test_mcp_client_version_projection.py`、`test_skill_loader_startup_lightweight.py` 和插件授权投影测试；覆盖通知突发、旧 session 晚到、撤销、schema 变化与扫描预算公平性。
- 剩余边界：Skill 外部目录变更仍依赖有界后台轮询，不是零文件 IO。实测 128 个 Skill、2,048 个引用文件完整 hash 约 1 秒，但没有堵住事件循环；若实机磁盘负荷仍显著，下一轮按实际观测评估现有 watcher 的增量扫描，禁止直接引入第二套全局目录服务。真实第三方 MCP 通知兼容性尚未逐产品验收。

## TD-ORCH-008：桌面完成证明与窄构建兼容的后续复核

- 登记日期：2026-09-08；基准 `4d2378cd` 后续修复；owner 分别为 Computer Use episode 和 Desktop release。
- P2：现有 Computer Use 部分任务专用机器检查及点击瞬时语义判据仍可能漏覆盖或误报。本轮移除“空检查即全目标成功”的权威、复用当前观察，并保持 Agent 判断与机器约束分开；下一迭代用新任务回放检查静态特例，不能再把专用歌曲/网站规则扩写成通用完成判据，也不能把截图存在当语义证明。
- P2：DMG 工具链临时卸载 `Resource busy` 使用至多一次重建作为窄兼容。入口 `scripts/desktop/build-macos-dmg-with-retry.mjs`，触发/再次失败在 CI 日志可见；所有其他错误不重试，不强制卸载宿主磁盘。下一发布周期核查 upstream dmg-builder 修复与原生复现；确认升级消除故障后移除此 helper 和 workflow 接入，保留等价失败反例。
- 物理待核：Ubuntu 9.7.1 缺 Shell 退出日志，旧 CPU 冷导入仍达约 17 秒；已有 120 秒 readiness 与 zombie 修复不能冒充托盘物理验收。拿到同一次 Shell/Engine 时间线或目标机后复核，不用 Windows timing 推导 Linux 改善。
