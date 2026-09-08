# Research 私有旧入口退休记录

核查日期：2026-09-08。源码基准：`fa3fe2ee3d7764cce41af79483949dc9d52d6f57` 加本轮尚未提交的 ResearchAgent 修复；不是已发布版本的验收结论。主责：Research runtime / `v8os-action-kernel`，退休方法：`v8os-repository-reduction`。

## 本次范围

| compatId | 私有入口 | 原始作用与退休理由 |
| --- | --- | --- |
| research-private-review-reconciliation | `_architect_reconcile_review_with_answer_surface` | 旧代码用关键词和表面格式覆盖独立审核的拒绝意见；当前 ResearchAgent 的有界审阅循环负责修订，代码只校验结构、引用与实读凭据。 |
| research-private-output-profile | `_architect_segmented_writer_profile` | 按旧 metadata 数字推导分段和输出上限；当前 Agent 自主决定提交全文或保存段落，预算由 `model_token_policy` 解析。 |
| research-private-query-architect | `_invoke_web_research_architect_query_plan` | 额外调用 LLM 生成分片查询并运行实体矩阵兜底；当前查询由 ResearchAgent 工具循环决定。 |

后两入口均来自提交 `434bc639`（2026-08-02）。三者本次分类为 `internal_private`，决定为 `remove`。退休操作未改变 REST、工具 schema、配置、数据库、安装或外部兼容合同，因此无需给这些未被调用的私有函数添加新的兼容窗口。本轮另有公开答案分页与获取参数改动，不能把整轮变更都描述为无接口变化。

## 删除证据

- 审阅调和入口另计 1979 行，完整符号与动态注册核查未见生产调用；移除 22 个只验证旧覆盖行为的测试及 3 个混合测试的专属尾段，保留混合测试原有独立断言。`test_research_review_retirement.py` 验证 reviewer 拒绝不会被表面关键词改成接受；接受仍需实际修订、审阅和有效引用。
- 删除前完整阅读两函数：分别 37、602 行，均无装饰器、无注册或导出声明；模块 AST 中函数名的生产 `Name Load` 均为 0。
- 全仓精确符号搜索覆盖生产源码、测试、脚本、配置、workflow 和文档；排除依赖、构建缓存、Git 对象和运行日志。只有定义与 7 个旧测试：查询入口 5 个，输出 profile 2 个。
- 进一步检查模块导入、`getattr`、`globals`、字符串前缀、native tool 注册和 RuntimeEpisodeRunner：外部生产入口显式使用公开 `research_broker` 或进度 reporter，未发现动态调用这两个函数。
- 7 个旧测试全部以这两函数的返回值为判定对象。一个查询测试用其他 helper 分类其返回值，并无独立 helper 行为断言；删除该测试，保留其他 helper 及其独立测试。
- 仅移除 5 个独占常量：`_RESEARCH_ARCHITECT_QUERY_PLAN_MAX_TOKENS`、`_RESEARCH_ARCHITECT_QUERY_PLAN_TIMEOUT_SECONDS`、`_RESEARCH_ARCHITECT_SECTION_MAX_TOKENS`、`_RESEARCH_ARCHITECT_IDEAL_ANSWER_MIN_CHARS`、`_RESEARCH_ARCHITECT_IDEAL_ANSWER_MAX_CHARS`。
- 仅从 broker 移除两个失去调用者的 import：`RESEARCH_PROMPT_CONTRACT_VERSION`、`research_runtime_prompt_digest`。它们在共享 prompt 模块中的定义及测试保留；不递归清理其他 `_helpers` 或常量。

后两入口的删除源码 650 行、旧测试 496 行；审阅调和入口如上另计。新增回归与本记录另计；死代码删除不构成运行性能提升证明，也不代表整个 broker 已完成瘦身。

## 当前替代路径与行为验证

公开 `research_broker` → `_run_agent_owned_research` → `_execute_research_agent` → `ResearchAgent.run`；当前 Agent 决定搜索/实读/提交，独立 reviewer 决定接受或要求修订。能力包、模型与网络接口未被改动。

新增 `test_current_broker_agent_owns_queries_and_full_submission_under_configured_budget` 覆盖实际 broker/Agent/模型调用边界，使用隔离来源和脚本化 provider：

- Agent 选择精确查询与 `bing_cn`；一次搜索、一次实读，随后提交完整答案、独立审核，只有这 4 次模型工具决策，没有额外 LLM 查询规划或被 profile 强制分段。
- 遗留 `global_max_tokens=1500` 与 `model_record.maxTokens=1500` 共存时，`auto` 调用不发送 `max_tokens`，`fixed` 保留人工 1500 预算。
- 最终答案、可交付状态、实际来源读取凭据一致。现有 reviewer 否决、partial、引用绑定、连续修订和经验复用用例继续执行。
- 在独立 pytest 进程中注入旧隐式 1500 cap，`auto` 用例按预期失败；该错误不会被当成成功或继续走伪造答案。

沿途同步修正一个已在删除前失败的 progress fixture：搜索事件现为 `active → source_read → completed`；验证首尾同 `nodeId`，保留读取摘要和不泄露 URL 查询参数的原断言，未修改生产进度代码。

## 验收与回滚

从 Engine 目录执行：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/core/test_research_broker.py tests/core/test_research_segmented_writer.py tests/core/test_research_review_retirement.py tests/core/test_research_agent.py tests/core/test_research_model_call.py -q --tb=short
```

- 补齐当前行为回归和 progress fixture 后、删除前：487 passed。
- 删除后：480 passed；差额为明确退休的 7 个旧测试。
- 实际仅逆向恢复本次删除 hunk，运行旧查询/profile 与当前 Agent 回归：11 passed；再应用同一删除补丁，上述完整相关组仍为 480 passed。
- Python 编译及 `git diff --check` 通过。此记录没有把 mock、回滚烟测等同 provider live、Preview 或安装包测试；本次未运行 live、重启服务、提交或推送。

恢复时只逆向本次退休 hunk；不要整文件回退到基准提交，以免覆盖同时完成的获取、预算、引用与来源归因修复。历史函数的恢复不会迁移或回写配置和状态库。
