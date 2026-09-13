# 第二次真实Preview：队列/主题闭环与草稿冷恢复阻断

**主题双向同步和已有队列恢复/编辑/取消已经在真实Shell闭环；新增草稿reload丢失阻断仍未关闭。** 新反例先证明输入实际写入IndexedDB，再证明同一完整身份键被空草稿覆盖，不能用热切面30轮通过抵消。

候选 `7f69d6bb514cc3ae01e36c4ce57b0287ccee019d`，包含CLI9ff、queue8b9及主题/product-ui0.0.17；Admin BUILD_ID `w3O2_UjivS-DgjLZ1HZBq`、Web `OD7v5ILr_Pyk3KrMEHmvX`。同一明确移交的preview-state-1与默认端口，生产Electron43.4.1/Chrome150.0.7871.224。测试通过原CLI停root既有Shell，再真实bootstrap接管；所有API是真实服务，未发模型chat请求。

| 检查 | 独立结果 |
|---|---|
| 无active run的queue冷加载/AB/A | A的2条、B的1条pending均经真实API读取且页面可见；不出现另一会话queue |
| queue编辑/取消 | 实际UI将A1改为“Independent A queue edited through real UI”，取消A2；真实API与随后只读mode=ro SQLite共同确认A1 pending新内容、A2 cancelled、B1 pending保留 |
| queue reload/新Shell | 编辑后的A1在reload和额外诊断Shell冷进入A时仍可见；取消A2未再出现。准备数据不包含active run，不声明队列实际执行 |
| 双向主题 | Web设dark→Admin、Admin设light→Web，连续各2次；目标屏幕与真实canonical GET均正确。每次激活仅1个主题GET，DOM焦点true；5次现有openWeb IPC产生真实重复visible时，额外主题GET为0 |
| 草稿reload FAIL | 新Shell初始A已空；再用当前同一页面重新建立完整独立反例，确认是持久内容丢失，详见下段 |

草稿反例步骤：实际textarea输入纯合成 `Independent_durable_A_reload_9131` → 等700ms → 只读IndexedDB `v8-composer-drafts-v1/drafts`，确认当前A条目text匹配、revision6、saved/hydrated=true → 实际page.reload → textarea出现后再等1500ms → 输入为空，持久条目变revision1且只剩scroll/files。前后key的instance、principal、workspace、session四元组逐字相同。没有把输入框刚出现时的空值直接判故障。

额外诊断Shell的实际 `app.getPath` 为preview-state-1/electron/shell，sessionData为其下Session Data，与新构建首次run一致。第一次30轮未记录完整草稿key，因此不假称那次key也已比对；当前同Shell同key、已落盘→reload的反例已经排除身份漂移。没有读取cookie/凭据，也没有把真实用户草稿当fixture；全部读数来自本任务A/B。

源码候选是hydrateDraft以整体revision是否不变决定恢复整份saved，而初始scroll可先升revision，随后flush只有scroll的记录覆盖正文。这里尚未记录put/delete发生顺序，不能把这个候选写成已实测的事件时间线。实际JSON前后字段已交给root和Web owner，他们在自己的工作树修复；本lane不修改产品。

原组合queue测试在完成显示、编辑、取消后，于reload草稿断言退出，因此原run保留queue项FAIL；没有改旧结果成绿。后续真实queue API/新Shell可见性和只读SQLite回读单独闭环已有queue语义，草稿失败继续单列。主题为该run完整PASS。0uncaught pageerror、0模型chat提交。

证据 `reports/shell-7f69d6bb-review.json` 汇总两个真实Shell的路径、队列、主题及草稿前后字段；原始run在 `tmp/experience-9131/shell-production-7f69d6bb/`，复现草稿在 `shell-production-draft-diagnosis/`。`verify-shell-production.mjs`新增独立draft/cold-draft判据及queue进展记录，避免后续某项失败掩盖其它独立层级；新draft入口待修片真实运行，当前反例通过临时控制会话执行。

两个Shell70968/62176均已由指定state原CLI stop --only shell明确停止，默认服务已全部释放root做package smoke；不清history、queue和IDB。原A正文在错误复现中已丢失，不能宣称可恢复；B原合成草稿仍需后续读取确认。修复后将重新建立可审计marker再验reload/新进程恢复。无push/tag或最终发布通过声明。
