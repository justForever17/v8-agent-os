# Phone Canvas 与会话权威测试矩阵

更新时间：2026-08-15

## 1. 证据等级

| 等级 | 含义 | 不能替代 |
| --- | --- | --- |
| STATIC | 源码和接线断言 | 运行时行为 |
| UNIT / CONTRACT | 纯函数、临时数据库和隔离合同测试 | 真实进程、真实网络和 UI |
| MOCK | 故障注入或假 provider | 真实 provider 和设备媒体栈 |
| DESKTOP_PROXY | 真实 Engine/Admin/Web 与 Phone bundle/代理 | Android 解码、WebView、系统文件保存 |
| EMULATOR | 安装包、adb UI tree、截图和 logcat | 物理设备性能与厂商系统差异 |
| REAL_DEVICE | 真实配对设备、屏幕证据、logcat 和下载文件 | 真实付费 provider |
| REAL_PROVIDER_LIVE | 真实 provider、产物 URL 和重载恢复 | 其他设备型号与长期稳定性 |

状态只使用 `PASS`、`FAIL`、`BLOCKED`、`NOT-RUN`。`BLOCKED` 表示缺少权威生产能力；`NOT-RUN` 表示当前环境没有相应设备或未执行付费路径。

## 2. Human Surface 与路由

| ID | 等级 | 场景 | 通过标准 | 当前结果 |
| --- | --- | --- | --- | --- |
| SURFACE-01 | CONTRACT | Canvas 用户消息，中/英 | Phone/Web 只显示“本消息来自画布”或“This message was sent from the canvas” | PASS |
| SURFACE-02 | CONTRACT | live/history/reload Canvas envelope | 气泡与复制内容均不含附件、合同、内部 ID、路径、mask 或执行字段 | PASS |
| SURFACE-03 | CONTRACT | 普通聊天附件 | 正常附件不被 Canvas Human Surface 判定或隐藏 | PASS |
| ROUTE-01 | UNIT | 合法 typed Canvas direct | 直达 Supervisor/creative_media，不产生 Vision preannounce | PASS，Engine 5 项正反组之一 |
| ROUTE-02 | UNIT | 普通图片附件 | 保留既有 Vision 前置，不能借 Canvas 通道绕过 | PASS，Engine 5 项正反组之一 |
| ROUTE-03 | CONTRACT | 缺 contract、operation 不一致、附件 lineage 不一致 | fail closed 为 invalid，不降级成特权或普通聊天 | PASS |
| ROUTE-04 | UNIT | queued/promoted Canvas 与普通附件 | 提升执行时重验精确 contract；普通附件恢复自己的 Vision scope | PASS |

复现命令：

```powershell
cd packages/session-realtime
npm test

cd ../../apps/v8-agent-os-phone
node --test tests/*.cjs

cd ../v8-agent-os-web
node --test tests/creative-canvas-contract.test.cjs
node --test tests/workbench-human-surface-contract.test.cjs
```

## 3. Session -> Workspace 权威层级

| ID | 等级 | 场景 | 通过标准 | 当前结果 |
| --- | --- | --- | --- | --- |
| BIND-01 | CONTRACT | source/artifact/graph/run/operation 的 2 session x 2 workspace 投影 | 五类记录仅接受 sessionId 与 workspaceId 同时精确匹配的项 | PASS |
| BIND-02 | CONTRACT | Phone artifact list/detail/preview/open | 每层缺 session/workspace 或不匹配时 fail closed | PASS |
| BIND-03 | UNIT | 两会话、两工作区历史与图状态 | cross-session、cross-workspace、旧 revision 均拒绝 | PASS，隔离 DB/纯函数证据 |
| BIND-04 | UNIT | Engine 重启恢复 | orphan run 变为 interrupted/retryable，历史仍按 session 隔离 | PASS，临时 DB 重启协调测试 |
| BIND-05 | UNIT / MOCK | provider artifact URL 恢复 | 清空内存映射后从 artifact DB 恢复 external_url；下载入库不丢 URL | PASS，假 provider/临时 DB |
| BIND-06 | REAL_PROVIDER_LIVE | Phone 历史重载真实 provider URL | Engine 重启后 Phone 仍可预览、下载同一 session/workspace 产物 | NOT-RUN，本轮不重复触发付费生成 |

## 4. 身份真相

| ID | 等级 | 场景 | 通过标准 | 当前结果 |
| --- | --- | --- | --- | --- |
| ID-01 | CONTRACT | Supervisor 历史 envelope | Admin nickname/role/avatar 覆盖 `Supervisor/Lead/智能主管/主理人` 占位 | PASS |
| ID-02 | CONTRACT | live merge 与 optimistic placeholder | canonical 占位不能覆盖已配置身份，新占位使用 activeAgentProfile | PASS |
| ID-03 | UNIT | Engine durable Supervisor/subagent projection | 使用当前 Admin profile，canonical id 与显示标签不混用 | PASS，Engine 8/8 |
| ID-04 | REAL_DEVICE | Phone 概览、详情、实时流、历史重载 | 四个表面头像、昵称、身份标签一致 | NOT-RUN，无已连接设备 |

## 5. Graph Human Surface

| ID | 等级 | 场景 | 通过标准 | 当前结果 |
| --- | --- | --- | --- | --- |
| GRAPH-01 | CONTRACT | queued/running/cancelling/cancelled/failed/interrupted/recovered/completed | 只消费 canonical typed event，显示紧凑双语状态，不复制 Web Canvas UI | PASS |
| GRAPH-02 | CONTRACT | wrong schema、缺 lineage、跨 scope、summary 猜状态 | 全部拒绝，不从文案制造状态 | PASS |
| GRAPH-03 | UNIT | retry failed branch | 复用原 run/operation；成功且可能付费的祖先 attempt 保持 1 | PASS，Engine 4 项 graph 组之一 |
| GRAPH-04 | DESKTOP_PROXY / REAL_DEVICE | live/history/reload graph 状态 parity | Engine 发布 `canvas.graph.run.state` v1，Phone 实时与重载一致 | NOT-RUN（Engine producer、snapshot/history projection 与真实 Graph run 序列已通过；Phone 代理/真机 parity 尚未执行） |
| PHONE-SCOPE-01 | STATIC | Phone 图状态界面 | 只有状态与产物投影，没有节点拖拽、连线或 Web Canvas 编辑器 | PASS |

`GRAPH-04` 不允许用 summary 解析、轮询伪装或客户端自造状态绕过。Engine 已在状态提交后发布 canonical v1 event，并由 snapshot/history projection 保留；下一步使用本矩阵补 Phone 代理与真机的 live/history/reload parity。

## 6. 资源容错与预览

| ID | 等级 | 场景 | 通过标准 | 当前结果 |
| --- | --- | --- | --- | --- |
| RES-01 | CONTRACT | artifact list/detail 任一路径失败 | 另一成功路径仍可显示；错误可见且可独立重试 | PASS |
| RES-02 | CONTRACT | sources/artifacts/files 单路失败 | 不清空其他目录，不白屏，各自重试 | PASS |
| RES-03 | STATIC | 单个 source renderer 抛错 | NodeRenderBoundary 隔离坏条目，其他资源保持可用 | PASS；真机故障注入 NOT-RUN |
| VIEW-01 | CONTRACT | image/video/audio/GLB | 复用 Phone canonical renderer，保留预览与下载入口 | PASS；设备解码 NOT-RUN |
| VIEW-02 | CONTRACT | Markdown/JSON | 受限内联读取、Markdown/JSON renderer、超限/二进制/失败提示与重试 | PASS |
| VIEW-03 | CONTRACT | 私有、Admin 同源、签名或 opaque external URL | 当前无权威 public provenance，PDF/PPT 一律不进入 Google Docs/xdocin 等第三方 Viewer | PASS；设备网络抓包 NOT-RUN |
| VIEW-04 | CONTRACT | 下载与外部 fallback | 先经鉴权内容接口缓存；只有 typed external_url 可外部打开 | PASS |

## 7. 构建、打包与设备

| ID | 等级 | 检查 | 当前结果 |
| --- | --- | --- | --- |
| BUILD-01 | CONTRACT | Phone `typecheck` | PASS |
| BUILD-02 | CONTRACT | Phone i18n | PASS |
| BUILD-03 | CONTRACT | Phone tests | PASS，20/20 |
| BUILD-04 | CONTRACT | Shared build/tests | PASS，63/63 |
| BUILD-05 | CONTRACT | Web Human Surface | PASS，13/13 |
| BUILD-06 | CONTRACT | Web Canvas | PASS，13/13 |
| BUILD-07 | BUILD | Shared tgz 与 Phone/Web/Admin/Pet lock integrity | PASS，四端同一 integrity |
| BUILD-08 | BUILD | Phone Android export / Expo doctor | PASS，Android bundle 导出；Expo Doctor 19/19；React Native 0.83.10 与 SDK 55 对齐 |
| PROXY-01 | DESKTOP_PROXY | `v8os preview --rebuild`、9530/9528/9527 | PASS，Admin/Web production build；Engine `/health`、Admin/Web 根页面 HTTP 200；Shell 存活；Engine 使用 `pythonw.exe`，服务子进程无可见终端窗口；Engine 稳态 10 次 `/health` 平均 1.82 秒、P95 2.36 秒 |
| DEVICE-01 | EMULATOR / REAL_DEVICE | adb 配对、截图、UI tree、logcat、crash | NOT-RUN，`adb devices -l` 无设备 |
| SAFE-01 | STATIC | `git diff --check`、reverse-apply、scoped commit | PASS，39 个本线路文件已通过 staged diff check 与 reverse-apply；提交哈希见交付记录 |

## 8. Engine 窄测

以下结果使用隔离测试数据库或 mock provider，不是 Phone 真机或真实付费 provider 证据：

| 分组 | 结果 | 覆盖 |
| --- | --- | --- |
| Canvas direct / 普通 Vision 正反组 | PASS，5/5 | direct 跳过 Vision；普通附件保留 Vision；promoted 重验 |
| Graph scope / history / restart / retry | PASS，4/4 | 串区拒绝、revision fence、orphan recovery、付费祖先不重跑 |
| Source/workspace/provider URL | PASS，5/5 | 当前 session ledger、持久资源重编译、URL 入库与恢复 |
| Supervisor/subagent identity | PASS，8/8 | Admin 配置身份、durable projection、canonical id |

## 9. 回滚与残余风险

回滚前先执行只读检查：

```powershell
git diff --binary HEAD -- <scoped-files> | git apply --check --reverse --whitespace=nowarn
```

残余风险必须继续保留在交付记录中：

- Engine graph canonical realtime producer 尚未实现，因此 Phone graph live parity 当前为 `BLOCKED`。
- Engine 冷启动后的首次 `/health` 探测曾超过 20 秒，背景 Skill/MCP 暖机结束后 10 次稳态采样平均 1.82 秒、P95 2.36 秒；本轮没有改 Engine 启动链，不能把该冷启动尖峰声明为已修复。
- 当前环境没有 Android 模拟器或真机，媒体解码、系统下载、WebView、网络抓包和视觉一致性均不能宣称通过。
- 3D 使用本地 WebView，但 `model-viewer` runtime 仍来自 CDN；模型私有 URL不会投递第三方 Viewer，运行时代码供应链仍需后续本地化。
- Canvas direct classifier 以 `sourceId` 集合校验附件，受信客户端伪造同 ID 不同 URL 时仍依赖 Engine/source ledger 的后续权威校验；后续应在 Engine 入口把附件 URL 归一到 source ledger。
- 未引用的 `PDFFileCard` / `PPTCard` 旧组件仍留在仓内，需遵循弃用流程后续清理，本轮不做越界删除。
- npm 安装审计存在仓库既有依赖告警；本次没有使用破坏性自动升级。
