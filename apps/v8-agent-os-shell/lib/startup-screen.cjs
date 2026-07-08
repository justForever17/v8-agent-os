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
      color: #111827;
      background: #f8fbff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      overflow: hidden;
      background:
        radial-gradient(circle at 50% 38%, rgba(124, 58, 237, 0.14), transparent 28%),
        radial-gradient(circle at 28% 18%, rgba(236, 72, 153, 0.10), transparent 26%),
        radial-gradient(circle at 74% 18%, rgba(34, 211, 238, 0.12), transparent 30%),
        linear-gradient(135deg, #fff7fb 0%, #f8fbff 48%, #eefaff 100%);
    }
    .brand-stage {
      display: grid;
      justify-items: center;
      gap: 26px;
      padding: 48px;
      -webkit-app-region: drag;
    }
    .product-mark {
      width: min(32vw, 210px);
      height: min(32vw, 210px);
      min-width: 132px;
      min-height: 132px;
      object-fit: contain;
      filter: drop-shadow(0 24px 48px rgba(124, 58, 237, 0.20));
      animation: breathe 2.8s ease-in-out infinite;
      user-select: none;
      -webkit-user-drag: none;
    }
    .brand-text {
      white-space: nowrap;
      font-size: clamp(44px, 8vw, 88px);
      line-height: 1;
      font-weight: 900;
      letter-spacing: 0;
      background:
        linear-gradient(100deg, #fb923c 0%, #ec4899 24%, #8b5cf6 46%, #06b6d4 68%, #22c55e 100%),
        linear-gradient(110deg, transparent 34%, rgba(255,255,255,0.92) 47%, transparent 60%);
      background-size: 100% 100%, 260% 100%;
      background-position: 0 0, -160% 0;
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      animation: shine 2.4s ease-in-out infinite;
      text-shadow: 0 18px 54px rgba(124, 58, 237, 0.16);
      user-select: none;
    }
    @keyframes shine {
      0% { background-position: 0 0, -180% 0; }
      100% { background-position: 0 0, 180% 0; }
    }
    @keyframes breathe {
      0%, 100% { transform: translateY(0) scale(1); }
      50% { transform: translateY(-3px) scale(1.025); }
    }
    @media (prefers-color-scheme: dark) {
      :root { color: #f8fafc; background: #05070d; }
      body {
        background:
          radial-gradient(circle at 50% 38%, rgba(124, 58, 237, 0.20), transparent 30%),
          radial-gradient(circle at 24% 18%, rgba(236, 72, 153, 0.12), transparent 28%),
          radial-gradient(circle at 76% 18%, rgba(34, 211, 238, 0.14), transparent 30%),
          linear-gradient(135deg, #07050d 0%, #070914 52%, #06131b 100%);
      }
      .product-mark { filter: drop-shadow(0 26px 58px rgba(34, 211, 238, 0.16)); }
      .brand-text { text-shadow: 0 20px 64px rgba(34, 211, 238, 0.14); }
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
