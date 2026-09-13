# ACP 接入验收

核对日期：2026-09-13。ACP 是本机编辑器到 V8OS Supervisor 的协议适配；运行、工作区、审批、历史仍由现有 Admin/Engine 管理。

## 协议依据与入口

- [ACP v1 Prompt Turn](https://agentclientprotocol.com/protocol/v1/prompt-turn)：提示为 ContentBlock 数组，输出为 session/update，完成必须有 stopReason；submit accepted 不能替代终态。
- [Session Setup](https://agentclientprotocol.com/protocol/v1/session-setup)：cwd 固定；load 必须重放历史。使用持久 Engine sessionId，不能生成进程退出就丢失的别名。
- [Tool Calls](https://agentclientprotocol.com/protocol/v1/tool-calls)：工具结果保留实际调用身份、完整可见内容与状态；审批回复必须送回执行 owner。
- [Elicitation](https://agentclientprotocol.com/protocol/v1/elicitation)：仅在客户端声明 form 后使用 elicitation/create；ask_user 不是安全授权。Spec 阶段决策仍转 Admin，不冒充普通工具授权。

编辑器配置：command 为 `v8os`，args 为 `["acp"]`。先启动本机 V8OS；需要自定义端口时设置非秘密的 `V8OS_ADMIN_URL`。ACP 从 loopback Admin 的现有 local-session 入口取得内存会话，不使用 Network API key，不要求在 argv/env 中复制 token。

明确的 cwd 与普通 `v8os chat --workspace` 一样，经 `/api/client/projects` 确认项目，再创建绑定会话；不修改全局默认工作区。会话模式 manual/reduced/minimal 通过标准 session/set_mode 映射既有 safetyApprovalMode；工具、Capsule、OS/V8 核心边界不由 ACP 重写。

## 自动化分层

在 Engine 目录执行：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/acp_bridge -q
```

| 层 | 文件 | 必须杀死的错误实现 |
| --- | --- | --- |
| 纯合同 | test_acp_bridge.py | prompt 数组变 Python repr；输出/工具内容被暗裁；EOF、failed、waiting 冒充成功；load 改 cwd；历史工具与正文乱序 |
| framing | test_acp_simulation_matrix.py | UTF-8 编码漂移；非 JSON stdout；错误消息令进程退出；通知收到非法 response |
| 真实 CLI + HTTP fixture | test_acp_stdio_integration.py | Node 命令不存在；token 配错；prompt 阻塞取消；审批只更新内存未 POST；ask_user 冒充授权；SSE 恢复重复答案、混入其他 run |

HTTP fixture 是实际 Node/Python 子进程、socket 与协议，但不是模型或真实 Engine。只在下面入口明确 `--live` 时使用本机配置的真实 Supervisor：

```powershell
.venv/Scripts/python.exe -X utf8 tests/scripts/run_acp_live_audit.py --live --case prompt
.venv/Scripts/python.exe -X utf8 tests/scripts/run_acp_live_audit.py --live --case ask
.venv/Scripts/python.exe -X utf8 tests/scripts/run_acp_live_audit.py --live --case cancel
.venv/Scripts/python.exe -X utf8 tests/scripts/run_acp_live_audit.py --live --case approval
```

live 创建专用临时工作区/会话。approval 只删除脚本刚创建的一份可丢弃临时文件，须真正收到审批且确认文件副作用；口令出现在答案不算动作成功。报告仅保留 session/run、时长、事件数、答案 hash 和断言，不输出秘密、原始事件或真实配置。

## 仍需补齐的能力与验收

- 非空客户端 mcpServers 明确报不支持，避免无声忽略；这意味着尚不能宣称完整 ACP v1 合规（标准要求 stdio MCP）。需复用 V8OS Plugin Manager 的任务级注册与授权，不直接启动未经治理的客户端命令。
- 图像/音频输入未接附件 owner，初始化明确声明 false；内嵌文本资源可用。
- 原生 IDE（Zed/JetBrains 等）、安装包 PATH、多平台和长会话背压尚需各自真实验收，不能由 Node subprocess 替代。
- Spec 决策留在 Admin；原生编辑器的文件 diff/产物预览需继续核对实际 UI。
- 单次真实 live 时长只能证明这次链路和测点；不能当 P95 或跨版本性能结论。
