export type V8AuthSession = {
  adminBaseUrl: string;
  accessToken: string;
  refreshToken: string;
  user?: { email?: string; name?: string; login?: string } | null;
};

export type V8Conversation = {
  id: string;
  title?: string;
  workspacePath?: string | null;
  workspaceDisplayName?: string | null;
  workspacePinned?: boolean;
  workspacePinnedAt?: string | null;
  pinned?: boolean;
  pinnedAt?: string | null;
  [key: string]: unknown;
};

export type V8Project = {
  id?: string;
  name?: string;
  workspacePath?: string;
  [key: string]: unknown;
};

export type V8UploadResult = {
  url?: string;
  path?: string;
  name?: string;
  mimeType?: string;
  resourceRef?: unknown;
  [key: string]: unknown;
};

export type V8AudioInputStatus = {
  route?: "stt" | "vision_audio" | "unavailable" | string;
  stt?: { usable?: boolean; provider?: string; reason?: string; [key: string]: unknown };
  visionAudio?: {
    usable?: boolean;
    modelId?: string;
    modelRef?: string;
    providerId?: string;
    reason?: string;
    [key: string]: unknown;
  };
  error?: string;
  [key: string]: unknown;
};

export type V8DesktopPetConfig = {
  appearance?: {
    petScale?: number;
    floatAmplitude?: number;
    floatSpeed?: number;
    [key: string]: unknown;
  };
  eventVoice?: {
    enabled?: boolean;
    mode?: string;
    voiceRef?: string;
    speakVoiceTags?: boolean;
    speakSupervisorReplies?: boolean;
    customRules?: unknown[];
    [key: string]: unknown;
  };
  actionTable?: Array<{
    id?: string;
    event?: string;
    /** @deprecated Read-only compatibility for configurations created before structured events. */
    match?: string;
    emotion?: string;
    spectrum?: string;
    [key: string]: unknown;
  }>;
  effectSpectrum?: {
    preset?: string;
    intensity?: number;
    customGlowColor?: string;
    [key: string]: unknown;
  };
  attachmentCapture?: {
    cameraEnabled?: boolean;
    includeDesktopScreenshot?: boolean;
    layout?: "desktop_pip_camera" | string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

const SESSION_STORAGE_KEY = "v8.desktopPet.auth";
const ACTIVE_CONVERSATION_KEY = "v8.desktopPet.activeConversationId";
const ADMIN_BASE_KEY = "v8.desktopPet.adminBaseUrl";

export function normalizeAdminBaseUrl(input: string) {
  const trimmed = String(input || "").trim();
  return (trimmed || "http://127.0.0.1:9528").replace(/\/+$/, "");
}

function readJson<T>(value: string | null): T | null {
  if (!value) return null;
  try {
    return JSON.parse(value) as T;
  } catch {
    return null;
  }
}

function localProxyPath(path: string) {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `/api/v8${normalized}`;
}

export class V8DesktopClientAdapter {
  private session: V8AuthSession | null;

  constructor(session?: V8AuthSession | null) {
    this.session = session || this.loadSession();
  }

  loadSession() {
    const stored = readJson<V8AuthSession>(localStorage.getItem(SESSION_STORAGE_KEY));
    if (!stored?.accessToken) return null;
    return {
      ...stored,
      adminBaseUrl: normalizeAdminBaseUrl(stored.adminBaseUrl),
    };
  }

  getSession() {
    return this.session;
  }

  getStoredAdminBaseUrl() {
    return normalizeAdminBaseUrl(
      this.session?.adminBaseUrl
      || localStorage.getItem(ADMIN_BASE_KEY)
      || "http://127.0.0.1:9528",
    );
  }

  getActiveConversationId() {
    return localStorage.getItem(ACTIVE_CONVERSATION_KEY) || "";
  }

  setActiveConversationId(id: string) {
    if (id) {
      localStorage.setItem(ACTIVE_CONVERSATION_KEY, id);
    } else {
      localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
    }
  }

  private clearAuthSession() {
    this.session = null;
    localStorage.removeItem(SESSION_STORAGE_KEY);
  }

  clearSession() {
    this.clearAuthSession();
    localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
  }

  private persistSession(session: V8AuthSession) {
    this.session = session;
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    localStorage.setItem(ADMIN_BASE_KEY, session.adminBaseUrl);
  }

  async signInLocal(input?: { adminBaseUrl?: string; deviceName?: string }) {
    const adminBaseUrl = normalizeAdminBaseUrl(input?.adminBaseUrl || "");
    const response = await fetch(localProxyPath("/api/client/auth/local-session"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-v8-admin-base": adminBaseUrl,
      },
      body: JSON.stringify({
        surface: "desktop_pet",
        deviceName: input?.deviceName || "v8-desktop-pet",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload?.accessToken) {
      throw new Error(payload?.error || "V8OS 本机连接失败");
    }
    const session = {
      adminBaseUrl,
      accessToken: String(payload.accessToken),
      refreshToken: String(payload.refreshToken || ""),
      user: payload.user || null,
    };
    this.persistSession(session);
    return session;
  }

  private async validateSession(session: V8AuthSession) {
    const response = await fetch(localProxyPath("/api/client/auth/me"), {
      method: "GET",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "x-v8-admin-base": session.adminBaseUrl,
      },
    });
    if (response.status === 401 || response.status === 403) return false;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error || `V8OS 会话校验失败：${response.status}`);
    }
    return true;
  }

  async ensureLocalSession(input?: { adminBaseUrl?: string; deviceName?: string }) {
    const requestedAdminBaseUrl = normalizeAdminBaseUrl(input?.adminBaseUrl || "");
    if (this.session && this.session.adminBaseUrl !== requestedAdminBaseUrl) {
      this.clearSession();
    }
    if (this.session) {
      if (await this.validateSession(this.session)) return this.session;
      try {
        const refreshed = await this.refreshSession();
        if (refreshed && await this.validateSession(refreshed)) return refreshed;
      } catch {
        this.clearAuthSession();
      }
    }
    return this.signInLocal({
      adminBaseUrl: requestedAdminBaseUrl,
      deviceName: input?.deviceName || "v8-desktop-pet",
    });
  }

  async refreshSession() {
    const current = this.session;
    if (!current?.refreshToken) return null;
    const response = await fetch(localProxyPath("/api/client/auth/refresh"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-v8-admin-base": current.adminBaseUrl,
      },
      body: JSON.stringify({
        refreshToken: current.refreshToken,
        deviceName: "v8-desktop-pet",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 || response.status === 403) {
      this.clearAuthSession();
      return null;
    }
    if (!response.ok || !payload?.accessToken) {
      throw new Error(payload?.error || `V8OS 会话刷新失败：${response.status}`);
    }
    const session = {
      adminBaseUrl: current.adminBaseUrl,
      accessToken: String(payload.accessToken),
      refreshToken: String(payload.refreshToken || current.refreshToken),
      user: payload.user || current.user || null,
    };
    this.persistSession(session);
    return session;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }
    const headers = new Headers(init.headers || {});
    headers.set("Authorization", `Bearer ${session.accessToken}`);
    headers.set("x-v8-admin-base", session.adminBaseUrl);
    const response = await fetch(localProxyPath(path), {
      ...init,
      headers,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error || payload?.message || `V8OS 请求失败：${response.status}`);
    }
    return payload as T;
  }

  async listProjects() {
    const payload = await this.request<{ projects?: V8Project[]; mainWorkspacePath?: string; defaultProjectId?: string }>("/api/client/projects", {
      cache: "no-store",
    });
    return payload;
  }

  async listConversations() {
    return this.request<V8Conversation[]>("/api/client/conversations", { cache: "no-store" });
  }

  async createConversation(input: { title?: string; workspacePath?: string; projectId?: string }) {
    const payload = await this.request<V8Conversation>("/api/client/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    const id = String(payload.id || "");
    if (id) this.setActiveConversationId(id);
    return payload;
  }

  async updateSessionScope(sessionId: string, input: { workspacePath?: string; projectId?: string; scopeSource?: string }) {
    return this.request<{ binding?: unknown }>(`/api/client/sessions/${encodeURIComponent(sessionId)}/scope`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, scopeSource: input.scopeSource || "desktop_pet" }),
    });
  }

  async submitMessage(input: {
    conversationId: string;
    content: string;
    clientMessageId: string;
    attachments?: unknown[];
    fileUrls?: string[];
  }) {
    return this.request<Record<string, unknown>>("/api/client/chat-submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientMessageId: input.clientMessageId,
        messages: [{ role: "user", content: input.content }],
        data: {
          conversationId: input.conversationId,
          clientMessageId: input.clientMessageId,
          attachments: input.attachments?.length ? input.attachments : undefined,
          fileUrls: input.fileUrls?.length ? input.fileUrls : undefined,
        },
      }),
    });
  }

  async synthesizeSpeech(text: string, input?: { voiceRef?: string }) {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }
    const response = await fetch(localProxyPath("/api/client/audio/tts"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.accessToken}`,
        "x-v8-admin-base": session.adminBaseUrl,
      },
      body: JSON.stringify({ text, voiceRef: input?.voiceRef || undefined }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(async () => ({ error: await response.text().catch(() => "") }));
      throw new Error(payload?.error || payload?.detail || `V8OS TTS 失败：${response.status}`);
    }
    return response.blob();
  }

  async transcribeSpeech(audio: Blob, input?: { language?: string; filename?: string }) {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }
    const form = new FormData();
    const filename = input?.filename || "cybercore-voice.webm";
    form.append("file", audio, filename);
    if (input?.language) {
      form.append("language", input.language);
    }
    const response = await fetch(localProxyPath("/api/client/audio/stt"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "x-v8-admin-base": session.adminBaseUrl,
      },
      body: form,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error || payload?.detail || `V8OS STT 失败：${response.status}`);
    }
    return payload as { text?: string; transcript?: string; confidence?: number; [key: string]: unknown };
  }

  async getAudioInputStatus() {
    return this.request<V8AudioInputStatus>("/api/client/audio/input-status", {
      method: "GET",
      cache: "no-store",
    });
  }

  async getDesktopPetConfig() {
    return this.request<{ data?: V8DesktopPetConfig } | V8DesktopPetConfig>("/api/client/desktop-pet/config", {
      method: "GET",
      cache: "no-store",
    });
  }

  async uploadFile(file: File, scope: { conversationId?: string; workspacePath?: string; sourceKind?: "desktop_pet_upload" | "desktop_pet_voice" }) {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }
    const form = new FormData();
    form.append("file", file);
    if (scope.conversationId) {
      form.append("sessionId", scope.conversationId);
      form.append("conversationId", scope.conversationId);
    }
    if (scope.workspacePath) {
      form.append("workspacePath", scope.workspacePath);
    }
    form.append("sourceKind", scope.sourceKind || "desktop_pet_upload");
    const response = await fetch(localProxyPath("/api/client/upload"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "x-v8-admin-base": session.adminBaseUrl,
      },
      body: form,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error || "文件上传失败");
    }
    return payload as V8UploadResult;
  }

  async getRealtimeSnapshot(conversationId: string) {
    return this.request<Record<string, unknown>>(
      `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/snapshot?surface=desktop&compact=1`,
      { cache: "no-store" },
    );
  }

  async getConversationDetail(conversationId: string) {
    return this.request<Record<string, unknown>>(
      `/api/client/conversations/${encodeURIComponent(conversationId)}`,
      { cache: "no-store" },
    );
  }

  async streamRealtimeSession(
    conversationId: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal: AbortSignal,
  ) {
    try {
      await this.streamRealtimeSessionViaEngineWs(conversationId, onEvent, signal);
      return;
    } catch (error) {
      if (signal.aborted) return;
      console.warn("V8 Engine WS unavailable, falling back to Admin SSE stream:", error);
      await this.streamRealtimeSessionViaAdminSse(conversationId, onEvent, signal);
    }
  }

  async streamSessionActivity(
    onEvent: (eventName: string, payload: unknown) => void,
    signal: AbortSignal,
  ) {
    await this.streamAdminSse(
      "/api/client/realtime/session-activity/stream",
      onEvent,
      signal,
      "会话活动流",
    );
  }

  private async streamRealtimeSessionViaEngineWs(
    conversationId: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal: AbortSignal,
  ) {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }

    const wsUrl = new URL("/api/v8/engine-ws", window.location.origin);
    wsUrl.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    wsUrl.searchParams.set("sessionId", conversationId);

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let aborted = false;
      const socket = new WebSocket(wsUrl);

      const cleanup = () => {
        signal.removeEventListener("abort", abort);
      };
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback();
      };
      const abort = () => {
        aborted = true;
        try {
          socket.close(1000, "aborted");
        } catch {}
        finish(resolve);
      };

      signal.addEventListener("abort", abort, { once: true });

      socket.onopen = () => {
        // The local CyberCore server subscribes to the session immediately.
      };
      socket.onmessage = (messageEvent) => {
        const rawText = typeof messageEvent.data === "string" ? messageEvent.data : "";
        if (!rawText) return;
        try {
          const payload = JSON.parse(rawText);
          const eventName = String(payload?.topic || payload?.kind || payload?.type || "message");
          onEvent(eventName, payload);
        } catch {
          onEvent("message", rawText);
        }
      };
      socket.onerror = () => {
        try {
          socket.close();
        } catch {}
        finish(() => reject(new Error("Engine WebSocket connection failed")));
      };
      socket.onclose = () => {
        finish(() => {
          if (aborted || signal.aborted) {
            resolve();
          } else {
            reject(new Error("Engine WebSocket closed"));
          }
        });
      };
    });
  }

  private async streamRealtimeSessionViaAdminSse(
    conversationId: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal: AbortSignal,
  ) {
    await this.streamAdminSse(
      `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/stream?surface=desktop&compact=1`,
      onEvent,
      signal,
      "实时流",
    );
  }

  private async streamAdminSse(
    path: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal: AbortSignal,
    label: string,
  ) {
    const session = this.session;
    if (!session?.accessToken) {
      throw new Error("尚未连接 V8OS Admin");
    }
    const response = await fetch(localProxyPath(path), {
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "x-v8-admin-base": session.adminBaseUrl,
      },
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`${label}连接失败：${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split(/\n\n+/);
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const eventLine = chunk.split("\n").find((line) => line.startsWith("event:"));
        const dataLines = chunk.split("\n").filter((line) => line.startsWith("data:"));
        if (!dataLines.length) continue;
        const eventName = eventLine ? eventLine.replace(/^event:\s*/, "").trim() : "message";
        const dataText = dataLines.map((line) => line.replace(/^data:\s?/, "")).join("\n");
        try {
          onEvent(eventName, JSON.parse(dataText));
        } catch {
          onEvent(eventName, dataText);
        }
      }
    }
  }
}
