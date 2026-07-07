function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function buildStartupHtml(options = {}) {
  const markUrl = escapeHtml(options.markUrl || "");
  const detail = escapeHtml(options.detail || "正在启动 Engine / Admin / Web...");

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>V8 Agent OS</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, "SF Pro Display", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #f7f9fc;
      color: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 18% 12%, rgba(139, 92, 246, 0.12), transparent 30%),
        radial-gradient(circle at 80% 10%, rgba(14, 165, 233, 0.10), transparent 28%),
        linear-gradient(135deg, #fbfcff 0%, #eef3f9 100%);
      overflow: hidden;
    }
    .topbar {
      height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 28px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.24);
      background: rgba(255, 255, 255, 0.74);
      backdrop-filter: blur(22px);
      -webkit-app-region: drag;
    }
    .brand {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      font-weight: 850;
      letter-spacing: 0;
      font-size: 20px;
    }
    .brand img {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      box-shadow: 0 10px 24px rgba(124, 58, 237, 0.16);
    }
    .badge {
      font-size: 12px;
      color: #64748b;
      border: 1px solid rgba(148, 163, 184, 0.35);
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(255, 255, 255, 0.66);
    }
    .topbar-right {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      -webkit-app-region: no-drag;
    }
    .window-controls {
      display: inline-flex;
      align-items: center;
      overflow: hidden;
      border: 1px solid rgba(148, 163, 184, 0.28);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.7);
    }
    .window-controls button {
      width: 40px;
      height: 30px;
      border: 0;
      background: transparent;
      color: #64748b;
      font-size: 13px;
      cursor: default;
    }
    .window-controls button:hover { background: rgba(226, 232, 240, 0.7); color: #0f172a; }
    .window-controls button:last-child:hover { background: rgba(255, 228, 230, 0.8); color: #e11d48; }
    main {
      min-height: calc(100vh - 68px);
      display: grid;
      place-items: center;
      padding: 32px;
    }
    .panel {
      width: min(520px, 88vw);
      border-radius: 28px;
      border: 1px solid rgba(148, 163, 184, 0.28);
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 24px 80px rgba(15, 23, 42, 0.10);
      padding: 34px;
      text-align: center;
    }
    .orb {
      width: 52px;
      height: 52px;
      margin: 0 auto 18px;
      border-radius: 18px;
      background: conic-gradient(from 0deg, #7c3aed, #06b6d4, #22c55e, #f59e0b, #7c3aed);
      animation: spin 1.4s linear infinite;
      box-shadow: 0 14px 36px rgba(124, 58, 237, 0.22);
    }
    h1 {
      margin: 0 0 10px;
      font-size: 26px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    p {
      margin: 0;
      color: #64748b;
      font-size: 14px;
      line-height: 1.8;
    }
    .steps {
      margin-top: 24px;
      display: grid;
      gap: 10px;
      text-align: left;
    }
    .step {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-radius: 14px;
      background: rgba(248, 250, 252, 0.9);
      padding: 11px 14px;
      color: #334155;
      font-size: 13px;
    }
    .pulse {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #8b5cf6;
      box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.45);
      animation: pulse 1.2s ease-out infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.45); }
      100% { box-shadow: 0 0 0 12px rgba(139, 92, 246, 0); }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      ${markUrl ? `<img alt="" src="${markUrl}" />` : ""}
      <span>V8 Agent OS</span>
    </div>
    <div class="topbar-right">
      <div class="badge">Preview</div>
      <div class="window-controls" aria-label="窗口控制">
        <button type="button" aria-label="最小化" onclick="window.v8osShell && window.v8osShell.minimize()">-</button>
        <button type="button" aria-label="最大化或还原" onclick="window.v8osShell && window.v8osShell.toggleMaximize()">□</button>
        <button type="button" aria-label="隐藏到托盘" onclick="window.v8osShell && window.v8osShell.close()">×</button>
      </div>
    </div>
  </header>
  <main>
    <section class="panel" aria-live="polite">
      <div class="orb"></div>
      <h1>正在准备 V8OS</h1>
      <p>${detail}</p>
      <div class="steps">
        <div class="step"><span>Engine 运行核心</span><span class="pulse"></span></div>
        <div class="step"><span>Admin 配置中心</span><span class="pulse"></span></div>
        <div class="step"><span>Web 聊天界面</span><span class="pulse"></span></div>
      </div>
    </section>
  </main>
</body>
</html>`;
}

module.exports = { buildStartupHtml };
