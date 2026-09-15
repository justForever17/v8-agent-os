# Tool authority and output surface contract

基线：`5764c32c99b676f02605de5a95d2a291bad0a2f2`（CANDIDATE_FACT）。

## 首个偏离

工具目录/registry 产生候选；`filter_visible_tools_for_actor` 产生 actor/runtime pool；但 `graph/agent_factories.py` 的 MCP selector 分支曾把 selector 结果直接拼回 `available_tools`。因此 selector 既是相关性选择器又隐式成为授权源。另一个独立偏离是 `toolPolicy.allowedTools=[]` 通过 `or` 回退到顶层 allowlist。

## 唯一决策顺序

```text
registry/manifest (候选)
  -> actor + runtimeAccess projection (授权池)
  -> MCP/Skill selector (只裁剪)
  -> ToolAuthorityDecision (task policy + Capsule/domain guard)
  -> bound schema + ToolNode execution
```

`core/tool_authority.py` 是 task policy 的 canonical owner。策略字段按“是否存在”解析：`mode=none` 永远为空；`mode=allowlist` 的空数组是 deny-all；显式 `mode=default` 的空数组只表示默认继承元数据。每次决策可输出 `policyDigest`、`visibleToolSetDigest` 与拒绝原因，权限变化必须使旧 surface 失效。

## 输出面

Runtime Surface 由原始观察记录保存来源、版本、时间、actor、`rawSha256`；Agent Surface 只保留完成下一步所需的状态、完整 `toolCallId`/`delegationId`/`invocationId`、`detailRef`、修复参数和 proof；Human Surface 只渲染结果、状态、风险和下一步，不解析 raw JSON，也不回退到内部 ID。

成功、拒绝、工具集合变化、partial、超预算和恢复都沿同一结构返回。未知状态保持 `UNKNOWN`；JSON 在执行前不得切半或宽松补全。

## 反例

1. `runtimeAccess=[]` + selector=`secret_mcp`：schema 不得进入模型输入，执行回执为 `policy_denied`。
2. 嵌套 allowlist 为空、顶层有 `read_native_file`：最终 surface 为空。
3. dispatch 回执包含 `delegationId=d1`：Agent projection 与 compact cross-episode result 都保留 `d1`，Human projection 不含该字段。
4. 同一工具集合从 2 变 35：`visibleToolSetDigest` 变化，旧 route cache 不得复用。

## 回滚与边界

本切片只改独占分支；无数据库 schema 迁移、无外部协议删除。回滚为恢复前一提交。未运行 live provider、Preview、UI、安装包和真实桌面动作；这些交给总任务按其发布门禁验收。
