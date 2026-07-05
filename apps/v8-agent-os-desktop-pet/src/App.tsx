import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CyberPet from "./components/CyberPet";
import type { ChatMessage, DesktopConversationSummary, PetEmotion, PetSettings, TrayContextPayload } from "./types";
import { V8DesktopClientAdapter, type V8Conversation, type V8Project } from "./lib/v8DesktopClient";

const DEFAULT_RULES = [
  { id: "thinking", match: "reasoning|thinking|思考", emotion: "thinking", spectrum: "violet" },
  { id: "tool", match: "tool|工具|command|terminal", emotion: "tool_calling", spectrum: "cyan" },
  { id: "media", match: "creative|media|artifact|audio|video|image|多媒体|产物", emotion: "scanning", spectrum: "emerald" },
  { id: "done", match: "completed|finished|done|完成|成功", emotion: "happy", spectrum: "amber" },
  { id: "error", match: "error|failed|失败|错误|异常", emotion: "worried", spectrum: "rose" },
] as const;

const DEFAULT_SETTINGS: PetSettings = {
  lang: "zh",
  petScale: 0.7,
  floatAmplitude: 8,
  floatSpeed: 1,
  customGlowColor: "default",
  ttsEnabled: true,
  muted: false,
  isWakewordActive: false,
  wakeword: "V8",
  wakeWindowMs: 6500,
  sttLanguage: "zh-CN",
  v8EventRulesJson: JSON.stringify(DEFAULT_RULES, null, 2),
};

const SETTINGS_KEY = "v8.desktopPet.settings";
const VOICE_PROMPT = "请原样提取音频中的语言并转换成文本；不要补写、不要总结、不要猜测缺失内容。";

function readSettings(): PetSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function shortId() {
  return Math.random().toString(36).slice(2, 9);
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  const direct = record.text || record.content || record.message || record.delta || record.summary;
  if (typeof direct === "string") return direct;
  const payload = record.payload;
  if (payload && typeof payload === "object") return asText(payload);
  return "";
}

function stripVoiceTags(text: string) {
  return text.replace(/<voice>([\s\S]*?)<\/voice>/gi, "$1").trim();
}

function extractVoiceText(text: string) {
  const matches = Array.from(text.matchAll(/<voice>([\s\S]*?)<\/voice>/gi));
  if (!matches.length) return "";
  return matches.map((match) => match[1]?.trim()).filter(Boolean).join("\n");
}

function guessRunning(conversation: V8Conversation) {
  const status = String(conversation.status || conversation.runStatus || conversation.state || "").toLowerCase();
  return status.includes("running") || status.includes("active") || status.includes("进行");
}

function summarizeConversation(conversation: V8Conversation, projects: V8Project[]): DesktopConversationSummary {
  const workspacePath = typeof conversation.workspacePath === "string" ? conversation.workspacePath : null;
  const project = projects.find((item) => item.workspacePath && item.workspacePath === workspacePath);
  return {
    id: String(conversation.id || ""),
    title: String(conversation.title || conversation.id || "未命名会话"),
    projectName: String(project?.name || project?.id || conversation.projectId || "V8OS"),
    workspacePath,
    running: guessRunning(conversation),
  };
}

function eventEmotion(topic: string, payload: unknown, settings: PetSettings): PetEmotion {
  const haystack = `${topic}\n${asText(payload)}`;
  try {
    const rules = JSON.parse(settings.v8EventRulesJson || "[]");
    if (Array.isArray(rules)) {
      for (const rule of rules) {
        const match = typeof rule?.match === "string" ? rule.match : "";
        const emotion = rule?.emotion as PetEmotion | undefined;
        if (!match || !emotion) continue;
        const re = new RegExp(match, "i");
        if (re.test(haystack)) return emotion;
      }
    }
  } catch {
    // Bad custom rules should not break the companion.
  }
  if (/error|failed|失败|错误/i.test(haystack)) return "worried";
  if (/tool|command|工具/i.test(haystack)) return "tool_calling";
  if (/done|completed|完成|成功/i.test(haystack)) return "happy";
  return "thinking";
}

export default function App() {
  const [settings, setSettings] = useState<PetSettings>(() => readSettings());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [emotion, setEmotion] = useState<PetEmotion>("idle");
  const [status, setStatus] = useState("启动中");
  const [connected, setConnected] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [conversations, setConversations] = useState<V8Conversation[]>([]);
  const [projects, setProjects] = useState<V8Project[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [clickThrough, setClickThrough] = useState(true);

  const clientRef = useRef(new V8DesktopClientAdapter());
  const settingsRef = useRef(settings);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const abortRealtimeRef = useRef<AbortController | null>(null);
  const lastSpokenRef = useRef("");

  useEffect(() => {
    settingsRef.current = settings;
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    void window.v8CyberCore?.setCompanionScale?.(settings.petScale);
  }, [settings]);

  const addMessage = useCallback((message: Omit<ChatMessage, "id" | "timestamp">) => {
    setMessages((current) => [
      ...current.slice(-10),
      { id: shortId(), timestamp: Date.now(), ...message },
    ]);
  }, []);

  const speak = useCallback(async (text: string) => {
    const clean = stripVoiceTags(text);
    if (!clean || settingsRef.current.muted || !settingsRef.current.ttsEnabled) return;
    if (clean === lastSpokenRef.current) return;
    lastSpokenRef.current = clean;
    setIsSpeaking(true);
    setEmotion("talking");
    try {
      const blob = await clientRef.current.synthesizeSpeech(clean);
      const url = URL.createObjectURL(blob);
      await new Promise<void>((resolve) => {
        const audio = new Audio(url);
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
        void audio.play().catch(() => resolve());
      });
      URL.revokeObjectURL(url);
    } catch {
      if ("speechSynthesis" in window) {
        await new Promise<void>((resolve) => {
          const utterance = new SpeechSynthesisUtterance(clean);
          utterance.lang = settingsRef.current.lang === "zh" ? "zh-CN" : "en-US";
          utterance.onend = () => resolve();
          utterance.onerror = () => resolve();
          window.speechSynthesis.speak(utterance);
        });
      }
    } finally {
      setIsSpeaking(false);
      setEmotion("idle");
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    const client = clientRef.current;
    const projectPayload = await client.listProjects().catch(() => null);
    const nextProjects = Array.isArray(projectPayload?.projects) ? projectPayload.projects : [];
    const nextConversations = await client.listConversations().catch(() => []);
    setProjects(nextProjects);
    setConversations(Array.isArray(nextConversations) ? nextConversations : []);

    let nextActive = client.getActiveConversationId();
    if (!nextActive && nextConversations.length) {
      nextActive = String(nextConversations[0]?.id || "");
    }
    if (nextActive) {
      setActiveConversationId(nextActive);
      client.setActiveConversationId(nextActive);
    }
    return { projects: nextProjects, conversations: nextConversations, activeConversationId: nextActive };
  }, []);

  const ensureConversation = useCallback(async () => {
    if (activeConversationId) return activeConversationId;
    const client = clientRef.current;
    const payload = await client.listProjects().catch(() => null);
    const mainWorkspace = payload?.mainWorkspacePath || payload?.projects?.[0]?.workspacePath || undefined;
    const created = await client.createConversation({
      title: "桌宠语音",
      workspacePath: mainWorkspace,
      projectId: payload?.defaultProjectId,
    });
    const id = String(created.id || "");
    if (!id) throw new Error("无法创建 V8OS 会话");
    setActiveConversationId(id);
    await refreshConversations();
    return id;
  }, [activeConversationId, refreshConversations]);

  useEffect(() => {
    let disposed = false;
    async function connect() {
      setStatus("连接 V8OS");
      try {
        await clientRef.current.signInLocal({ deviceName: "v8-desktop-pet" });
        if (disposed) return;
        setConnected(true);
        setStatus("已连接");
        setEmotion("happy");
        await refreshConversations();
      } catch (error) {
        if (disposed) return;
        setConnected(false);
        setStatus("等待 V8OS");
        setEmotion("worried");
        addMessage({
          sender: "system",
          text: error instanceof Error ? error.message : "V8OS 本机连接失败",
          emotion: "worried",
        });
      }
    }
    void connect();
    return () => {
      disposed = true;
    };
  }, [addMessage, refreshConversations]);

  useEffect(() => {
    const payload: TrayContextPayload = {
      activeConversationId,
      conversations: conversations.slice(0, 18).map((item) => summarizeConversation(item, projects)),
    };
    void window.v8CyberCore?.updateTrayContext?.(payload);
  }, [activeConversationId, conversations, projects]);

  useEffect(() => {
    const offSelect = window.v8CyberCore?.onTraySelectConversation?.((id) => {
      if (!id) return;
      setActiveConversationId(id);
      clientRef.current.setActiveConversationId(id);
    });
    const offListen = window.v8CyberCore?.onTrayStartListening?.(() => {
      void toggleRecording();
    });
    return () => {
      offSelect?.();
      offListen?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    abortRealtimeRef.current?.abort();
    if (!activeConversationId || !connected) return;
    const controller = new AbortController();
    abortRealtimeRef.current = controller;
    setStatus("监听中");
    void clientRef.current.streamRealtimeSession(activeConversationId, (topic, payload) => {
      const nextEmotion = eventEmotion(topic, payload, settingsRef.current);
      setEmotion(nextEmotion);
      const text = asText(payload);
      const voiceText = extractVoiceText(text);
      if (voiceText) {
        void speak(voiceText);
      }
      if (text && /assistant|message|final|回复|voice/i.test(topic)) {
        addMessage({ sender: "pet", text: stripVoiceTags(text), emotion: nextEmotion });
      }
    }, controller.signal).catch(() => {
      if (!controller.signal.aborted) setStatus("监听断开");
    });
    return () => controller.abort();
  }, [activeConversationId, addMessage, connected, speak]);

  async function submitVoice(blob: Blob) {
    const conversationId = await ensureConversation();
    const client = clientRef.current;
    setStatus("发送语音");
    setEmotion("listening");

    let transcript = "";
    try {
      const audioStatus = await client.getAudioInputStatus().catch(() => null);
      if (audioStatus?.route === "stt" || audioStatus?.stt?.usable) {
        const payload = await client.transcribeSpeech(blob, { language: settingsRef.current.sttLanguage, filename: "desktop-pet-voice.webm" });
        transcript = String(payload.text || payload.transcript || "").trim();
      }
    } catch {
      transcript = "";
    }

    const file = new File([blob], `desktop-pet-voice-${Date.now()}.webm`, { type: blob.type || "audio/webm" });
    const upload = await client.uploadFile(file, { conversationId }).catch(() => null);
    addMessage({
      sender: "user",
      text: transcript || "语音消息",
      audioUrl: upload?.url || upload?.path,
      emotion: "listening",
    });

    await client.submitMessage({
      conversationId,
      clientMessageId: `desktop-pet-${Date.now()}`,
      content: transcript,
      attachments: upload ? [{
        kind: "audio",
        source: "desktop_pet",
        name: upload.name || file.name,
        url: upload.url,
        path: upload.path,
        mimeType: upload.mimeType || file.type,
        prompt: transcript ? undefined : VOICE_PROMPT,
      }] : undefined,
      fileUrls: upload?.url || upload?.path ? [String(upload.url || upload.path)] : undefined,
    });
    setStatus("已发送");
    setEmotion("thinking");
    await refreshConversations();
  }

  async function toggleRecording() {
    if (isRecording) {
      recorderRef.current?.stop();
      return;
    }
    try {
      await window.v8CyberCore?.requestMediaAccess?.("microphone");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        setIsRecording(false);
        void submitVoice(blob).catch((error) => {
          setStatus("发送失败");
          setEmotion("worried");
          addMessage({
            sender: "system",
            text: error instanceof Error ? error.message : "语音发送失败",
            emotion: "worried",
          });
        });
      };
      recorder.start();
      setIsRecording(true);
      setStatus("录音中");
      setEmotion("listening");
    } catch (error) {
      setStatus("麦克风不可用");
      setEmotion("worried");
      addMessage({
        sender: "system",
        text: error instanceof Error ? error.message : "无法打开麦克风",
        emotion: "worried",
      });
    }
  }

  const activeConversation = useMemo(
    () => conversations.find((item) => String(item.id) === activeConversationId) || null,
    [activeConversationId, conversations],
  );

  return (
    <CyberPet
      connected={connected}
      status={status}
      emotion={emotion}
      settings={settings}
      messages={messages}
      activeConversation={activeConversation ? summarizeConversation(activeConversation, projects) : null}
      isListening={isRecording}
      isSpeaking={isSpeaking}
      clickThrough={clickThrough}
      onOpenAdmin={() => window.v8CyberCore?.openAdmin?.(clientRef.current.getStoredAdminBaseUrl())}
      onOpenSettings={() => window.v8CyberCore?.openAdmin?.(`${clientRef.current.getStoredAdminBaseUrl()}/admin/desktop-pet`)}
      onToggleClickThrough={async () => {
        const next = !clickThrough;
        setClickThrough(next);
        await window.v8CyberCore?.setClickThrough?.(next);
      }}
      onToggleMuted={() => setSettings((current) => ({ ...current, muted: !current.muted }))}
      onToggleListening={() => void toggleRecording()}
      onQuit={() => window.v8CyberCore?.quit?.()}
    />
  );
}
