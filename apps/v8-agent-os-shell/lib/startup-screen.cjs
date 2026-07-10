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

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>V8 Agent OS</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: Inter, "SF Pro Display", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      --bg: #f7f8fb;
      --text-base: #d9e0ea;
      --text-mid: #8d98a8;
      --text-dark: #252b34;
      --text-shine: rgba(255, 255, 255, 0.92);
      --text-shadow: rgba(15, 23, 42, 0.14);
      color: #111827;
      background: var(--bg);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      overflow: hidden;
      background: var(--bg);
    }
    .brand-stage {
      display: grid;
      justify-items: center;
      gap: 32px;
      padding: 48px;
      -webkit-app-region: drag;
    }
    .product-mark {
      width: min(24vw, 220px);
      height: min(24vw, 220px);
      min-width: 150px;
      min-height: 150px;
      object-fit: contain;
      filter: drop-shadow(0 28px 64px rgba(15, 23, 42, 0.22));
      user-select: none;
      -webkit-user-drag: none;
    }
    .brand-text {
      position: relative;
      display: inline-block;
      white-space: nowrap;
      font-size: clamp(14px, 2.5vw, 32px);
      line-height: 1.18;
      font-weight: 900;
      letter-spacing: 0.015em;
      background:
        linear-gradient(
          102deg,
          transparent 0%,
          transparent 43%,
          rgba(255, 255, 255, 0.4) 47%,
          var(--text-shine) 50%,
          rgba(255, 255, 255, 0.32) 53%,
          transparent 57%,
          transparent 100%
        ),
        linear-gradient(180deg, #f8fafc 0%, var(--text-base) 35%, var(--text-mid) 68%, var(--text-dark) 100%);
      background-size: 220% 100%, 100% 100%;
      background-position: -140% 0, 0 0;
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      animation: text-shine 2.6s linear infinite;
      text-shadow: 0 12px 34px var(--text-shadow);
      user-select: none;
      will-change: background-position;
    }
    @keyframes text-shine {
      from { background-position: -140% 0, 0 0; }
      to { background-position: 140% 0, 0 0; }
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #05070d;
        --text-base: #eef2f8;
        --text-mid: #aeb8c8;
        --text-dark: #687386;
        --text-shine: rgba(255, 255, 255, 0.98);
        --text-shadow: rgba(148, 163, 184, 0.16);
        color: #f8fafc;
      }
      .product-mark { filter: drop-shadow(0 28px 64px rgba(255, 255, 255, 0.12)); }
    }
    @media (prefers-reduced-motion: reduce) {
      .brand-text {
        animation: none;
        background-position: -140% 0, 0 0;
        will-change: auto;
      }
    }
  </style>
</head>
<body>
  <main class="brand-stage" aria-label="V8 Agent OS 正在启动">
    ${markUrl ? `<img class="product-mark" alt="" src="${markUrl}" />` : ""}
    <div class="brand-text">V8 Agent OS</div>
  </main>
</body>
</html>`;
}

module.exports = { buildStartupHtml };
