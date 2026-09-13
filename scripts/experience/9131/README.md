# 9.13.1 独立体验验收

此目录只含验收工具与全合成 fixture，不修改生产实现。默认工作树必须是分配的 experience checkout；候选复验使用协调任务给出的 commit 和隔离 UI 地址。

证据分为 `SOURCE`、`BOUNDARY_EXECUTED`、`RENDERED`、`INTERACTED`、`MUTATION_VERIFIED`、`NATIVE_SIMULATOR`、`PHYSICAL`；未执行为 `NOT_RUN`。功能、便利性、视觉、性能分别判定，任何功能失败不能由其它分项抵消。HTTP 200、截图、源码哨兵均不能证明保存、安装或真实设备行为。

- `collect.py`：只读源码路由、版本、hash、运行能力，建立全部源码页面与子视图待验收矩阵。
- `boundary-baseline.mjs`：从生产源码 AST 提取实际回调并注入延迟/存储失败。提取失败记 harness error，不能被当作产品回归。固定基线反例不是新候选的唯一回归门禁。
- `observe_admin.py`：隔离 Admin 启动与浏览器观察；合成 API 边界，拒绝未声明写操作；只监听 22824。生成的截图/状态/日志留在本工作树忽略目录。
- `fixtures.py`：生成同 ID 双身份、未知配置字段、secret 占位、草稿/队列、50 字段、100 段 README 与确定时序输入。
- `sql-baseline.py`：把生产 CREATE/INSERT SQL 放在内存 SQLite 中检查消息与 cursor 冲突；不是 native service 验收。
- `summarize_observations.py`：从合成 browser 记录提取可提交的坐标、状态与性能样本，不提交凭据/状态根/大截图。

运行命令和首轮结论见 `BASELINE.md`。完整入口与反例见 `acceptance-matrix.md`。安装依赖前先核对该 checkout 的 package/lock；本机基线只读复用原有 Admin node_modules，使用本 worktree 独立 `.next`。不共享可变构建输出。

所有结果必须记录 source commit、fixture、环境、命令、预期/实际和首个偏离。复验新候选先核对回调/API 变化，再更新适配层；保持反例与用户行为 oracle。
