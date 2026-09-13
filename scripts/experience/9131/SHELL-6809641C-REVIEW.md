# 真实Shell草稿reload与冷恢复闭环

**原先“已确认落盘的正文在reload后被scroll-only记录覆盖”的反例，在6809641c真实生产Shell中已通过。** 新进程也恢复了A/B新合成正文、A选择范围3–9及历史中段scrollTop4142。这里证明修复防止再次丢失，没有声称找回旧bug已擦掉的A原值。

候选 `6809641c9c4f084958db1fe112db34ed33a5ef54`，Web修复 `8a99afe364de460f116df0b27a7bba0bd38cccb2`；本lane独立核对关键draft/hooks/InputArea/ChatWindow源码差异为空。Web BUILD_ID `cemuxJH120eA9OnLCfkCc`、Admin `w3O2_UjivS-DgjLZ1HZBq`。root已清理package占用并明确交回同preview-state-1真实默认服务；未访问22930/live-state-1。

| 实际步骤 | 可观察结果 |
|---|---|
| 原反例：fill→等700ms→readonly IDB→reload→等1500ms | before同key已保存text marker、revision7；after同key仍有text及selection等字段、revision8；界面正文精确相同。原判据未放宽 |
| 完整Shell停止后另一新进程 | PID36576经原CLI停止，新PID78856冷进入A，恢复marker；不是同一个WebContents的热返回 |
| A/B与关键状态 | 为B写入新的合成marker并确认持久；A用真实键盘选择3–9，实际wheel滚到历史中段4142，IDB记录bottom=false/top4142；A→B→A和一次Web/Admin实际按钮往返均保持A正文/选择/位置与同文档identity |
| 另一新进程完整冷恢复 | 准备中段状态的Shell82764已停，新Shell66676恢复A正文、选择3–9、top4142；只读IDB的完整四元组与预期相同；B新正文也恢复，最终返回A |
| 队列保留 | A仅已编辑A1、B仅B1，读取各1条；未重复取消、编辑或提交执行 |

首个附加state run的wheel正向滚动时已在底部，不能用它证明中段恢复。脚本随后改为按当前可滚范围计算真实wheel增量，并断言离顶部/底部均超过100px；严格中段run和其后的新进程验证均通过。这个补充不改变已经通过的正文reload原反例。没有为了纯字段修复重复30轮性能测试。

4个真实进程run对应判据为1/2/1/1项通过，各0FAIL、0uncaught pageerror、0模型chat提交。完整证据 `reports/shell-6809641c-review.json` 保留每个进程、原CLI停止回执、IDB字段、选择/滚动、A/B结果和截图hash。`verify-shell-production.mjs`加入draft-state/cold-state及只读期望文件，用同一实际keyboard/wheel/冷启动动作复测。

```powershell
node scripts/experience/9131/verify-shell-production.mjs --live --allow-side-effects --state-root E:/Projects/v8chat/.codex-tmp/release-20260913-1/preview-state-1 --candidate 6809641c9c4f084958db1fe112db34ed33a5ef54 --manifest tmp/experience-9131/shell-production-9ff4121b/sessions.json --out tmp/experience-9131/shell-production-6809641c-draft --only draft
node scripts/experience/9131/verify-shell-production.mjs --live --allow-side-effects --state-root E:/Projects/v8chat/.codex-tmp/release-20260913-1/preview-state-1 --candidate 6809641c9c4f084958db1fe112db34ed33a5ef54 --manifest tmp/experience-9131/shell-production-9ff4121b/sessions.json --out tmp/experience-9131/shell-production-6809641c-state-mid --only draft-state
node scripts/experience/9131/verify-shell-production.mjs --live --allow-side-effects --state-root E:/Projects/v8chat/.codex-tmp/release-20260913-1/preview-state-1 --candidate 6809641c9c4f084958db1fe112db34ed33a5ef54 --manifest tmp/experience-9131/shell-production-9ff4121b/sessions.json --out tmp/experience-9131/shell-production-6809641c-cold-state --expected-state tmp/experience-9131/shell-production-6809641c-state-mid/draft-state-expected.json --only cold-state
```

原始输出/截图在experience worktree `tmp/experience-9131/shell-production-6809641c-*`。全部测试Shell已由指定state的原CLI `stop --only shell`准确停止，最后PID66676；默认端口/源码/Next冻结已明确释放root。未清除state、队列或历史，工作区只有验收文件变更。

本轮挑选selection与scroll作为关键附属状态，没有实际上传非空source/file、测试IME或断电。scroll结果是该固定fixture的数值位置保留，不是所有历史分页/语义阅读锚点证明。最后截图还可见合成长历史080后出现001，尚未区分fixture同时包含nodes/content_text与实际renderer重复，本轮未验证全文去重，已另告root，不将草稿闭环扩写为历史全文渲染通过。主题/queue既有真实闭环保留为之前候选证据，安装包、真实provider和最终发布由协调验收。无产品修改、push或tag。
