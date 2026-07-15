# Spec Mode Pipeline Test Matrix

这份文件是可提交的测试地图附件；完整本地说明同步保存在
`docs/V8OS/SPEC_MODE_PIPELINE_TEST_MATRIX_ZH.md`。仓库当前 `.gitignore`
默认忽略 `docs/**`，因此这里保留一份精简但可追踪的门禁矩阵。

## 核心约束

- 多份 Spec 同时存在是正常状态。
- 默认注入 Supervisor / Runtime / Subagent 的只能是当前 run 绑定的 active Spec。
- 三段审批顺序固定：requirements 或 bugfix -> design -> tasks -> runtime execution。
- `tasks` 审批前不得派发 runtime。
- 交付完成后 Spec 标记为 `delivered`，退出默认 active 列表；显式 `specId` 仍可读取。

## Unit / Fixture

| ID | 覆盖点 | 测试 |
| --- | --- | --- |
| S1 | 下游阶段审批门禁 | `tests/core/test_spec_service.py` |
| S2 | tasks approved 后允许 runtime execution | `tests/core/test_spec_service.py` |
| S3 | tasks 管线字段：TASK ID、runtime lane、依赖、Spec refs、产物、验收/proof | `tests/core/test_spec_service.py` |
| S4 | 局部替换/重写撤销下游 approval 并标记 stale | `tests/core/test_spec_service.py` |
| S5 | delivered Spec 不进入 active list，但显式读取仍可用 | `tests/core/test_spec_service.py` |
| S6 | SpecBroker 默认解析最新 active Spec，不选 delivered 旧 Spec | `tests/runtime_core/test_spec_broker_tool.py` |
| S7 | SpecBroker 写阶段会创建阻塞式 `spec_stage_approval` | `tests/runtime_core/test_spec_broker_tool.py` |
| S8 | 未批准 Spec 阻止 runtime dispatch | `tests/chat_runtime/test_supervisor_runtime_finalization.py` |
| S9 | Runtime taskBrief 只能引用当前 run 绑定的 SpecBrief | `tests/runtime_core/test_runtime_tool_access.py` |
| S10 | Completion gate 等待审批而不是误判完成 | `tests/chat_runtime/test_supervisor_runtime_finalization.py` |
| S11 | 成功交付后标记 `spec.lifecycle.delivered` | `tests/chat_runtime/test_supervisor_runtime_finalization.py` |

## Integration / Live 前置

- 同 workspace 多 Spec：当前 run 的 specId 优先；无 run 绑定时只选 latest active。
- 同题旧 Spec 已 delivered：不得默认注入或被 `spec_broker` 自动选择。
- 打回任一上游阶段：下游 stage stale，runtimeExecutionAllowed=false。
- tasks approved 后：Supervisor 只能通过 `runtime_broker(route)` 进入执行型 runtime。
- Runtime handoff/proof/artifact：必须引用 `specId` 与 requirement/task ids。

## Live 验收记录项

- `sessionId`
- `runId`
- `specId`
- 三段 approval 记录
- Runtime taskBrief 中的 `context.specBrief.specId`
- runtime episode ids
- handoff/proof/artifact refs
- delivered lifecycle
