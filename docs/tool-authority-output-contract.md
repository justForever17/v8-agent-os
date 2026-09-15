# Tool authority and output surface contract

基线：`5764c32c99b676f02605de5a95d2a291bad0a2f2`（CANDIDATE_FACT）。

## 首个偏离

已复现的策略缺陷：`toolPolicy.mode=allowlist` 且嵌套 `allowedTools=[]` 时，旧 truthiness 合并会回退到更宽的顶层 allowlist。现在按字段存在性解析，并在真实 ToolNode 验证允许、禁止、未选中和显式空集。

MCP 候选来自已配置的 MCP pool，原生 runtime group 只枚举原生工具。将 `filter_visible_tools_for_actor` 再套到自定义 MCP 名称会使已允许的工具不可达，这项错误过滤已撤销；不能把 `runtimeAccess=[]` 单独解释为 MCP 禁用。

## 唯一决策顺序

```text
native registry -> actor + runtimeAccess projection ─┐
configured MCP pool -> MCP selector ────────────────┤
                                                   ↓
                  task policy + Capsule/domain guard
                                                   ↓
                          bound schema + ToolNode execution
```

`core/tool_authority.py` 负责 task policy 解析，生产调用者在 `graph/agent_factories.py`。它不取代 MCP 配置池、插件授权或原生 runtime 权限。`mode=none` 永远为空；`mode=allowlist` 的空数组是 deny-all；显式 `mode=default` 的空数组保留当前归一化默认语义。`policyDigest` 进入 route cache key，`visibleToolSetDigest` 描述实际工具集合。别名和其他领域授权尚未全仓收敛。

## 输出面

Runtime Surface 继续使用已有观察记录。Agent 回执保留执行所需控制 ID、修复参数、proof 与 detailRef。Human 的结构化 fallback 渲染 typed summary/action，引用提取不再展示内部控制 ID。无生产调用者的独立 output_surface_contract 已删除；这些定向改动不代表三种输出面的整体重构已完成。

设计目标是让成功、拒绝、工具集合变化、partial、超预算和恢复使用清晰一致的合同；当前仍有各领域投影，需要逐个核对。JSON 在执行前不得切半或宽松补全。

## 反例

1. 已配置 MCP + selector 命中 + task allowlist 允许：模型绑定和 ToolNode 均可达，合成动作执行一次；未选中、forbidden 或显式空 allowlist 时均零次执行。
2. 嵌套 `mode=allowlist` 且空集、顶层有 `read_native_file`：最终 surface 为空。
3. dispatch 回执包含 `delegationId=d1`：Agent projection 与 compact cross-episode result 都保留 `d1`，Human projection 不含该字段。
4. 工具集合从 2 变 35：`visibleToolSetDigest` 变化，旧 route cache 不得复用；实际 Agent 是否正确使用新增工具还需真实模型验证。

## 回滚与边界

当前为整合候选，无数据库 schema 迁移。MCP 真实 builder/ToolNode 正反例、原错误消融和相邻工具合同已验证；真实跨图 A/B 验收仍未通过，不得从这些局部结果宣称已发布或整体重构完成。
