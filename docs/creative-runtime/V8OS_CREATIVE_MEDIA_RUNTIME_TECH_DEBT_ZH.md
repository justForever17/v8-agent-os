# Creative Media Runtime 技术债清单

本清单只记录当前仍在生产路径上的兼容面。新增能力不得继续向这些旧存储写入，也不得把它们描述成新的运行真相。

## CM-DB-01：edit/render JSON 尚未迁移

- **优先级**：P1。
- **现状**：job、lifecycle、remote projection、work order、cost、quality 与 safety 已迁入 SQLite；`edit_plans.json` 和 `render_jobs.json` 仍由现有受锁存储负责。
- **边界**：本轮只为 edit/render 输入补统一资源 authority，不新增第二套半成品 SQLite schema。
- **偿还计划**：下一次 Creative Media 持久化迭代先补 schema、CAS、幂等迁移和 rollback harness；至少保留两个完整迭代周期的只读兼容，并在旧读取量低于 0.1% 后删除 JSON 写路径。
- **门禁**：迁移前后同一 work order/render 结果一致；并发更新不丢失；10k 记录查询 P95 相对基线退化不超过 10%。

## CM-DB-02：jobs.json 只读迁移输入

- **优先级**：P1。
- **现状**：SQLite 是运行真相；`jobs.json` 只用于 v1 幂等迁移和明确回滚读取，正常 Engine 不再回写。Session 删除 tombstone 独立保存，避免删除 Graph/Session 后丢失远端对账证据。
- **退出条件**：迁移 receipt、失败告警和旧读取计数连续两个完整迭代稳定，旧读取占比低于 0.1%，并完成旧文件归档/恢复演练后，移除 fallback。
- **禁止项**：不得为了旧 fixture 恢复整文件读改写，也不得让 malformed v1 静默变成空数据库。

## CM-ADAPTER-01：Provider 适配仍集中在大 runtime 模块

- **优先级**：P1。
- **现状**：稳定边界已抽出结构化 HTTP 错误和远端状态合同，但各 Provider 的 submit/poll/cancel/probe 请求构造仍在 runtime 内。
- **偿还计划**：按 Provider 小步迁移到同一 adapter 接口，先迁移 remote lifecycle 覆盖完整的供应商；每次迁移复用原 endpoint/parser，不保留第二套状态映射。
- **门禁**：相同 Provider 的 poll 与 reconcile 必须使用同一状态解析器；只有带 canonical terminal proof 的结果才能解除 retry 门禁；错误面不得泄露凭据、URL query、handle 或 raw response。
