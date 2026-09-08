# Agent 指导、提示上下文与缓存计量验收

基线：`7ba4890f4ecf8cfd22536880cbc66fd4785db384`（2026-09-08）。
本文件描述本轮变更和可复现测试，不把本机 live 视为全供应商或物理机验收。

## 权威链及改动

- 子/孙 Agent 工作准则与现有文件版本凭据一致：首次读、成功写续用版本、外部修改/过期重读，写集和完整性检查继续生效。移除无关角色的多媒体指导与短命令专用旧规则。
- Supervisor 手动委派使用较窄的工具投影，模型 schema 要求本地任务精确目标名，并枚举合法 mode。运行时内部派发、终止型镜像与显式外部 worker 仍走原校验/执行 owner；没有新增调度器或权限源。
- Supervisor/worker 稳定提示排在动态状态前；环境与 registry 标签完整。删除两份重复的环境切段实现及委派的固定 28 消息裁剪，保留分支隔离，token 压缩仍由 ContextOrchestrator 负责。
- 缓存段核对偏移、覆盖及 hash；无效元数据保持正文但退出主动缓存，不回退成整段主动缓存。普通凭据保护指令不再抹掉静态段；实际凭据值仍阻止主动断点。
- Anthropic 多 system 合并保留有效分段，无效分段退为动态；主动断点覆盖稳定末段。
- 用量读取统一兼容 SDK/OpenAI/Anthropic 等格式。原生 Anthropic 输入与缓存相加一次；SDK 总输入不重复加。缓存读取/写入未知与零分开，V8 本地响应复用不重记供应商费用。
- Admin 普通调用记录显示缓存读写；汇总注明报告覆盖率和保留日志范围。费用为未计供应商缓存折扣的估算，不是账单。
- 自动预算仅在协议必填时采用已保存的官方/在线权威能力。Models API 的 max_input_tokens/max_tokens 能进入已有事实来源链。支持流式且未显式指定传输时，自动长请求默认流式，避免 Anthropic SDK 的非流式长请求拒绝。

## 行为矩阵

| 测试入口 | 关键反例与 oracle |
| --- | --- |
| `runtime_core/test_delegation_prompt_alignment.py` | 缺失/空目标不能通过手动 schema；外部任务仍可表达；注入状态和证据原样转发；第一个工具读取结果经过统一上下文准备仍存在 |
| `runtime_core/test_subagent_model_budget.py` | 保留早期消息不重置工具次数；同一 actor 的写入结果停止强制工具调用 |
| `prompt_cache/test_prompt_prefix_integrity.py` | 动态时间/负载/任务变化时稳定前缀逐字相同；标签闭合；坏 hash/偏移/类型不崩溃、不添加主动缓存；实际凭据及之后内容无主动断点 |
| `runtime_core/test_model_cache_usage.py` | raw/SDK 用量归一；零/未知；本地重放不重计费；callback→隔离 DB→公开 dashboard；历史混合数据及日志清理 |
| `model_control/test_model_token_policy.py` | auto/fixed/短请求、来源不明不迁移；官方能力 8192/65536/131072；实际 Anthropic SDK 经 HTTP transport fixture 的流式 wire 参数 |
| `tests/chat_runtime/test_context_orchestrator_governance.py` | 持久压缩基线、最近轮次、复用和恢复 |
| Admin `tests/model-cache-usage.test.cjs` | 中英文 React 实际渲染，未知和 0 不混淆，报告覆盖率不冒充全量命中率 |

改前最小基线 132 项通过。新断言使用基线真实函数注入后，早期证据保留 1 项、协议能力 6 项均失败；新实现通过同一组。没有修改工作树以切换旧实现。
旧“消息必须不超过 28 条”的断言被替换，因为它奖励了证据丢失；新判据检查具体 claim/引用/版本以及完整工具配对。

## 本机验证

- Engine 最终相关组 544 项通过（包含 Models API 与 SDK wire/传输反例），另有多媒体工具面 16 项通过。
- Admin TypeScript、i18n 和 React 渲染通过；Admin/Web production build、Preview rebuild 通过。最终 Engine 修改单独重启验收。
- 真实已配置 M3 的两次隔离流式请求：输入均 1008 token，缓存读取分别 128、1007，缓存写入未报告；耗时 4.21s、1.39s；自动输出未附加 cap。样本只有两次，不构成性能基准或所有长任务提速结论。
- 另一次真实 Supervisor 短任务完成，Admin API/普通记录/刷新对账：缓存读取 128，写入未报告；既有历史显示未报告。后台实际配置模型的零缓存记录显示 0。

窄 live 使用既有 harness，仅调用明确选择的配置 provider，观测写入新隔离目录，凭据不复制：

```powershell
python tests/scripts/run_prompt_cache_streaming_live_matrix.py --live --require-all --provider minimax --repeat 2 --isolated-db-root <new-private-test-directory> --output <private-report-path>
```

## 恢复与残余边界

- 无 DB schema 或用户配置迁移；缓存明细在原 invocation metadata，旧消费者忽略新增字段。按 scoped commit revert 后重新构建/重启可回滚；当前数据结构无需逆向迁移。
- 已存在且未保存的缓存用量无法补算，清理调用日志后不能从总量账本推测缓存。缓存统计与累计 token 账本的覆盖范围分开显示。
- 协议必填且没有可信能力时仍使用 `required_parameter_default=32768`，这是兼容请求预算，不是模型能力。删除条件：对应 provider 能稳定提供能力元数据或支持可省略参数；不得仅按旧数值迁移人工设置，也不得用上下文窗口猜输出容量。owner：model_token_policy；后续预算收敛继续跟踪。
- 显式非流式调用仍须符合供应商 SDK 的超时/长请求规则；不会为通过请求而削减用户预算。
- 未执行真实 Anthropic provider、多轮完整工程/调研、跨平台安装包或物理机验收；不能将 SDK transport fixture 标为这些层级通过。
