# 多 Agent 协作技术债台账

本台账只记录多 Agent 消息合同、Episode Fabric、handoff、实时事件与恢复链中已知但未在当前迭代彻底消除的兼容或临时实现。新增条目必须写清事实边界、可观测入口、偿还条件和计划周期；不能把它当作长期保留兼容分支的理由。

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
