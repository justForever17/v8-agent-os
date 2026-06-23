<div align="center">
  <img src="./docs/assets/banner.svg" alt="V8 Agent OS Banner" width="800">
</div>

<div align="center">
  <strong>记住上下文 · 收束工具噪音 · 过程透明可视 · 强兜底可接管</strong>
</div>
<br>

<div align="center">

[![OS](https://img.shields.io/badge/Platform-Win_|_Mac_|_Linux-green.svg?style=for-the-badge&color=050505&labelColor=111111)](#)
[![Node](https://img.shields.io/badge/Runtime-Node.js-orange.svg?style=for-the-badge&color=050505&labelColor=111111)](#)
[![Security](https://img.shields.io/badge/Security-Fail--Closed-red.svg?style=for-the-badge&color=050505&labelColor=111111)](#)

</div>
<br>

> 🌐 [**English documentation available here**](./README.md)

## 🪐 划时代的 Agent 机器生态

**V8 Agent OS** 并非另一页花哨的“聪明聊天界面”，而是一座有着极强防护装甲与跨端调度能力的 **Agent 运行生态 (Runtime)**。

如果你受够了“同一个项目反复跟模型解释”、“未知 Skills 导致失控盲跑”以及“任务中途断裂无法干预接管”——V8 将为你打造一台拥有全局视野记忆、可观测、甚至可中途拔网线重来的企业级治理引擎。

---

## 🛡️ 核心战争级抽象能力

<table width="100%">
  <tr>
    <td width="50%">
      <h3>1. 全自主 Auto-runtime</h3>
      <p>彻底告别关闭浏览器就崩盘的虚假独立。结构上强隔离 <strong>Control Plane (Admin 9528)</strong> 与 <strong>Execution Core (Engine 9530)</strong>。这一硬核隔离让连续数日的无人值守任务能够在后台稳健存活、断点唤醒，并静候人工指令。</p>
    </td>
    <td width="50%">
      <h3>2. 逆向接管 OpenClaw 生态</h3>
      <p>抛弃重启炉灶。通过黑科技 <code>v8-bridge</code>，V8 原生劫持并全量兼容 OpenClaw 庞大的开源插件社区，将其化为你的前遣火力，并牢牢套上 V8 的安全约束与执行管线。</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>3. 精算级成本路由</h3>
      <p>系统原生拦截并向下路由至免费的本地小模型接管杂活与长文本摘要，避免 Token 账单爆炸，将最昂贵的前沿模型推理算力专门留给不可替代的核心锋刃。</p>
    </td>
    <td width="50%">
      <h3>4. 白盒安全隔离审批</h3>
      <p>盲猜死挂未知 Skills 约等于向黑客开门。遇到极危指令环境，V8 立刻在图层面挂起任务流，并在控制台强制请求人工安全越权审批。哪怕藏着的 <code>rm -rf</code> 也休想逃过你的凝视。</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>5. MCP & Skills 归一化预筛</h3>
      <p>一次加载海量插件也不怕污染上下文。顶层 Reranker 将数百个零散的 MCP 工具与原生 SKILLS 统一步调进行“归一预筛”，并在最恰当的时间向当前节点只暴露最精准的利刃。</p>
    </td>
    <td width="50%">
      <h3>6. 干预节点的记忆手术床</h3>
      <p>记忆不再是被动读取的单向文本流。V8 后座常驻专属 Memory Agent，并在前端提供可视化知识图谱。允许人类随时对特定记忆长链进行精细的手术级别调整与覆写。</p>
    </td>
  </tr>
</table>

---

## ⚡ 极速起飞 (Bootstrap)

无需在一堆子模块中繁琐配置。引擎自带跨平台的单行命令即可拉起 Admin 控制台与 Execution Engine。

### Windows (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.ps1 | iex"
```

默认 bootstrap 会启动 Engine `9530` 与 Admin `9528`。如果要做本地 `os-web` pairing / login / profile 回归，可在已 clone 的仓库根目录显式启动 Web：

```powershell
.\bootstrap.ps1 --services engine+admin+web
```

启动后 Web 入口为 `http://127.0.0.1:9527`，Admin 仍为 `http://127.0.0.1:9528`。

可先运行 `node scripts/verify-bootstrap-web-mode.mjs` 做 bootstrap dry-run 校验；该脚本不会安装依赖或启动服务。

### macOS / Linux (Bash)
```bash
curl -fsSL https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.sh | bash
```
> *(注：若已 clone 到本地目录，只需直接运行目录下的 `./bootstrap.ps1` 或 `./bootstrap.sh` 即可构建。默认不启动 Web；本地 Web 回归请用 `.\bootstrap.ps1 --services engine+admin+web`。)*

---

## ⚙️ 第一序列启动指南

1. 服务就绪后，**优先访问 Admin 治理中枢：** `http://127.0.0.1:9528`
2. **必需动作**：配置主干大模型基座；
3. **极度重要**：配置 Reranker 选项映射。如果不配置，工具挂载质量和长期记忆回流将呈现悬崖式断档；
4. 保存配置，启动 Web 端 `http://127.0.0.1:9527` 开启你的协同之旅。

---

## 📚 研发级军火库 (文档体系)

请按照下方顺序建立你对 V8 Engine 运行的全局认知：

*   [🚀 快速入门（项目级）](./docs/V8_AGENT_OS_QUICK_START_ZH.md)
*   [📖 开发者指南（项目级）](./docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
*   [🔌 API 参考（项目级）](./docs/V8_AGENT_OS_API_REFERENCE_ZH.md)
*   [🎛️ 配置指南（项目级）](./docs/V8_AGENT_OS_CONFIG_GUIDE_ZH.md)

---

<div align="center">
  <h3>支持 V8 Agent OS 持续演进</h3>
  <p>如果你希望这套系统继续向着更工业级、更深度的自动执行运转舱衍化，欢迎给予支持：</p>
  <a href="https://afdian.com/a/justforever17"><strong>https://afdian.com/a/justforever17</strong></a>
</div>
