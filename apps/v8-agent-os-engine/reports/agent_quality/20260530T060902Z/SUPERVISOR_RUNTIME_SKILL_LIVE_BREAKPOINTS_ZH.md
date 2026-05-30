# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T060902Z
- 模型标签：`doubao-seed-2-0-pro`
- Engine：`http://127.0.0.1:9530`（报告内敏感路径已脱敏）

## 结论概览

| 等级 | 数量 |
| --- | --- |
| P0 | 1 |
| P1 | 0 |
| P2 | 0 |

## Case 结果

| Case | 状态 | Session | Run | 延迟 | 工具 | Runtime topics |
| --- | --- | --- | --- | --- | --- | --- |
| engine_unavailable | failed |  |  | 0 ms |  |  |

## 失败与整改


### P0

#### engine_unavailable - Engine unavailable

- 摘要：Engine health check failed before live audit.
- 涉及模块：apps/v8-agent-os-engine
- 建议修复：先启动 Engine，再运行 live audit。
- 回归测试：`manual live smoke`

<details>
<summary>证据</summary>

```text
URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
```

</details>


## 详细回答摘录

### engine_unavailable

- 标题：Engine unavailable
- 最终回答摘录：未找到 assistant 最终文本

<details>
<summary>关键事件</summary>

```text

```

</details>
