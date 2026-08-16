import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const testDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(testDir, "..");
const repoRoot = path.resolve(webRoot, "../..");
const webpackModule = require(path.join(webRoot, "node_modules/next/dist/compiled/webpack/webpack"));
const webpack = webpackModule.webpack;
const { loadBindings } = require(path.join(webRoot, "node_modules/next/dist/build/swc"));
const { chromium } = require(path.join(repoRoot, "apps/v8-agent-os-admin/node_modules/playwright"));
const sharp = require(path.join(webRoot, "node_modules/sharp"));

const reportDir = path.resolve(
  process.env.V8_CANVAS_VISUAL_REPORT_DIR
    || path.join(os.homedir(), ".v8-agent-os/reports/canvas-inspector-review"),
);
const edgePath = path.resolve(
  process.env.V8_EDGE_PATH
    || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
);

function compileFixture(tempRoot) {
  const entryPath = path.join(tempRoot, "entry.tsx");
  const outputPath = path.join(tempRoot, "dist");
  const entry = String.raw`
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { LocaleProvider } from "@fixture-locale";
import { CanvasInspectorReviewPanel } from "@fixture-inspector";

const resultNode = {
  nodeId: "fixture-result",
  kind: "result",
  origin: "placeholder",
  x: 0,
  y: 0,
  width: 280,
  height: 190,
  title: "品牌主视觉",
  mediaType: "image",
  producerActionNodeId: "fixture-action",
};
const imageA = {
  id: "fixture-a",
  origin: "artifact",
  name: "品牌主视觉 · v2.png",
  mimeType: "image/png",
  mediaType: "image",
  url: "/image-a.png",
};
const imageB = {
  id: "fixture-b",
  origin: "artifact",
  name: "品牌主视觉 · v1.png",
  mimeType: "image/png",
  mediaType: "image",
  url: "/image-b.png",
};
const versions = [
  { identity: "version-a", resultNodeId: "fixture-result", version: { outputVersionId: "version-a", version: 2 }, resource: imageA },
  { identity: "version-b", resultNodeId: "fixture-result", version: { outputVersionId: "version-b", version: 1 }, resource: imageB },
];
const action = {
  label: "生成品牌主视觉",
  definition: {
    actionId: "creative_media.generate_image",
    binding: { kind: "creative_media", capability: "image.generate" },
    inputs: [{ portId: "references", mediaTypes: ["image"], min: 0, max: 8, ordered: true }],
    output: { portId: "output", slot: "image", mediaTypes: ["image"] },
    requiresPrompt: true,
    networkRequired: true,
    mayIncurCost: true,
    providerLabel: "Agnes Images",
    modelLabel: "Image 2.1 Flash",
  },
  configured: true,
  runtimeState: { state: "succeeded", recoverable: false },
};
const runtime = {
  status: "succeeded",
  nodeStates: { "fixture-action": action.runtimeState },
  outputs: { "fixture-result": versions.map((candidate) => candidate.version) },
  recovery: { canRetry: false },
};

function Fixture() {
  const initialMode = new URLSearchParams(location.search).get("mode") === "review" ? "review" : "details";
  const [mode, setMode] = useState(initialMode);
  return <LocaleProvider initialLocale="zh-CN">
    <main className="relative overflow-hidden" style={{ height: "100vh", background: "#f4f4f5" }}>
      <div className="absolute inset-0 opacity-60" style={{ backgroundImage: "linear-gradient(#d4d4d8 1px,transparent 1px),linear-gradient(90deg,#d4d4d8 1px,transparent 1px)", backgroundSize: "24px 24px" }} />
      <CanvasInspectorReviewPanel
        node={resultNode}
        resource={imageA}
        versions={versions}
        mode={mode}
        action={action}
        inputs={[{ label: "产品正面参考.png", mediaType: "image" }, { label: "品牌色板.png", mediaType: "image" }]}
        outputLabel="品牌主视觉 · v2.png"
        graphRuntime={runtime}
        renderPreview={(resource) => <img src={resource.url} alt={resource.name} className="h-full w-full object-contain" />}
        onModeChange={setMode}
        onRetry={() => undefined}
        onClose={() => undefined}
      />
    </main>
  </LocaleProvider>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
`;
  fs.writeFileSync(entryPath, entry, "utf8");
  const config = {
    mode: "production",
    target: "web",
    entry: entryPath,
    output: { path: outputPath, filename: "fixture.js" },
    resolve: {
      extensions: [".tsx", ".ts", ".jsx", ".js"],
      modules: [path.join(webRoot, "node_modules"), "node_modules"],
      alias: {
        "@": path.join(webRoot, "src"),
        "@fixture-inspector": path.join(webRoot, "src/components/workbench/creative-canvas/inspector-review.tsx"),
        "@fixture-locale": path.join(webRoot, "src/components/providers/LocaleProvider.tsx"),
      },
    },
    module: {
      rules: [{
        test: /\.[jt]sx?$/,
        exclude: /node_modules/,
        use: {
          loader: path.join(webRoot, "node_modules/next/dist/build/webpack/loaders/next-swc-loader.js"),
          options: {
            isServer: false,
            rootDir: webRoot,
            pagesDir: path.join(webRoot, "src/app"),
            appDir: path.join(webRoot, "src/app"),
            hasReactRefresh: false,
            nextConfig: {},
            jsConfig: { compilerOptions: { baseUrl: webRoot, paths: { "@/*": ["src/*"] } } },
            supportedBrowsers: ["chrome 120"],
            swcPlugins: [],
          },
        },
      }],
    },
    optimization: { minimize: false },
    plugins: [new webpack.DefinePlugin({ "process.env.NODE_ENV": JSON.stringify("production") })],
  };
  return new Promise((resolve, reject) => {
    webpack(config, (error, stats) => {
      if (error) return reject(error);
      if (stats?.hasErrors()) return reject(new Error(stats.toString({ colors: false, errors: true, warnings: false })));
      resolve(outputPath);
    });
  });
}

async function createImage(filePath, background, accent, label) {
  const overlay = Buffer.from(`<svg width="960" height="600" xmlns="http://www.w3.org/2000/svg"><rect x="64" y="64" width="832" height="472" rx="24" fill="none" stroke="${accent}" stroke-width="8"/><circle cx="720" cy="300" r="150" fill="${accent}" fill-opacity=".82"/><text x="96" y="160" fill="#fff" font-size="54" font-family="Arial" font-weight="700">${label}</text><text x="96" y="214" fill="#fff" fill-opacity=".76" font-size="24" font-family="Arial">Canvas visual review fixture</text></svg>`);
  await sharp({ create: { width: 960, height: 600, channels: 4, background } }).composite([{ input: overlay }]).png().toFile(filePath);
}

function productionCss() {
  const cssDir = path.join(webRoot, ".next/static/css");
  return fs.readdirSync(cssDir)
    .filter((name) => name.endsWith(".css"))
    .sort()
    .map((name) => fs.readFileSync(path.join(cssDir, name), "utf8"))
    .join("\n");
}

function serve(root, css) {
  const contentTypes = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png" };
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url || "/", "http://fixture.local").pathname;
    if (pathname === "/app.css") {
      response.writeHead(200, { "Content-Type": "text/css; charset=utf-8" });
      response.end(css);
      return;
    }
    const filename = pathname === "/" ? "index.html" : pathname.slice(1);
    const target = path.resolve(root, filename);
    if (!target.startsWith(`${path.resolve(root)}${path.sep}`) || !fs.existsSync(target)) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": contentTypes[path.extname(target)] || "application/octet-stream" });
    fs.createReadStream(target).pipe(response);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function pixelEvidence(screenshot) {
  const metadata = await sharp(screenshot).metadata();
  const stats = await sharp(screenshot).stats();
  const averageDeviation = stats.channels.slice(0, 3).reduce((sum, channel) => sum + channel.stdev, 0) / 3;
  if (averageDeviation < 18) throw new Error(`Screenshot is visually blank (${averageDeviation.toFixed(2)}): ${screenshot}`);
  return { width: metadata.width, height: metadata.height, averageDeviation: Number(averageDeviation.toFixed(2)) };
}

async function assertLayout(page, viewport, mode) {
  const evidence = await page.locator("[data-canvas-inspector]").evaluate((panel, expected) => {
    const bounds = panel.getBoundingClientRect();
    const controls = [...panel.querySelectorAll("header button, header a")].map((element) => element.getBoundingClientRect());
    const overlaps = controls.some((left, index) => controls.slice(index + 1).some((right) => (
      left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top
    )));
    const overflows = [...panel.querySelectorAll("header span, dt, dd")].some((element) => element.scrollWidth > element.clientWidth + 2);
    return {
      mode: panel.getAttribute("data-canvas-inspector-mode"),
      bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
      overlaps,
      overflows,
      viewport: expected,
      computed: {
        background: getComputedStyle(panel).backgroundColor,
        color: getComputedStyle(panel).color,
        display: getComputedStyle(panel).display,
        opacity: getComputedStyle(panel).opacity,
        visibility: getComputedStyle(panel).visibility,
        zIndex: getComputedStyle(panel).zIndex,
      },
      textLength: panel.textContent?.trim().length || 0,
    };
  }, viewport);
  if (evidence.mode !== mode) throw new Error(`Unexpected inspector mode: ${JSON.stringify(evidence)}`);
  if (evidence.bounds.x < 0 || evidence.bounds.y < 0 || evidence.bounds.x + evidence.bounds.width > viewport.width + 1 || evidence.bounds.y + evidence.bounds.height > viewport.height + 1) {
    throw new Error(`Inspector escaped viewport: ${JSON.stringify(evidence)}`);
  }
  if (evidence.overlaps) throw new Error(`Inspector controls overlap: ${JSON.stringify(evidence)}`);
  if (evidence.overflows) throw new Error(`Inspector labels overflow: ${JSON.stringify(evidence)}`);
  return evidence;
}

async function capture(page, baseUrl, mode, viewport, name) {
  await page.setViewportSize(viewport);
  await page.goto(`${baseUrl}/?mode=${mode}`, { waitUntil: "networkidle" });
  await page.locator("[data-canvas-inspector]").waitFor({ state: "visible" });
  if (mode === "review") {
    await page.getByRole("button", { name: "擦除对比" }).click();
    const slider = page.getByRole("slider", { name: "擦除位置" });
    await slider.evaluate((element) => {
      element.value = "64";
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.locator("[data-canvas-ab-review]").waitFor({ state: "visible" });
  }
  const layout = await assertLayout(page, viewport, mode);
  console.log(JSON.stringify({ name, layout }));
  const screenshot = path.join(reportDir, `${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: false });
  return { screenshot, layout, pixels: await pixelEvidence(screenshot) };
}

async function main() {
  if (!fs.existsSync(edgePath)) throw new Error(`Edge executable not found: ${edgePath}`);
  fs.mkdirSync(reportDir, { recursive: true });
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-canvas-inspector-"));
  let server;
  let browser;
  try {
    await loadBindings();
    const outputPath = await compileFixture(tempRoot);
    fs.writeFileSync(path.join(outputPath, "index.html"), "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><link rel=\"stylesheet\" href=\"/app.css\"></head><body><div id=\"root\"></div><script src=\"/fixture.js\"></script></body></html>", "utf8");
    await createImage(path.join(outputPath, "image-a.png"), "#172554", "#22d3ee", "VERSION A");
    await createImage(path.join(outputPath, "image-b.png"), "#4c0519", "#fb7185", "VERSION B");
    server = await serve(outputPath, productionCss());
    const address = server.address();
    const baseUrl = `http://127.0.0.1:${address.port}`;
    browser = await chromium.launch({ executablePath: edgePath, headless: true });
    const page = await browser.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    const results = [
      await capture(page, baseUrl, "details", { width: 1280, height: 840 }, "canvas-inspector-desktop"),
      await capture(page, baseUrl, "review", { width: 1280, height: 840 }, "canvas-review-wipe-desktop"),
      await capture(page, baseUrl, "details", { width: 390, height: 844 }, "canvas-inspector-mobile"),
      await capture(page, baseUrl, "review", { width: 390, height: 844 }, "canvas-review-wipe-mobile"),
    ];
    if (pageErrors.length) throw new Error(`Page errors: ${pageErrors.join(" | ")}`);
    console.log(JSON.stringify({ status: "passed", pageErrors, results }, null, 2));
  } finally {
    if (browser) await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
    const resolvedTemp = path.resolve(tempRoot);
    if (resolvedTemp.startsWith(`${path.resolve(os.tmpdir())}${path.sep}`)) fs.rmSync(resolvedTemp, { recursive: true, force: true });
  }
}

await main();
