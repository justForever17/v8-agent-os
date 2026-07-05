import express from "express";
import crypto from "crypto";
import fs from "fs";
import { createServer } from "http";
import os from "os";
import path from "path";
import { createServer as createViteServer } from "vite";
import { WebSocket, WebSocketServer } from "ws";

const app = express();
const PORT = 3000;

// Increase request size limit to handle webcam frame base64 uploads
app.use(express.json({ limit: "15mb" }));

function normalizeAdminBaseUrl(input: unknown) {
  const raw = String(input || process.env.V8_ADMIN_BASE_URL || "http://127.0.0.1:9528").trim();
  return raw.replace(/\/+$/, "") || "http://127.0.0.1:9528";
}

function stripProxyOnlyQuery(originalUrl: string) {
  const [rawPath, rawQuery = ""] = originalUrl.split("?");
  const targetPath = rawPath.replace(/^\/api\/v8/, "") || "/";
  const params = new URLSearchParams(rawQuery);
  params.delete("adminBaseUrl");
  const suffix = params.toString();
  return suffix ? `${targetPath}?${suffix}` : targetPath;
}

type V8BridgeConfig = {
  engineBaseUrl?: string;
  engineWsBaseUrl?: string;
  internalSecret?: string;
};

function readV8BridgeConfig(): V8BridgeConfig {
  const configPath = path.join(os.homedir(), ".v8-agent-os", "config.json");
  try {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    const bridge = parsed?.systemBase?.bridge;
    return bridge && typeof bridge === "object" ? bridge : {};
  } catch {
    return {};
  }
}

function normalizeWsBaseUrl(value: unknown, fallback = "ws://127.0.0.1:9530/v1") {
  const raw = String(value || "").trim() || fallback;
  const withoutSlash = raw.replace(/\/+$/, "");
  if (withoutSlash.startsWith("https://")) return withoutSlash.replace(/^https:\/\//, "wss://");
  if (withoutSlash.startsWith("http://")) return withoutSlash.replace(/^http:\/\//, "ws://");
  return withoutSlash;
}

function resolveEngineWsUrl() {
  const bridge = readV8BridgeConfig();
  const base = normalizeWsBaseUrl(bridge.engineWsBaseUrl || bridge.engineBaseUrl);
  return `${base.replace(/\/+$/, "")}/chat/ws`;
}

function buildEngineWsTicket(subject = "cybercore-desktop") {
  const secret = String(readV8BridgeConfig().internalSecret || "").trim();
  if (!secret) return "";
  const payload = JSON.stringify({
    sub: subject,
    aud: "chat_ws",
    exp: Math.floor(Date.now() / 1000) + 120,
  });
  const payloadB64 = Buffer.from(payload, "utf-8").toString("base64url");
  const signature = crypto.createHmac("sha256", secret).update(payloadB64).digest("base64url");
  return `${payloadB64}.${signature}`;
}

// V8OS Admin BFF local proxy.
// Renderer code talks to same-origin /api/v8/*; this server forwards to Admin /api/client/*.
app.all("/api/v8/*", async (req, res) => {
  const adminBaseUrl = normalizeAdminBaseUrl(req.header("x-v8-admin-base") || req.query.adminBaseUrl);
  const targetUrl = `${adminBaseUrl}${stripProxyOnlyQuery(req.originalUrl)}`;
  const headers = new Headers();

  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    const lowered = key.toLowerCase();
    if (
      lowered === "host"
      || lowered === "content-length"
      || lowered === "x-v8-admin-base"
    ) {
      continue;
    }
    headers.set(key, Array.isArray(value) ? value.join(",") : String(value));
  }

  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers,
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    const contentType = String(req.headers["content-type"] || "");
    if (contentType.includes("application/json")) {
      init.body = JSON.stringify(req.body || {});
    } else {
      init.body = req as unknown as BodyInit;
      init.duplex = "half";
    }
  }

  try {
    const upstream = await fetch(targetUrl, init);
    res.status(upstream.status);
    upstream.headers.forEach((value, key) => {
      if (["set-cookie", "content-encoding", "content-length"].includes(key.toLowerCase())) {
        return;
      }
      res.setHeader(key, value);
    });
    if (!upstream.body) {
      res.end();
      return;
    }
    const reader = upstream.body.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(Buffer.from(value));
      }
      res.end();
    } finally {
      reader.releaseLock();
    }
  } catch (error: any) {
    console.error("[CyberCore V8 Proxy] request failed:", error);
    res.status(502).json({
      error: "v8_admin_proxy_failed",
      message: error?.message || String(error),
      adminBaseUrl,
    });
  }
});

const httpServer = createServer(app);
const engineRealtimeWss = new WebSocketServer({ noServer: true });

httpServer.on("upgrade", (request, socket, head) => {
  const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
  if (requestUrl.pathname !== "/api/v8/engine-ws") {
    return;
  }

  engineRealtimeWss.handleUpgrade(request, socket, head, (clientSocket) => {
    (request as typeof request & { cybercoreRequestUrl?: URL }).cybercoreRequestUrl = requestUrl;
    engineRealtimeWss.emit("connection", clientSocket, request);
  });
});

engineRealtimeWss.on("connection", (clientSocket, request) => {
  const requestUrl =
    (request as typeof request & { cybercoreRequestUrl?: URL }).cybercoreRequestUrl ||
    new URL(request.url || "/", "http://127.0.0.1");
  const sessionId = requestUrl.searchParams.get("sessionId") || "";
  if (!sessionId) {
    clientSocket.send(JSON.stringify({
      kind: "error",
      topic: "cybercore.realtime.invalid_request",
      payload: { message: "sessionId is required" },
    }));
    clientSocket.close(1008, "sessionId required");
    return;
  }

  const engineUrl = new URL(resolveEngineWsUrl());
  const ticket = buildEngineWsTicket("cybercore-desktop");
  if (ticket) {
    engineUrl.searchParams.set("ticket", ticket);
  }

  let engineSocket: WebSocket | null = null;
  let engineOpen = false;

  const closeBoth = () => {
    try {
      if (engineSocket && engineSocket.readyState === WebSocket.OPEN) {
        engineSocket.close(1000, "client closed");
      }
    } catch {}
    try {
      if (clientSocket.readyState === WebSocket.OPEN) {
        clientSocket.close(1000, "engine closed");
      }
    } catch {}
  };

  try {
    engineSocket = new WebSocket(engineUrl);

    engineSocket.on("open", () => {
      engineOpen = true;
      engineSocket?.send(JSON.stringify({
        kind: "command",
        topic: "session.subscribe",
        session_id: sessionId,
        payload: { include_snapshot: false },
      }));
    });

    engineSocket.on("message", (data) => {
      if (clientSocket.readyState === WebSocket.OPEN) {
        clientSocket.send(data.toString());
      }
    });

    engineSocket.on("error", (error) => {
      if (clientSocket.readyState === WebSocket.OPEN) {
        clientSocket.send(JSON.stringify({
          kind: "error",
          topic: "cybercore.realtime.engine_ws_error",
          payload: { message: error instanceof Error ? error.message : String(error) },
        }));
      }
      closeBoth();
    });

    engineSocket.on("close", closeBoth);
  } catch (error) {
    clientSocket.send(JSON.stringify({
      kind: "error",
      topic: "cybercore.realtime.engine_ws_failed",
      payload: { message: error instanceof Error ? error.message : String(error) },
    }));
    closeBoth();
    return;
  }

  clientSocket.on("message", (data) => {
    if (engineOpen && engineSocket?.readyState === WebSocket.OPEN) {
      engineSocket.send(data.toString());
    }
  });
  clientSocket.on("close", closeBoth);
  clientSocket.on("error", closeBoth);
});

// Local app metrics for the desktop pet visual status panel.
app.get("/api/pet/metrics", (req, res) => {
  res.json([
    { label: "Core Frame Rate", value: "60 FPS", level: 95 },
    { label: "Neural Flow Speed", value: "3.2 TFLOPs", level: 82 },
    { label: "Waveform Sync State", value: "100% Locked", level: 100 },
    { label: "Energy Capacitance", value: `${Math.floor(60 + Math.random() * 40)}%`, level: 75 },
  ]);
});


// Setup Vite & static assets
async function startServer() {
  if (process.env.NODE_ENV !== "production") {
    console.log("Setting up Vite middleware for full-stack integration...");
    const vite = await createViteServer({
      server: { middlewareMode: true, hmr: { server: httpServer } },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  httpServer.listen(PORT, "0.0.0.0", () => {
    console.log(`[CyberCore Server] Connected securely and active on http://0.0.0.0:${PORT}`);
  });
}

startServer();
