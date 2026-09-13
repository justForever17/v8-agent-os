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

Admin首切片独立复验见 `ADMIN-CCBCD590-REVIEW.md` 与 `reports/admin-ccbcd590-review.json`。`verify-admin-candidate.mjs`只连接被明确交付的22828候选，前提是负责人确认实际commit和冻结状态；它不会为指定Git hash自动切换服务版本。

后续生产候选见 `ADMIN-D951D9CC-REVIEW.md`：原失败项通过，新增焦点/镜头及页面状态问题仍须处理。`graph-observation.mjs`记录真实绘制与资源调用；后台信号注入与真实页面切离分层报告。

Extensions生产候选见 `EXTENSIONS-09DFCC3C-REVIEW.md`：原来源/目标/footer通过，结构错误文案和继承的stdio参数保真问题分别保留；真实安装和业务API不由合成UI层证明。

Admin后续定向闭环见 `ADMIN-251D31E6-REVIEW.md`：焦点、相机过渡、安全两类失败恢复和治理文案5项通过，范围不扩大为全站/整合验收。

Extensions定向闭环见 `EXTENSIONS-84959E0F-REVIEW.md`：X10可读错误和F03精确argv在生产UI/冻结函数边界分别通过，保留整合与真实安装验收限制。

Phone原生独立阻断见 `PHONE-82ED1C88-REVIEW.md`：同会话/跨会话草稿通过，原生配对B及B切回A均触发VideoPlayer已释放错误。`verify-phone-native.py`只接受显式`--live`及移交的合成AVD，核对安装APK与文件hash；首次配对用例要求仅已有A，后续P05/P10要求已有A/B及本脚本草稿。`verify-phone-storage-boundary.mjs --candidate <commit>`执行冻结生产模块与内存SQLite反例，结果不能替代原生UI或OS故障注入。

Admin+Extensions共享包组合验收见 `INTEGRATION-C03B7EBB-REVIEW.md`：22938只使用公开合成owner，22928保留协调模型live。两脚本支持`--url`并记录实际target/build；`admin-surface-observation.mjs`跨脚本对照真实shell/CSS。新增X15捕获祖先overflow裁切下根scrollWidth不变的窄屏不可达按钮，不用shell一致的通过项抵消该故障。

Phone新release定向闭环见 `PHONE-6111D7C6-REVIEW.md`：实际AVD ABABA及A/B分别活动态进程重启保留身份/消息/草稿，原切换崩溃不再复现。S06对照82ed失败/6111通过，验证submitting恢复unknown而保留消息ID/指纹/新输入；native、存储替身、作者terminal/长流和物理机仍分层陈述。

扩展管理动作行闭环见 `INTEGRATION-829B34A3-REVIEW.md`：原X15裁切判据在两主题390/桌面通过，保存状态单行；X16以实际坐标点击保存/刷新并观察合成响应反馈，X05/footer和X14/header定向回归通过。原c03失败记录保留，不重写成首轮全绿。

真实桌面Preview见 `SHELL-PRODUCTION-FIRST-REVIEW.md`：指定preview-state-1真实Electron/服务，30轮按钮往返保留文档/草稿/选择/滚动；发现CLI活进程误删账、public snapshot丢queue、驻留主题不同步。`seed-shell-review.py`只为本任务API创建会话写合成数据且不建run；`shell-resident-scenario.mjs`为实跑判据，`verify-shell-production.mjs`用于后续queue/theme定向复验，绝不mock成功或烧模型。

第二次真实Preview见 `SHELL-7F69D6BB-REVIEW.md`：主题双向及重复激活、已有queue冷加载/编辑/取消已闭环；新草稿反例证明同一IDB身份键中已落盘text在reload后被scroll-only记录覆盖，继续阻断。旧组合测试FAIL不改写，新增独立draft/cold-draft入口供修后验证。

Phone传输后续候选见 `PHONE-49DB49EA-REVIEW.md`：独立原生ABABA及协调要求的一次A重启通过；P10A单次判据不替换原P10，传输故障/LAN/长流作者证据与本lane最窄主流程分别保留。

所有结果必须记录 source commit、fixture、环境、命令、预期/实际和首个偏离。复验新候选先核对回调/API 变化，再更新适配层；保持反例与用户行为 oracle。
