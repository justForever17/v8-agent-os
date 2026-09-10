# V8OS Repository Entry

默认中文沟通。直接打开本 Git 仓库也必须遵守本入口；若父工作区的 `../AGENTS.md` 和 `../.agents/skills/v8os-development-command/SKILL.md` 存在，先读取并按任务加载最小 Skill 集。独立 clone 缺少这些文件时，以本文件、当前源码和测试为依据，不猜历史快照。

- Supervisor First, Runtime Grounded：Supervisor 理解与决定执行；runtime/hint/Memory/gate 提供能力与证据，不恢复第二个聊天 Planner。
- 2026-09-09 项目阶段：用户确认目前主要为测试数据，无需为假想旧用户维持双轨兼容。仍保护真实凭据、配置、明确保留的产物与他人改动；检查实际外部协议和动态调用者后才删除。
- 先复现并定位首个偏离。Agent 误操作先查实际 prompt、tools/schema、上下文/返回值、角色冲突和截断，不先责怪模型或新增硬门禁。
- 比较小修、复用现有能力、替换错误 owner；选总机制和维护成本最低且可验证的方案。巨型模块按责任拆解，不做纯目录搬家，不保留无消费者的 facade。
- 普通免审操作按用户授权放行；OS/V8 执行内核、认证密钥与明确恶意行为保留具体保护。路径位于 `.v8-agent-os` 或 `.agents` 不自动等于内核。Task Capsule/writeSet/身份/版本约束仍生效。
- 单次模型输出、上下文、累计成本、预览限长、时间/步骤和文件正确性分别处理。自动输出预算协议允许时不加 cap，人工配置保留；catalog 不得冒充用户设置的权威。
- Research Agent 评质量/时效，代码证明实际读取/引用/持久化；禁止通用字数/来源数硬接收门槛。partial、review、接受和入库不得混称成功。
- Engine 是执行真相；`packages/session-realtime` 统一实时和历史投影。UI/刷新、handoff 与产物需同源；共享包改动重新 pack 并同步消费者 lock/integrity。
- 测试入口 `apps/v8-agent-os-engine/tests/README.md`；联网/真实 provider 测试显式 `--live`。P0/P1 用能击败旧错误的反例；mock、构建、Preview、安装包和物理机分层陈述。
- CLI/Shell/Web/Admin/桌宠与打包变更按影响验证生产构建、`v8os preview --rebuild` 和真实启动；原生登录/提权组件验证取消、重复、秘密不外泄和卸载恢复。请求送达不是操作成功。
- 手工改动使用 patch；不回滚他人改动，不提交 token、cookie、原始日志或状态库。故障 SQLite 只读；测试用隔离状态。禁止 destructive git。
- 同时最多一个 Codex 子代理，不继续派生。后台隔离桌面设想已搁置。
- 推送和发布依用户授权；先核对提交 CI，再推新统一 tag `v8-os-vYYYY.MM.DD.N`，不强移 tag。交付写清验证与残余风险。
