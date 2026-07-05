import fs from "node:fs/promises";
import path from "node:path";

type AuthPayload = {
  accessToken?: string;
  refreshToken?: string;
  user?: unknown;
};

type ConversationPayload = {
  id?: string;
};

type AudioInputStatus = {
  route?: string;
  stt?: { reason?: string };
  visionAudio?: { reason?: string; modelId?: string; modelRef?: string };
  error?: string;
};

const adminBaseUrl = (process.env.V8_ADMIN_BASE_URL || "http://127.0.0.1:9528").replace(/\/+$/, "");
const proxyBaseUrl = (process.env.V8_CYBERCORE_PROXY_BASE || "http://127.0.0.1:3000/api/v8").replace(/\/+$/, "");
const useCyberCoreProxy = process.env.V8_CYBERCORE_USE_PROXY === "1";
const workspacePath = process.env.V8_CYBERCORE_WORKSPACE || "";
const voiceText = process.env.V8_CYBERCORE_VOICE_TEXT || "你好，请用一句话回复：CyberCore 语音链路已收到。";
const live = process.argv.includes("--live");

function fail(message: string): never {
  throw new Error(message);
}

async function readPayload(response: Response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => ({}));
  }
  return response.text().catch(() => "");
}

async function adminFetch(pathname: string, init: RequestInit = {}, token?: string) {
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (useCyberCoreProxy) {
    headers.set("x-v8-admin-base", adminBaseUrl);
  }
  const baseUrl = useCyberCoreProxy ? proxyBaseUrl : adminBaseUrl;
  return fetch(`${baseUrl}${pathname}`, {
    ...init,
    headers,
  });
}

async function jsonRequest<T>(pathname: string, init: RequestInit = {}, token?: string): Promise<T> {
  const response = await adminFetch(pathname, init, token);
  const payload = await readPayload(response);
  if (!response.ok) {
    fail(`${pathname} failed: ${response.status} ${typeof payload === "string" ? payload : JSON.stringify(payload)}`);
  }
  return payload as T;
}

function collectAssistantTexts(value: unknown): string[] {
  const result: string[] = [];
  const seen = new Set<unknown>();
  const visit = (node: unknown) => {
    if (!node || typeof node !== "object" || seen.has(node)) return;
    seen.add(node);
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    const record = node as Record<string, unknown>;
    const roleText = String(record.role || record.sender || record.authorRole || record.actor || record.type || "").toLowerCase();
    const candidate = [record.content, record.text, record.message, record.summary]
      .find((item) => typeof item === "string" && item.trim());
    if (candidate && /(assistant|supervisor|agent|pet|ai|智能主管)/i.test(roleText)) {
      result.push(String(candidate).trim());
    }
    Object.values(record).forEach(visit);
  };
  visit(value);
  return [...new Set(result)];
}

async function synthesizeVoice(token: string) {
  const response = await adminFetch("/api/client/audio/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: voiceText }),
  }, token);
  if (!response.ok) {
    const payload = await readPayload(response);
    fail(`TTS failed: ${response.status} ${typeof payload === "string" ? payload : JSON.stringify(payload)}`);
  }
  const arrayBuffer = await response.arrayBuffer();
  if (arrayBuffer.byteLength < 128) {
    fail(`TTS returned too little audio: ${arrayBuffer.byteLength} bytes`);
  }
  const contentType = response.headers.get("content-type") || "audio/mpeg";
  const extension = contentType.includes("wav") ? "wav" : contentType.includes("webm") ? "webm" : "mp3";
  return {
    blob: new Blob([arrayBuffer], { type: contentType }),
    fileName: `cybercore-live-voice.${extension}`,
    contentType,
    bytes: arrayBuffer.byteLength,
  };
}

async function uploadVoice(token: string, conversationId: string, audio: Awaited<ReturnType<typeof synthesizeVoice>>) {
  const form = new FormData();
  form.append("file", new File([audio.blob], audio.fileName, { type: audio.contentType }));
  form.append("sessionId", conversationId);
  form.append("conversationId", conversationId);
  if (workspacePath) form.append("workspacePath", workspacePath);
  const response = await adminFetch("/api/client/upload", {
    method: "POST",
    body: form,
  }, token);
  const payload = await readPayload(response) as Record<string, unknown>;
  if (!response.ok) {
    fail(`upload failed: ${response.status} ${JSON.stringify(payload)}`);
  }
  const url = String(payload.url || payload.publicUrl || payload.path || "").trim();
  if (!url) {
    fail(`upload did not return a usable url: ${JSON.stringify(payload)}`);
  }
  return { payload, url };
}

async function submitMessage(token: string, conversationId: string, content: string, fileUrls?: string[], attachments?: Record<string, unknown>[]) {
  const clientMessageId = `cybercore-live-${Date.now()}`;
  await jsonRequest<Record<string, unknown>>("/api/client/chat-submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      clientMessageId,
      messages: [{ role: "user", content }],
      data: {
        conversationId,
        clientMessageId,
        fileUrls: fileUrls?.length ? fileUrls : undefined,
        attachments: attachments?.length ? attachments : undefined,
      },
    }),
  }, token);
}

async function waitForSupervisorReply(token: string, conversationId: string) {
  for (let attempt = 0; attempt < 18; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 1200 : 3000));
    const snapshot = await jsonRequest<Record<string, unknown>>(
      `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/snapshot?surface=desktop&compact=1`,
      { cache: "no-store" },
      token,
    );
    const texts = collectAssistantTexts(snapshot)
      .filter((text) => !text.includes("CyberCore 语音链路已收到"));
    if (texts.length) {
      return texts[texts.length - 1];
    }
  }
  return "";
}

async function main() {
  if (!live) {
    console.log("DRY RUN: add --live to call Admin, TTS, STT/upload, and Supervisor.");
    console.log(JSON.stringify({
      adminBaseUrl,
      proxyBaseUrl,
      useCyberCoreProxy,
      workspacePath: workspacePath || null,
      voiceText,
    }, null, 2));
    return;
  }

  console.log(`[CyberCore V8OS Voice Smoke] Admin: ${adminBaseUrl}`);
  console.log(`[CyberCore V8OS Voice Smoke] Transport: ${useCyberCoreProxy ? `CyberCore proxy ${proxyBaseUrl}` : "direct Admin BFF"}`);
  const auth = await jsonRequest<AuthPayload>("/api/client/auth/local-session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ surface: "desktop_pet", deviceName: "v8-desktop-pet-live-smoke" }),
  });
  const token = auth.accessToken || fail("login did not return accessToken");

  const conversation = await jsonRequest<ConversationPayload>("/api/client/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: `CyberCore voice smoke ${new Date().toISOString()}`,
      workspacePath: workspacePath || undefined,
    }),
  }, token);
  const conversationId = conversation.id || fail("conversation did not return id");

  const audio = await synthesizeVoice(token);
  const reportDir = path.join(process.env.USERPROFILE || process.cwd(), ".v8-agent-os", "reports", "cybercore_voice_live");
  await fs.mkdir(reportDir, { recursive: true });
  await fs.writeFile(path.join(reportDir, audio.fileName), Buffer.from(await audio.blob.arrayBuffer()));

  const status = await jsonRequest<AudioInputStatus>("/api/client/audio/input-status", { cache: "no-store" }, token);
  console.log(`[CyberCore V8OS Voice Smoke] audio route=${status.route || "unknown"}`);

  if (status.route === "stt") {
    const form = new FormData();
    form.append("file", new File([audio.blob], audio.fileName, { type: audio.contentType }));
    const sttResponse = await adminFetch("/api/client/audio/stt", { method: "POST", body: form }, token);
    const sttPayload = await readPayload(sttResponse) as Record<string, unknown>;
    if (!sttResponse.ok) {
      fail(`STT failed: ${sttResponse.status} ${JSON.stringify(sttPayload)}`);
    }
    const transcript = String(sttPayload.text || sttPayload.transcript || "").trim();
    if (!transcript) fail(`STT returned empty transcript: ${JSON.stringify(sttPayload)}`);
    console.log(`[CyberCore V8OS Voice Smoke] transcript=${transcript}`);
    await submitMessage(token, conversationId, transcript);
  } else if (status.route === "vision_audio") {
    const upload = await uploadVoice(token, conversationId, audio);
    await submitMessage(token, conversationId, "", [upload.url], [{
      url: upload.url,
      publicUrl: upload.url,
      name: audio.fileName,
      mimeType: audio.contentType,
      size: audio.bytes,
      mediaKind: "audio",
      source: "cybercore_voice_live_smoke",
    }]);
  } else {
    fail(`No usable audio input route: ${JSON.stringify(status)}`);
  }

  const reply = await waitForSupervisorReply(token, conversationId);
  if (!reply) {
    fail(`Supervisor reply was not observed in desktop realtime snapshot for ${conversationId}`);
  }
  console.log("[CyberCore V8OS Voice Smoke] SUCCESS");
  console.log(JSON.stringify({ conversationId, route: status.route, audioBytes: audio.bytes, reply }, null, 2));
}

main().catch((error) => {
  console.error("[CyberCore V8OS Voice Smoke] FAILED", error);
  process.exitCode = 1;
});
