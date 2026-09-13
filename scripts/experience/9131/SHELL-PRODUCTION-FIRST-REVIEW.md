# 首轮真实 Preview Shell 独立验收

**真实Shell的30次Web/Admin往返保留了文档、草稿、选择和滚动；真实队列恢复与驻留主题同步各发现一处缺口。** 另在首次接管时复现CLI环境变化删除活跃Engine记录、导致Shell启动失败；9ff4121b修后原环境反例已独立通过。后续queue/theme修片尚须新生产构建实测，不能用作者合同测试提前关闭。

环境由root明确移交：`preview-state-1`全新合成状态，root执行真实`v8os preview --rebuild`，UI源码dd95abb7（后续CLI/Engine测试提交没有改变已构建UI）；Admin BUILD_ID `CW502O2e19YdrKvJ8n-CQ`，Web `IwpMPZ-zNC-CHz_jh1Mxc`，默认9530/9528/9527。不是浏览器两个标签或dev server。本lane先用指定state原CLI `stop --only shell`，再以integration已有Electron加载真实shell bootstrap；测试结束亦只走该CLI停止Shell，没有app.close连带停止核心服务，没有手写进程账。

| 边界 | 独立实测与结论 |
|---|---|
| CLI解释器环境漂移 | 首次status不带V8_ENGINE_PYTHON，活跃Engine被标external，后来record消失；OS端口PID16424/父43072与root启动账、隔离cwd相符。Shell接管进入“Engine failed to start”真实错误页。root按9ff修复并治理重新托管后，本lane再次不带该变量status，Engine42628 managed_running、pidAlive/portOpen均true |
| 首次登录 | 实际Admin表单创建公开shell-preview-fixture账户并登录，点击生产“聊天”按钮进入Web，经既有trusted client流程完成认证；没有复制token/cookie或模拟API成功 |
| 合成数据准备 | 通过真实Web→Admin→Engine API创建A/B空会话并绑定本lane临时工作区；经明确授权使用生产Database owner给这两个精确会话加入4条canonical消息、3条pending queue，run_id=None，无模型/active run。全部读取、渲染与后续操作走真实服务 |
| 30轮实际切面 | 页面“控制台”/“聊天”按钮触发IPC；Web和Admin文档UUID/timeOrigin不变，A输入全文、selection3..9、鼠标wheel后中段scrollTop5083.3335均保留；结束仍1窗口/2个产品WebContents |
| 交互时间 | 同一真实Electron/生产状态n30，点击→目标view两帧median29.45ms、P9535.80ms。包括测试同步，不是INP、真实显示器呈现测量或旧版优化比例 |
| 会话切换 | 实际sidebar A→B→A，A/B不同草稿均保留；A选择3..9/滚动5083.3335也恢复。未声称附件/IME/所有selection场景通过 |
| 队列恢复FAIL | 专用真实Web API及Engine `/v1/chat/queued-messages`返回A2条/B1条pending；Engine public snapshot无queuedMessages/queuedMessagesWindow，Web detail projection亦无，UI dock=0。不是隐藏折叠或active run过滤。首个缺失在RuntimeCommandRouter重建RuntimeSnapshotPayload漏字段 |
| 主题同步FAIL | Web实际保存dark后，切Admin等待15秒仍light；Admin主题GET返回200/dark。BrowserWindow和Admin WebContents有焦点，show/focus后二次按钮往返仍light，排除无OS焦点。ProductThemeSync仅监听普通focus/visibility，未消费Shell surface-visibility；没有重建文档来掩盖同步缺失 |

root已分别接手CLI、queue、theme根因；本lane不修改产品实现。队列专用端点存在不等于页面收到，主题API200不等于屏幕同步；这两个反例正是前期合成UI边界未能暴露的真实集成缺口。

数据准备脚本 `seed-shell-review.py`要求显式`--live --allow-side-effects --state-root`、精确指定Preview路径、当前API返回会话ID/公开owner/title、空history和空queue，才调用生产owner。没有任意SQL或假run。首次harness期待新任务首页textarea属于假设错误，实际首页先选工作区；已调整为创建并选择会话。准备脚本最初期待任意metadata经Web proxy传递，但源码确认该route只转发既定字段，改以当前创建UUID+owner/title+空状态绑定；第一次校验在任何写入前退出。以上两项是测试适配，不算产品回归。

原始过程由临时Electron控制会话执行，`shell-resident-scenario.mjs`是本轮实际执行的30轮核心判据；`verify-shell-production.mjs`将启动/队列/主题步骤收为定向复验入口，当前新入口尚待候选重建后完整运行。所有业务API均未route mock。记录453个跨表面请求、0uncaught pageerror；`/chat`的周期POST是页面server action，未当成模型提交，记录中无`/api/chat-submit`或`/api/chat`提交。没有凭据header或正文进入报告。

结果见 `reports/shell-production-first-review.json`；原始证据在experience worktree `tmp/experience-9131/shell-production-9ff4121b/`，首次启动失败另在`shell-production-dd95abb7/`。主题失败截图已保留；退出前主动点回初始light，未将其记为同步修复。最终A草稿和B草稿再次确认，Shell24952已被原CLI准确停止，默认服务冻结明确释放给root重建。

本报告没有队列实际执行、provider、clean安装包或其它物理平台通过声明。已有队列的编辑/冷恢复待8b9修复，主题待product-ui0.0.17的真实构建复验；不重复30轮来证明纯字段转发修复。无push/tag。
