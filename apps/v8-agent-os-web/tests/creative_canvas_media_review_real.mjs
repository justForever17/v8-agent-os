import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const execFileAsync = promisify(execFile);
const testDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(testDir, "..");
const repoRoot = path.resolve(webRoot, "../..");
const webpack = require(path.join(webRoot, "node_modules/next/dist/compiled/webpack/webpack")).webpack;
const { loadBindings } = require(path.join(webRoot, "node_modules/next/dist/build/swc"));
const { chromium } = require(path.join(repoRoot, "apps/v8-agent-os-admin/node_modules/playwright"));
const ffmpeg = process.env.V8_FFMPEG_PATH || "ffmpeg";

function compileFixture(tempRoot) {
    const entryPath = path.join(tempRoot, "entry.tsx");
    const outputPath = path.join(tempRoot, "dist");
    fs.writeFileSync(entryPath, String.raw`
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { LocaleProvider } from "@fixture-locale";
import { CanvasABReview } from "@fixture-review";

const resultNodeId = "media-fixture-result";
const media = (id, name, mediaType, url) => ({ id, origin: "artifact", name, mimeType: mediaType === "video" ? "video/mp4" : "audio/wav", mediaType, url });
const versions = [
  { identity: "video-a", resultNodeId, version: { outputVersionId: "video-a", version: 2, proof: { schema: "v8.creative_canvas_output_proof.v1", status: "succeeded", provider: "fixture", model: "fixture-video", recipeId: "fixture", operationKind: "video.generate" } }, resource: media("video-a", "Video A", "video", "/video-a.mp4") },
  { identity: "video-b", resultNodeId, version: { outputVersionId: "video-b", version: 1, proof: { schema: "v8.creative_canvas_output_proof.v1", status: "succeeded", provider: "fixture", model: "fixture-video", recipeId: "fixture", operationKind: "video.generate" } }, resource: media("video-b", "Video B", "video", "/video-b.mp4") },
];
const audioVersions = [
  { identity: "audio-a", resultNodeId, version: { outputVersionId: "audio-a", version: 2, proof: { schema: "v8.creative_canvas_output_proof.v1", status: "succeeded" } }, resource: media("audio-a", "Audio A", "audio", "/audio-a.wav") },
  { identity: "audio-b", resultNodeId, version: { outputVersionId: "audio-b", version: 1, proof: { schema: "v8.creative_canvas_output_proof.v1", status: "succeeded" } }, resource: media("audio-b", "Audio B", "audio", "/audio-b.wav") },
];
function Fixture() {
  const [mounted, setMounted] = useState(true);
  const audio = new URLSearchParams(location.search).get("kind") === "audio";
  const failedSide = new URLSearchParams(location.search).get("failed") || "";
  const source = audio ? audioVersions : versions;
  const displayed = source.map((item) => failedSide === (item.identity.endsWith("a") ? "a" : "b") ? { ...item, resource: { ...item.resource, url: "/missing-media.bin" } } : item);
  return <LocaleProvider initialLocale="en"><main><button data-unmount type="button" onClick={() => setMounted(false)}>unmount</button>{mounted ? <CanvasABReview resultNodeId={resultNodeId} versions={displayed} /> : null}</main></LocaleProvider>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
`, "utf8");
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
                "@fixture-review": path.join(webRoot, "src/components/workbench/creative-canvas/inspector-review.tsx"),
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
    return new Promise((resolve, reject) => webpack(config, (error, stats) => {
        if (error) return reject(error);
        if (stats?.hasErrors()) return reject(new Error(stats.toString({ colors: false, errors: true, warnings: false })));
        resolve(outputPath);
    }));
}

async function runFfmpeg(args) {
    await execFileAsync(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", ...args], { windowsHide: true });
}

async function createMediaFixtures(root) {
    await runFfmpeg(["-f", "lavfi", "-i", "color=c=0x164e63:s=320x180:r=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path.join(root, "video-a.mp4")]);
    await runFfmpeg(["-f", "lavfi", "-i", "color=c=0x701a75:s=320x180:r=24", "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path.join(root, "video-b.mp4")]);
    await runFfmpeg(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", "-c:a", "pcm_s16le", path.join(root, "audio-a.wav")]);
    await runFfmpeg(["-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000", "-t", "2", "-c:a", "pcm_s16le", path.join(root, "audio-b.wav")]);
}

function serve(root) {
    const requests = [];
    const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mp4": "video/mp4", ".wav": "audio/wav" };
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url || "/", "http://fixture.local").pathname;
        requests.push({ pathname, range: request.headers.range || "" });
        const filename = pathname === "/" ? "index.html" : pathname.slice(1);
        const target = path.resolve(root, filename);
        if (!target.startsWith(`${path.resolve(root)}${path.sep}`) || !fs.existsSync(target)) {
            response.writeHead(404).end();
            return;
        }
        const stat = fs.statSync(target);
        const range = request.headers.range;
        if (range && /bytes=\d*-\d*/.test(range) && !filename.endsWith(".html") && !filename.endsWith(".js")) {
            const [, startText, endText] = range.match(/bytes=(\d*)-(\d*)/);
            const start = startText ? Number(startText) : 0;
            const end = endText ? Math.min(Number(endText), stat.size - 1) : stat.size - 1;
            if (start >= stat.size || start > end) { response.writeHead(416, { "Content-Range": `bytes */${stat.size}` }).end(); return; }
            response.writeHead(206, { "Content-Type": types[path.extname(target)] || "application/octet-stream", "Accept-Ranges": "bytes", "Content-Length": end - start + 1, "Content-Range": `bytes ${start}-${end}/${stat.size}` });
            fs.createReadStream(target, { start, end }).pipe(response);
            return;
        }
        response.writeHead(200, { "Content-Type": types[path.extname(target)] || "application/octet-stream", "Accept-Ranges": "bytes", "Content-Length": stat.size });
        fs.createReadStream(target).pipe(response);
    });
    return { server, requests };
}

async function assertPair(page, kind) {
    await page.locator("[data-canvas-media-side=a][data-canvas-media-state=ready]").waitFor({ state: "attached", timeout: 10000 });
    await page.locator("[data-canvas-media-side=b][data-canvas-media-state=ready]").waitFor({ state: "attached", timeout: 10000 });
    const media = await page.locator(kind === "video" ? "video" : "audio").evaluateAll((items) => items.map((item) => ({ duration: item.duration, muted: item.muted, readyState: item.readyState })));
    if (media.length !== 2 || media.some((item) => !Number.isFinite(item.duration) || item.duration <= 0 || item.readyState < 1)) throw new Error(`media metadata incomplete: ${JSON.stringify(media)}`);
    await page.locator('[data-canvas-audition="b"]').click();
    const switched = await page.locator(kind === "video" ? "video" : "audio").evaluateAll((items) => items.map((item) => item.muted));
    if (switched[0] !== true || switched[1] !== false) throw new Error(`audition did not switch: ${JSON.stringify(switched)}`);
    await page.getByRole("button", { name: /Play both versions/ }).click();
    await page.waitForTimeout(400);
    const afterPlay = await page.locator(kind === "video" ? "video" : "audio").evaluateAll((items) => items.map((item) => item.currentTime));
    if (afterPlay[0] <= 0 || afterPlay[1] <= 0 || Math.abs(afterPlay[0] - afterPlay[1]) > 0.2) throw new Error(`media pair drifted: ${JSON.stringify(afterPlay)}`);
    const slider = page.getByRole("slider", { name: "Synchronized timeline" });
    await slider.evaluate((element) => { element.value = "1"; element.dispatchEvent(new Event("input", { bubbles: true })); element.dispatchEvent(new Event("change", { bubbles: true })); });
    await page.waitForTimeout(100);
    const afterSeek = await page.locator(kind === "video" ? "video" : "audio").evaluateAll((items) => items.map((item) => item.currentTime));
    if (Math.abs(afterSeek[0] - afterSeek[1]) > 0.15) throw new Error(`seek drifted: ${JSON.stringify(afterSeek)}`);
    for (let index = 0; index < 30; index += 1) {
        const selectors = page.locator("[data-canvas-review-selector]");
        await selectors.nth(0).selectOption(String(index % 2));
        await selectors.nth(1).selectOption(String((index + 1) % 2));
        await page.waitForTimeout(20);
    }
    if (await page.locator(kind === "video" ? "video" : "audio").count() !== 2) throw new Error("A/B swaps leaked media elements");
}

async function main() {
    if (path.isAbsolute(ffmpeg) && !fs.existsSync(ffmpeg)) {
        throw new Error(`ffmpeg not found: ${ffmpeg}`);
    }
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-canvas-media-review-"));
    let server;
    let browser;
    try {
        await loadBindings();
        const outputPath = await compileFixture(tempRoot);
        await createMediaFixtures(outputPath);
        fs.writeFileSync(path.join(outputPath, "index.html"), "<!doctype html><html><body><div id=\"root\"></div><script src=\"/fixture.js\"></script></body></html>", "utf8");
        const fixture = serve(outputPath);
        server = fixture.server;
        await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
        const address = server.address();
        const baseUrl = `http://127.0.0.1:${address.port}`;
        browser = await chromium.launch({ executablePath: process.env.V8_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
        const page = await browser.newPage();
        await page.goto(`${baseUrl}/?kind=video`, { waitUntil: "networkidle" });
        await assertPair(page, "video");
        if (!fixture.requests.some((item) => item.range && item.pathname.endsWith(".mp4"))) throw new Error("video did not issue a Range request");
        await page.goto(`${baseUrl}/?kind=audio`, { waitUntil: "networkidle" });
        await assertPair(page, "audio");
        if (!fixture.requests.some((item) => item.range && item.pathname.endsWith(".wav"))) throw new Error("audio did not issue a Range request");
        await page.goto(`${baseUrl}/?kind=video&failed=b`, { waitUntil: "networkidle" });
        await page.locator('[data-canvas-media-side=b][data-canvas-media-state=error]').waitFor({ state: "attached", timeout: 10000 });
        await page.locator('[data-canvas-media-side=a] video').evaluate((media) => media.play());
        await page.waitForTimeout(250);
        const oneSide = await page.locator('[data-canvas-media-side=a] video').evaluate((media) => ({ currentTime: media.currentTime, paused: media.paused }));
        if (oneSide.paused || oneSide.currentTime <= 0) throw new Error(`healthy side stopped after peer failure: ${JSON.stringify(oneSide)}`);
        await page.getByRole("button", { name: "unmount" }).click();
        const cleanup = await page.evaluate(() => ({ mediaCount: document.querySelectorAll("video,audio").length, detachedWithSrc: [...document.querySelectorAll("video,audio")].filter((item) => item.src).length }));
        if (cleanup.mediaCount !== 0 || cleanup.detachedWithSrc !== 0) throw new Error(`media cleanup incomplete: ${JSON.stringify(cleanup)}`);
        console.log(JSON.stringify({ status: "passed", rangeRequests: fixture.requests.filter((item) => item.range).length, cleanup }, null, 2));
    } finally {
        if (browser) await browser.close();
        if (server) await new Promise((resolve) => server.close(resolve));
        const resolved = path.resolve(tempRoot);
        if (resolved.startsWith(`${path.resolve(os.tmpdir())}${path.sep}`)) fs.rmSync(resolved, { recursive: true, force: true });
    }
}

await main();
