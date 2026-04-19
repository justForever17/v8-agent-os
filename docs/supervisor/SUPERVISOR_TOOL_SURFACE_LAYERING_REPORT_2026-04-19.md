# Supervisor 工具面分层与后续合并建议报告

更新时间：2026-04-19  
采样仓：`E:\Projects\v8chat\v8-agent-os`  
采样方式：按当前本机真实配置构建 `supervisor` 静态 toolset，再结合本轮 route 后 prompt 快照核对

## 结论摘要

这轮继续把 `web` 与 `s3` 家族收成 broker 之后，`supervisor` 的单轮 `system content` 已经从上一版 `18787` 字符降到 `14415` 字符，实际压缩 **23.27%**，仍落在原计划 `18% ~ 26%` 的区间里。

但这次减负的主贡献来自：

1. memory 被动注入重构  
2. stock `V8_AGENT_OS.md` 去重  
3. OpenClaw 条件暴露  

`supervisor` 的**静态 direct tool 总面**已经从 **45 个** 降到 **40 个**。  
在普通非 plugin-host 轮次下，route 后可见工具面进一步降到 **34 个**。

因此下一步最合理的方向不是继续堆例外，而是把工具面正式分成三层：

1. **常驻核心**
2. **按意图 / runtime 状态再暴露**
3. **退出主 prompt，仅保留内部或 debug/operator 面**

## 当前静态工具面事实

当前静态 `supervisor toolset` = **40**：

- `orchestration`: 3
- `baseline_system`: 15
- `rpa`: 3
- `computer_use`: 5
- `web`: 1
- `network_supervisor`: 1
- `s3`: 1
- `memory`: 5
- `plugin_host`: 6

### 当前静态清单

#### `orchestration`

- `create_agent`
- `delegate_parallel`
- `fetch_skill_instructions`

#### `baseline_system`

- `ask_user`
- `download_media_for_vision`
- `grep_search`
- `http_request`
- `read_background_output`
- `read_native_file`
- `run_system_command`
- `send_background_input`
- `share_workspace_file`
- `terminate_background_command`
- `update_todo`
- `vision_media_analyzer`
- `wait`
- `write_native_file`
- `write_todos`

#### `computer_use`

- `computer_use_desktop_capabilities`
- `computer_use_execute_task`
- `computer_use_list_apps`
- `computer_use_observe_scene`
- `computer_use_resolve_execution_route`

#### `memory`

- `mem_delete`
- `mem_update`
- `memory_map_expand`
- `memory_read_day`
- `memory_recall`

#### `web`

- `web_broker`

#### `rpa`

- `rpa_list_robot_scripts`
- `rpa_run_draft`
- `rpa_run_existing_flow`

#### `s3`

- `s3_broker`

#### `network_supervisor`

- `delegate_network_task`

#### `plugin_host`

- `openclaw-lark.feishu_app_scopes`
- `openclaw-lark.feishu_bitable`
- `openclaw-lark.feishu_chat`
- `openclaw-lark.feishu_doc`
- `openclaw-lark.feishu_drive`
- `openclaw-lark.feishu_wiki`

## 三层分法建议

### 第一层：常驻核心

这层应该长期稳定留在主 prompt 中，因为它们代表 `supervisor` 的主编排职责，或者是高频的最小执行入口。

建议保留：

- `create_agent`
- `delegate_parallel`
- `fetch_skill_instructions`
- `ask_user`
- `read_native_file`
- `grep_search`
- `run_system_command`
- `write_native_file`
- `share_workspace_file`
- `update_todo`
- `write_todos`
- `wait`
- `vision_media_analyzer`
- `download_media_for_vision`
- `memory_recall`
- `memory_read_day`
- `memory_map_expand`
- `mem_update`
- `mem_delete`
- `computer_use_list_apps`
- `computer_use_desktop_capabilities`
- `computer_use_resolve_execution_route`
- `computer_use_execute_task`
- `computer_use_observe_scene`

判断标准：

- 是主链编排入口
- 是常见跨任务能力
- 没有更高层 broker 可以完全替代
- 不暴露它会显著影响日常完成率

### 第二层：按意图 / runtime 状态再暴露

这层不是“永远不暴露”，而是应该按任务意图、route 结果或 runtime 状态动态放进当前轮次 prompt。

建议进入这一层：

- `web_broker`
- `web_read`
- `web_extract`
- `web_search`
- `delegate_network_task`
- `s3_upload_file`
- `s3_list_objects`
- `s3_download_file`
- `rpa_list_robot_scripts`
- `rpa_run_draft`
- `rpa_run_existing_flow`
- 所有 `openclaw-lark.*`
- `read_background_output`
- `send_background_input`
- `terminate_background_command`
- `http_request`

推荐暴露条件：

- `web_*`
  - 用户消息或 route 明确涉及网页搜索、网页阅读、页面抽取
- `delegate_network_task`
  - 任务涉及外网协同、代理网络执行、外部网络环境差异
- `s3_*`
  - 用户明确提到对象存储、bucket、上传下载归档
- `rpa_*`
  - route 判断命中已有机器人脚本 / flow
- `openclaw-lark.*`
  - 这轮已实现：`bridgeReady=true` + inventory 真 + 用户当前轮次相关
- `read_background_output / send_background_input / terminate_background_command`
  - 只有在已经开了 session command 的情况下才暴露或强调
- `http_request`
  - 只在确实需要低层 HTTP、而 `web_*` 不足时放出来

### 第三层：退出主 prompt，仅保留内部或 debug/operator 面

这层本轮没有直接改代码，但已经能看出一些候选项。

优先候选：

- `read_background_output`
- `send_background_input`
- `terminate_background_command`
- `rpa_list_robot_scripts`
- `rpa_run_draft`
- `rpa_run_existing_flow`
- `http_request`
- `delegate_network_task`
- `s3_upload_file`
- `s3_list_objects`
- `s3_download_file`
- 所有 `openclaw-lark.*`

注意：

- 这里的含义不是“删实现”
- 而是退出默认常驻的 `supervisor` 主 prompt
- 保留为内部 broker 子步骤、debug/operator 面或按需动态暴露

## 低频 / 低收益候选的具体判断

### 1. 命令会话三件套

- `read_background_output`
- `send_background_input`
- `terminate_background_command`

问题：

- 它们其实依赖 `run_system_command(mode=session)` 的上下文
- 不属于大多数普通轮次的第一选择
- 长期裸露在主 prompt 里，会让模型背一整套 session CLI 语法

结论：

- 当前还能工作，但更适合从“常驻 direct tools”收口成**命令会话 broker**

### 2. `http_request`

问题：

- 与 `web_fetch / web_read / web_extract / web_search` 有明显功能重叠
- 它更偏 debug/operator 低层工具

结论：

- 适合作为第二层按需暴露，甚至未来退出 supervisor 主面

### 3. `rpa_*`

问题：

- Supervisor 真正需要的是“调用流程”，而不是同时理解 `draft` 和 `existing_flow` 两种入口差异

结论：

- 当前可保留实现
- 但 prompt 面应该继续收口

### 4. `s3_*`

问题：

- 对大多数本地 runtime 会话是低频需求
- 单独占 3 个 tool 槽位

结论：

- 更适合按意图暴露，而不是常驻

### 5. `openclaw-lark.*`

问题：

- 桥没连好时是纯噪声
- 即便桥是好的，也不应在普通轮次抢 supervisor 注意力

结论：

- 这轮已经先把它收成“桥接真实可用 + 本轮相关”才暴露
- 这是对的，后续不建议再回退

## 建议的未来合并路线

### 1. 命令会话 broker

把下面 4 个入口收成一套更高层的会话能力：

- `run_system_command`
- `read_background_output`
- `send_background_input`
- `terminate_background_command`

建议未来统一成：

- `command_session_execute`
- 或 `run_system_command` 带更明确的 `mode=session_broker`

这样 `supervisor` 只需要表达：

- 运行什么
- 是否需要交互
- 当前下一步要发什么

而不是自己背整套会话生命周期。

### 2. Web broker（本轮已完成）

本轮已把下面 4 个入口从 supervisor 主面收成一个更清晰的语义面：

- `web_fetch`
- `web_read`
- `web_extract`
- `web_search`

当前主面统一为：

- `web_broker(mode=search|fetch|read|extract)`

底层实现没有删除，仍作为 broker 子能力保留；变化的是 supervisor 不再需要直接背 4 个近似名字。

### 3. S3 broker（本轮已完成）

本轮也已把下面 3 个入口收成一个高层入口：

- `s3_upload_file`
- `s3_list_objects`
- `s3_download_file`

当前主面统一为：

- `s3_broker(mode=upload|list|download)`

同样地，底层实现仍在，主变化是 prompt 面降噪。

### 4. RPA broker

把下面两个主执行入口收成一个：

- `rpa_run_draft`
- `rpa_run_existing_flow`

建议未来统一成：

- `rpa_execute_flow`

再通过参数区分：

- `draft`
- `existing`
- `reuse`

Supervisor 不需要在 prompt 层面理解两个工具的分叉。

## 对 prompt 压缩的意义

### 已实现部分

本轮实际已实现的压缩主因：

1. memory 四层并列 -> 三层结构
2. `Current Time` 统一但不增量
3. stock `V8_AGENT_OS.md` 去重
4. OpenClaw 动态工具条件暴露
5. `web` 与 `s3` 家族 broker 化

真实导出结果：

- 上一版快照：`18787` 字符
- 当前快照：`14415` 字符
- 实际压缩：`23.27%`

### 如果继续落实工具面分层

如果按这份报告继续把第二层 / 第三层的大部分候选收出默认主 prompt，保守预期：

- 在当前版本基础上，还能继续再压 **9% ~ 15%**
- 相对于更早的 prompt 版本，总压缩空间大约可到 **28% ~ 38%**

## 风险与取舍

### 会减少什么

- 默认 prompt 中可见的工具家族会更少
- supervisor 需要更依赖 route 和 broker，而不是自己直接记低层工具语法

### 不会减少什么

- 底层实现能力本身不会丢
- debug/operator 能力不会被删除
- OpenClaw / RPA / S3 / HTTP 等能力仍然可以保留，只是从“常驻噪声”变成“按需暴露”

## 本轮建议的最终结论

这轮只改 OpenClaw 条件暴露、memory 注入、时间真相和 stock prompt 去重，是合理的。

下一轮真正值得继续做的，不是继续抠单句文案，而是：

1. 先把 `background session / web / rpa` 三个家族设计成 broker
2. 再把低频 direct tools 从常驻 prompt 中移出
3. 最后再对 capability registry 的高层卡片做更准确的 runtime family 映射

也就是说：

> 下一轮 supervisor prompt 减负的主战场，应该是“工具面正式分层 + 家族 broker 化”，而不是继续往 system prompt 里堆新的解释文本。
