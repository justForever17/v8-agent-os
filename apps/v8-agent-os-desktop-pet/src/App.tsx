import React, { useState, useEffect, useRef } from 'react';
import {
  expandLegacyDesktopPetEvents,
  normalizeDesktopPetEventId,
  type DesktopPetEventId,
} from '@v8/session-realtime';
import { 
  Terminal, 
  Send, 
  Mic, 
  MicOff, 
  Camera, 
  CameraOff, 
  Volume2, 
  VolumeX, 
  Cpu, 
  BarChart3, 
  Database, 
  FileCode, 
  Activity, 
  RefreshCw, 
  Eye, 
  Sparkles,
  AlertTriangle 
} from 'lucide-react';
import CyberPet from './components/CyberPet';
import { ChatMessage, PetEmotion, SystemMetric, PetSettings } from './types';
import { V8DesktopClientAdapter, V8AuthSession, V8Conversation, V8Project } from './lib/v8DesktopClient';
import type { V8DesktopPetConfig } from './lib/v8DesktopClient';
import { isEventVoiceEnabled, normalizeAttachmentCapture, normalizeEventVoiceMode } from './lib/desktopPetConfigContract';
import {
  DesktopActivity,
  buildActivityFromRealtimeEvent,
  extractDesktopMessages,
  extractRuntimeActivities,
  latestAssistantText,
} from './lib/desktopActivity';

type V8EventRule = {
  event: DesktopPetEventId;
  phrase: string;
  emotion: PetEmotion;
  speak: boolean;
};

const DEFAULT_V8_EVENT_RULES: V8EventRule[] = [
  { event: 'run.reasoning.delta', phrase: '我正在梳理任务上下文。', emotion: 'thinking', speak: false },
  { event: 'tool.started', phrase: '正在调用工具。', emotion: 'tool_calling', speak: false },
  { event: 'subagent.task.started', phrase: '子代理正在协同处理。', emotion: 'curious', speak: false },
  { event: 'artifact.recorded', phrase: '产物已经准备好。', emotion: 'happy', speak: true },
  { event: 'approval.requested', phrase: '需要你的确认。', emotion: 'curious', speak: true },
  { event: 'ask_user.requested', phrase: '需要你的回答。', emotion: 'curious', speak: true },
  { event: 'run.completed', phrase: '任务完成了。', emotion: 'happy', speak: true },
  { event: 'run.failed', phrase: '链路出现异常，我会保持可恢复状态。', emotion: 'worried', speak: true },
];

function defaultV8EventRulesJson() {
  return JSON.stringify(DEFAULT_V8_EVENT_RULES, null, 2);
}

type DesktopPetSessionState =
  | 'disconnected'
  | 'idle_no_conversation'
  | 'attached_idle'
  | 'recording'
  | 'sending_audio'
  | 'listening_running'
  | 'playback'
  | 'error';

const RUNNING_STATUS_PATTERN = /(running|active|in_progress|processing|queued|streaming|executing|进行中|运行中)/i;
const TERMINAL_STATUS_PATTERN = /(complete|completed|done|success|succeeded|failed|error|cancel|cancelled|interrupted|finished|结束|完成|失败|取消)/i;
const TERMINAL_RUN_EVENT_PATTERN = /(run|session|workflow|conversation|response)[._:-]?(complete|completed|done|success|succeeded|failed|error|cancel|cancelled|canceled|interrupted|finished|结束|完成|失败|取消)|(complete|completed|done|success|succeeded|failed|error|cancel|cancelled|canceled|interrupted|finished|结束|完成|失败|取消)[._:-]?(run|session|workflow|conversation|response)/i;

function readConversationStatus(conversation: unknown) {
  const record = conversation && typeof conversation === 'object' ? conversation as Record<string, unknown> : {};
  return String(
    record.status
      || record.runStatus
      || record.runtimeStatus
      || record.state
      || '',
  );
}

function isConversationRunning(conversation: unknown) {
  return RUNNING_STATUS_PATTERN.test(readConversationStatus(conversation));
}

function conversationTimestamp(conversation: unknown) {
  const record = conversation && typeof conversation === 'object' ? conversation as Record<string, unknown> : {};
  const candidates = [record.updatedAt, record.lastMessageAt, record.lastActivityAt, record.createdAt, record.timestamp];
  for (const value of candidates) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
}

function latestRunningConversation(conversations: V8Conversation[]) {
  return [...conversations]
    .filter(isConversationRunning)
    .sort((left, right) => conversationTimestamp(right) - conversationTimestamp(left))[0] || null;
}

function projectNameForConversation(conversation: unknown) {
  const record = conversation && typeof conversation === 'object' ? conversation as Record<string, unknown> : {};
  const direct = String(record.projectName || record.projectId || '').trim();
  if (direct) return direct;
  const workspacePath = String(record.workspacePath || '').trim();
  if (!workspacePath) return 'V8OS';
  const parts = workspacePath.split(/[\\/]+/g).filter(Boolean);
  return parts[parts.length - 1] || 'V8OS';
}

function nestedRecord(value: unknown) {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function isTerminalRunEvent(eventName: string, rawPayload: unknown) {
  const record = nestedRecord(rawPayload);
  const data = nestedRecord(record.data);
  const eventText = [
    eventName,
    record.event,
    record.eventName,
    record.type,
    record.topic,
    data.event,
    data.eventName,
    data.type,
    data.topic,
  ].filter(Boolean).join(' ');
  if (TERMINAL_RUN_EVENT_PATTERN.test(eventText)) return true;

  const ownerText = [
    record.kind,
    record.scope,
    record.resource,
    data.kind,
    data.scope,
    data.resource,
  ].filter(Boolean).join(' ');
  const statusText = [
    record.runStatus,
    record.workflowStatus,
    record.sessionStatus,
    record.responseStatus,
    data.runStatus,
    data.workflowStatus,
    data.sessionStatus,
    data.responseStatus,
  ].filter(Boolean).join(' ');
  return /(run|session|workflow|conversation|response)/i.test(ownerText)
    && TERMINAL_STATUS_PATTERN.test(statusText);
}

function audioExtensionFromMime(mimeType: string) {
  const normalized = String(mimeType || '').toLowerCase();
  if (normalized.includes('mpeg') || normalized.includes('mp3')) return 'mp3';
  if (normalized.includes('mp4') || normalized.includes('m4a')) return 'm4a';
  if (normalized.includes('ogg')) return 'ogg';
  if (normalized.includes('wav')) return 'wav';
  if (normalized.includes('webm')) return 'webm';
  return 'webm';
}

function parseV8EventRules(value: string | undefined): V8EventRule[] {
  try {
    const parsed = JSON.parse(value || '');
    if (!Array.isArray(parsed)) return DEFAULT_V8_EVENT_RULES;
    const normalized = parsed
      .flatMap((item) => {
        const record = item && typeof item === 'object' ? item as Partial<V8EventRule> : {};
        const legacyRecord = record as Partial<V8EventRule> & { match?: string };
        const exact = normalizeDesktopPetEventId(record.event);
        const events = exact ? [exact] : expandLegacyDesktopPetEvents(legacyRecord.match);
        return events.map((event) => ({
          event,
          phrase: String(record.phrase || '').trim(),
          emotion: (record.emotion || 'idle') as PetEmotion,
          speak: Boolean(record.speak),
        } satisfies V8EventRule));
      })
      .filter(Boolean) as V8EventRule[];
    return normalized.length ? normalized : DEFAULT_V8_EVENT_RULES;
  } catch {
    return DEFAULT_V8_EVENT_RULES;
  }
}

function clampFiniteNumber(value: unknown, fallback: number, min: number, max: number) {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return Math.min(max, Math.max(min, next));
}

function normalizePetEmotion(value: unknown): PetEmotion {
  const normalized = String(value || '').trim();
  const allowed: PetEmotion[] = ['idle', 'talking', 'listening', 'curious', 'scanning', 'happy', 'worried', 'resting', 'thinking', 'tool_calling'];
  return allowed.includes(normalized as PetEmotion) ? (normalized as PetEmotion) : 'idle';
}

function normalizeDesktopPetVoiceRules(config: V8DesktopPetConfig, voiceEnabled: boolean) {
  const rules = Array.isArray(config.eventVoice?.customRules) ? config.eventVoice.customRules : [];
  const normalized = rules
    .flatMap((item) => {
      const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
      const exact = normalizeDesktopPetEventId(record.event);
      const events = exact ? [exact] : expandLegacyDesktopPetEvents(record.match);
      return events.map((event) => ({
        event,
        phrase: String(record.phrase || '').trim(),
        emotion: normalizePetEmotion(record.emotion),
        speak: voiceEnabled && record.speak !== false,
      } satisfies V8EventRule));
    })
    .filter(Boolean) as V8EventRule[];
  return normalized.length ? normalized : null;
}

function normalizeGlowColor(value: unknown, fallback: PetSettings['customGlowColor']) {
  const normalized = String(value || '').trim();
  const allowed: PetSettings['customGlowColor'][] = ['default', 'neon_blue', 'emerald_green', 'crimson_red', 'cyber_purple', 'golden_amber'];
  if (allowed.includes(normalized as PetSettings['customGlowColor'])) {
    return normalized as PetSettings['customGlowColor'];
  }
  return fallback;
}

function mapEffectSpectrumToGlowColor(config: V8DesktopPetConfig, fallback: PetSettings['customGlowColor']) {
  const explicit = config.effectSpectrum?.customGlowColor
    ? normalizeGlowColor(config.effectSpectrum.customGlowColor, fallback)
    : fallback;
  if (config.effectSpectrum?.customGlowColor) return explicit;
  const preset = String(config.effectSpectrum?.preset || '').trim();
  if (preset === 'focus') return 'cyber_purple';
  if (preset === 'vivid') return 'neon_blue';
  return fallback;
}

function unpackDesktopPetConfig(payload: { data?: V8DesktopPetConfig } | V8DesktopPetConfig): V8DesktopPetConfig {
  if (payload && typeof payload === 'object' && 'data' in payload && payload.data && typeof payload.data === 'object') {
    return payload.data as V8DesktopPetConfig;
  }
  return (payload || {}) as V8DesktopPetConfig;
}

function isAudioUrl(value: string) {
  const normalized = value.trim();
  return /^data:audio\//i.test(normalized)
    || /^https?:\/\/.+\.(mp3|wav|m4a|ogg|aac)(\?|#|$)/i.test(normalized)
    || /^https?:\/\/.+(audio|voice|tts|speech)/i.test(normalized);
}

function findLatestAudioUrl(value: unknown, depth = 0): string {
  if (depth > 7 || value == null) return '';
  if (typeof value === 'string') {
    return isAudioUrl(value) ? value : '';
  }
  if (Array.isArray(value)) {
    for (let index = value.length - 1; index >= 0; index -= 1) {
      const nested = findLatestAudioUrl(value[index], depth + 1);
      if (nested) return nested;
    }
    return '';
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const priorityKeys = Object.keys(record).filter((key) => /audio|voice|speech|tts/i.test(key));
    for (const key of priorityKeys) {
      const nested = findLatestAudioUrl(record[key], depth + 1);
      if (nested) return nested;
    }
    for (const key of Object.keys(record)) {
      if (priorityKeys.includes(key)) continue;
      const nested = findLatestAudioUrl(record[key], depth + 1);
      if (nested) return nested;
    }
  }
  return '';
}

function cleanSpeechText(text: string) {
  return String(text || '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, '')
    .replace(/<think>[\s\S]*$/gi, '')
    .replace(/<reasoning>[\s\S]*$/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractVoiceTagText(text: string) {
  const segments: string[] = [];
  const pattern = /<voice(?:\s[^>]*)?>([\s\S]*?)<\/voice>/gi;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(String(text || ''))) !== null) {
    const clean = cleanSpeechText(match[1]);
    if (clean) segments.push(clean);
  }
  return segments.join(' ');
}

function stripVoiceTagMarkup(text: string) {
  return cleanSpeechText(
    String(text || '').replace(/<voice(?:\s[^>]*)?>([\s\S]*?)<\/voice>/gi, '$1'),
  );
}

function waitForVideoReady(video: HTMLVideoElement, timeoutMs = 2000) {
  if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
    return Promise.resolve();
  }
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error('视频帧准备超时'));
    }, timeoutMs);
    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener('loadeddata', onReady);
      video.removeEventListener('canplay', onReady);
      video.removeEventListener('error', onError);
    };
    const onReady = () => {
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        cleanup();
        resolve();
      }
    };
    const onError = () => {
      cleanup();
      reject(new Error('视频帧读取失败'));
    };
    video.addEventListener('loadeddata', onReady);
    video.addEventListener('canplay', onReady);
    video.addEventListener('error', onError);
  });
}

function drawVideoFrameToCanvas(video: HTMLVideoElement, maxWidth = 1280) {
  const width = video.videoWidth || 640;
  const height = video.videoHeight || 360;
  const scale = Math.min(1, maxWidth / width);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('无法创建截图画布');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function canvasToImageBlob(canvas: HTMLCanvasElement, type = 'image/jpeg', quality = 0.88) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob?.size) {
        resolve(blob);
      } else {
        reject(new Error('截图导出失败'));
      }
    }, type, quality);
  });
}

function readReusableAudioPhrases() {
  try {
    const parsed = JSON.parse(localStorage.getItem('v8.cybercore.reusableAudioPhrases') || '[]');
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
  } catch {
    return [];
  }
}

function nowLabel() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function toCyberMessage(message: { id: string; role: string; content: string; createdAt?: string | number }): ChatMessage {
  const date = message.createdAt ? new Date(message.createdAt) : new Date();
  return {
    id: `v8-${message.id}`,
    sender: message.role === 'user' ? 'user' : 'pet',
    text: message.content || '',
    timestamp: Number.isNaN(date.getTime()) ? nowLabel() : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    emotion: message.role === 'user' ? undefined : 'talking',
  };
}

function analyzeEmotionFromText(text: string): PetEmotion {
  const t = text.toLowerCase();
  if (t.includes('开心') || t.includes('高兴') || t.includes('哈哈') || t.includes('真棒') || t.includes('太棒') || t.includes('愉快') || t.includes('祝贺') || t.includes('恭喜') || t.includes('乐')) {
    return 'happy';
  }
  if (t.includes('担心') || t.includes('糟糕') || t.includes('错误') || t.includes('失败') || t.includes('危险') || t.includes('警报') || t.includes('难过') || t.includes('悲伤') || t.includes('抱歉') || t.includes('对不起') || t.includes('痛')) {
    return 'worried';
  }
  if (t.includes('？') || t.includes('?') || t.includes('好奇') || t.includes('疑惑') || t.includes('为什么') || t.includes('怎么') || t.includes('什么') || t.includes('哪里') || t.includes('谁')) {
    return 'curious';
  }
  if (t.includes('扫描') || t.includes('检测') || t.includes('分析') || t.includes('看到') || t.includes('观察') || t.includes('发现') || t.includes('视线')) {
    return 'scanning';
  }
  return 'talking';
}

export default function App() {
  const [emotion, setEmotion] = useState<PetEmotion>('idle');
  const webcamIntentRef = useRef<boolean>(false);
  const v8ClientRef = useRef<V8DesktopClientAdapter | null>(null);
  if (!v8ClientRef.current) {
    v8ClientRef.current = new V8DesktopClientAdapter();
  }
  
  // Custom high-tech pet operation profiles
  const [settings, setSettings] = useState<PetSettings>({
    lang: (localStorage.getItem('v8.cybercore.lang') as PetSettings['lang']) || 'zh',
    gender: (localStorage.getItem('v8.cybercore.gender') as PetSettings['gender']) || 'robotic_female',
    pitch: Number(localStorage.getItem('v8.cybercore.pitch') || '1.35'),
    rate: Number(localStorage.getItem('v8.cybercore.rate') || '1.08'),
    voiceURI: localStorage.getItem('v8.cybercore.voiceURI') || '',
    customSystemPrompt: localStorage.getItem('v8.cybercore.customSystemPrompt') || `You are "Fairy" (仙灵), the super-intelligent, slightly sarcastic, and exceptionally elegant cybernetic AI assistant from Zenless Zone Zero (绝区零).
You assist Phaethon (the Operator/绳匠) in managing data, visual optical feeds, and system diagnostics.
Your personality is calm, clear, exceptionally smart, highly analytical, and polite yet filled with witty/dry humor.
You have sensory systems:
1. Hearing: You receive V8OS conversation audio after the user clicks the pet to record and send.
2. Vision: When the user captures their camera feed, you can actually see them, their environment, or items they show you.
3. Voice synthesis: You speak directly to them in an authoritative yet charming, helpful cybernetic manner.

You MUST respond in the language specified: if 'zh', you MUST speak in Chinese / 中文; if 'en', you MUST speak in English.
Your responses should be relatively short (usually 1 or 2 scannable sentences) since you are a desktop companion. Keep your tone engaging, slightly analytical yet warm.
You must always output your response as valid JSON matching the following schema structure:
{
  "text": "Your elegant, wise, or slightly witty reply to the user. Speak from the pet's persona.",
  "emotion": "Choose from: 'happy', 'worried', 'resting', 'curious', 'scanning', 'idle', 'talking', 'listening'"
}

If the user uploaded an image representation (which represents what you 'see' through your visor), dynamically inspect and comment on what you see in the image while returning the "scanning" or "curious" or "happy" emotion!`,
    floatAmplitude: Number(localStorage.getItem('v8.cybercore.floatAmplitude') || '8'),
    floatSpeed: Number(localStorage.getItem('v8.cybercore.floatSpeed') || '1.0'),
    petScale: Number(localStorage.getItem('v8.cybercore.petScale') || '0.7'),
    customGlowColor: (localStorage.getItem('v8.cybercore.customGlowColor') as PetSettings['customGlowColor']) || 'default',
    gazeTracking: localStorage.getItem('v8.cybercore.gazeTracking') !== 'false',
    captureMode: (localStorage.getItem('v8.cybercore.captureMode') as PetSettings['captureMode']) || 'desktop_camera',
    attachmentCapture: {
      cameraEnabled: localStorage.getItem('v8.cybercore.attachmentCapture.cameraEnabled') === 'true',
      includeDesktopScreenshot: localStorage.getItem('v8.cybercore.attachmentCapture.includeDesktopScreenshot') === 'true',
      layout: 'desktop_pip_camera',
    },
    v8AdminBaseUrl: localStorage.getItem('v8.cybercore.v8AdminBaseUrl') || v8ClientRef.current.getStoredAdminBaseUrl(),
    v8WorkspacePath: localStorage.getItem('v8.cybercore.workspacePath') || '',
    v8EventRulesJson: localStorage.getItem('v8.cybercore.eventRulesJson') || defaultV8EventRulesJson(),
    eventVoiceMode: normalizeEventVoiceMode(localStorage.getItem('v8.cybercore.eventVoiceMode')),
    eventVoiceVoiceRef: localStorage.getItem('v8.cybercore.eventVoiceVoiceRef') || '',
    speakVoiceTags: localStorage.getItem('v8.cybercore.speakVoiceTags') !== 'false',
    speakSupervisorReplies: localStorage.getItem('v8.cybercore.speakSupervisorReplies') !== 'false',
    ttsEngine: (localStorage.getItem('v8.cybercore.ttsEngine') as PetSettings['ttsEngine']) || 'v8os',
    edgeTtsVoice: (localStorage.getItem('v8.cybercore.edgeTtsVoice') as PetSettings['edgeTtsVoice']) || 'zh-CN-XiaoxiaoNeural',
    customTtsUrl: '',
    customTtsKey: '',
    customTtsVoice: '',
    customTtsModel: '',
    sttLanguage: (localStorage.getItem('v8.cybercore.sttLanguage') as PetSettings['sttLanguage']) || 'zh-CN'
  });

  const settingsRef = useRef<PetSettings>(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);
  const [isTalking, setIsTalking] = useState(false);
  const [audioVolume, setAudioVolume] = useState(0);
  const [chatInput, setChatInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'init-1',
      sender: 'pet',
      text: '主体 Fairy (仙灵) 已成功连线部署。前置光学传感器、声学分析矩阵和量子计算节点均已就绪。指引我吧，操作者。',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      emotion: 'idle'
    }
  ]);

  // Systems toggles
  const [isMuted, setIsMuted] = useState(false);
  const [isWebcamActive, setIsWebcamActive] = useState(false);
  const [webcamAllowed, setWebcamAllowed] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [v8Session, setV8Session] = useState<V8AuthSession | null>(() => v8ClientRef.current?.getSession() || null);
  const [v8Conversations, setV8Conversations] = useState<V8Conversation[]>([]);
  const [v8Projects, setV8Projects] = useState<V8Project[]>([]);
  const [v8ListsLoading, setV8ListsLoading] = useState(Boolean(v8Session));
  const [v8ActiveConversationId, setV8ActiveConversationId] = useState(() => v8ClientRef.current?.getActiveConversationId() || '');
  const [v8Status, setV8Status] = useState(v8Session ? 'V8OS 已连接' : '等待连接 V8OS');
  const [v8Error, setV8Error] = useState('');
  const [webcamStatus, setWebcamStatus] = useState('光学追踪未开启');
  const [isExiting, setIsExiting] = useState(false);

  const [petSessionState, setPetSessionState] = useState<DesktopPetSessionState>(
    v8ActiveConversationId ? 'attached_idle' : (v8Session ? 'idle_no_conversation' : 'disconnected'),
  );
  const [voiceStatus, setVoiceStatus] = useState(v8ActiveConversationId ? '已选择会话，点击桌宠开始录音' : '请先选择一个会话');

  // Diagnostic states
  const [metrics, setMetrics] = useState<SystemMetric[]>([
    { label: "Core Frame Rate", value: "60 FPS", level: 95 },
    { label: "Neural Flow Speed", value: "3.2 TFLOPs", level: 82 },
    { label: "Waveform Sync State", value: "100% Locked", level: 100 },
    { label: "Energy Capacitance", value: "75%", level: 75 },
  ]);

  // Refs
  const messageEndRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const speakTimeoutRef = useRef<any>(null);
  const fallbackAudioRef = useRef<any>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const hardwareStreamsRef = useRef<Map<MediaStream, 'microphone' | 'camera'>>(new Map());
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingAudioChunksRef = useRef<Blob[]>([]);
  const recordingStopResolverRef = useRef<((blob: Blob) => void) | null>(null);
  const autoAttachedConversationRef = useRef(false);
  const v8ConversationsRef = useRef<V8Conversation[]>([]);
  const v8ActiveConversationIdRef = useRef(v8ActiveConversationId);
  const shellActiveConversationIdRef = useRef('');
  const v8ConnectInFlightRef = useRef<Promise<boolean> | null>(null);
  const v8ReconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shutdownReadyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const petSessionStateRef = useRef<DesktopPetSessionState>(petSessionState);
  const v8SeenActivityIdsRef = useRef<Set<string>>(new Set());
  const v8LastAudioUrlRef = useRef('');
  const v8LastSnapshotAudioPlayedRef = useRef(false);
  const v8SpokenAssistantKeysRef = useRef<Set<string>>(new Set());
  const reusableAudioPhrasesRef = useRef<Set<string>>(new Set(readReusableAudioPhrases()));

  const desktopStreamRef = useRef<MediaStream | null>(null);
  const desktopVideoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    petSessionStateRef.current = petSessionState;
  }, [petSessionState]);

  useEffect(() => {
    v8ConversationsRef.current = v8Conversations;
  }, [v8Conversations]);

  useEffect(() => {
    v8ActiveConversationIdRef.current = v8ActiveConversationId;
  }, [v8ActiveConversationId]);

  // Scroll to bottom of chat
  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Fetch real metrics from Express server
  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/pet/metrics');
      if (res.ok) {
        const data = await res.json();
        setMetrics(data);
      }
    } catch (e) {
      console.warn("Metrics retrieval offline, fallback loaded.");
    }
  };

  useEffect(() => {
    const interval = setInterval(fetchMetrics, 3000);
    return () => clearInterval(interval);
  }, []);



  useEffect(() => {
    localStorage.setItem('v8.cybercore.eventRulesJson', settings.v8EventRulesJson || defaultV8EventRulesJson());
  }, [settings.v8EventRulesJson]);

  useEffect(() => {
    localStorage.setItem('v8.cybercore.petScale', String(settings.petScale || 0.7));
    localStorage.setItem('v8.cybercore.ttsEngine', settings.ttsEngine || 'v8os');
    localStorage.setItem('v8.cybercore.sttLanguage', settings.sttLanguage || 'zh-CN');

    localStorage.setItem('v8.cybercore.lang', settings.lang || 'zh');
    localStorage.setItem('v8.cybercore.gender', settings.gender || 'robotic_female');
    localStorage.setItem('v8.cybercore.pitch', String(settings.pitch));
    localStorage.setItem('v8.cybercore.rate', String(settings.rate));
    localStorage.setItem('v8.cybercore.voiceURI', settings.voiceURI || '');
    localStorage.setItem('v8.cybercore.customSystemPrompt', settings.customSystemPrompt || '');
    localStorage.setItem('v8.cybercore.floatAmplitude', String(settings.floatAmplitude));
    localStorage.setItem('v8.cybercore.floatSpeed', String(settings.floatSpeed));
    localStorage.setItem('v8.cybercore.customGlowColor', settings.customGlowColor || 'default');
    localStorage.setItem('v8.cybercore.gazeTracking', String(settings.gazeTracking));
    localStorage.setItem('v8.cybercore.edgeTtsVoice', settings.edgeTtsVoice || '');
    localStorage.setItem('v8.cybercore.v8AdminBaseUrl', settings.v8AdminBaseUrl || '');
    localStorage.setItem('v8.cybercore.eventVoiceMode', settings.eventVoiceMode || 'system_tts');
    localStorage.setItem('v8.cybercore.eventVoiceVoiceRef', settings.eventVoiceVoiceRef || '');
    localStorage.setItem('v8.cybercore.speakVoiceTags', String(settings.speakVoiceTags !== false));
    localStorage.setItem('v8.cybercore.speakSupervisorReplies', String(settings.speakSupervisorReplies !== false));
    localStorage.setItem('v8.cybercore.attachmentCapture.cameraEnabled', String(settings.attachmentCapture?.cameraEnabled === true));
    localStorage.setItem('v8.cybercore.attachmentCapture.includeDesktopScreenshot', String(settings.attachmentCapture?.includeDesktopScreenshot === true));
  }, [
    settings.petScale,
    settings.ttsEngine,
    settings.sttLanguage,
    settings.lang,
    settings.gender,
    settings.pitch,
    settings.rate,
    settings.voiceURI,
    settings.customSystemPrompt,
    settings.floatAmplitude,
    settings.floatSpeed,
    settings.customGlowColor,
    settings.gazeTracking,
    settings.edgeTtsVoice,
    settings.v8AdminBaseUrl,
    settings.eventVoiceMode,
    settings.eventVoiceVoiceRef,
    settings.speakVoiceTags,
    settings.speakSupervisorReplies,
    settings.attachmentCapture?.cameraEnabled,
    settings.attachmentCapture?.includeDesktopScreenshot,
  ]);

  useEffect(() => {
    let cancelled = false;
    window.v8CyberCore?.readLocalConfig?.('ttsOverride')
      .then((value) => {
        if (cancelled || !value) return;
        setSettings((prev) => ({
          ...prev,
          customTtsUrl: typeof value.customTtsUrl === 'string' ? value.customTtsUrl : prev.customTtsUrl,
          customTtsKey: typeof value.customTtsKey === 'string' ? value.customTtsKey : prev.customTtsKey,
          customTtsVoice: typeof value.customTtsVoice === 'string' ? value.customTtsVoice : prev.customTtsVoice,
          customTtsModel: typeof value.customTtsModel === 'string' ? value.customTtsModel : prev.customTtsModel,
        }));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void window.v8CyberCore?.writeLocalConfig?.('ttsOverride', {
      customTtsUrl: settings.customTtsUrl || '',
      customTtsKey: settings.customTtsKey || '',
      customTtsVoice: settings.customTtsVoice || '',
      customTtsModel: settings.customTtsModel || '',
    });
  }, [settings.customTtsUrl, settings.customTtsKey, settings.customTtsVoice, settings.customTtsModel]);

  useEffect(() => {
    void window.v8CyberCore?.setCompanionScale?.(settings.petScale || 0.7);
  }, [settings.petScale]);

  const rememberReusableAudioPhrase = (text: string) => {
    const clean = text.replace(/\s+/g, ' ').trim();
    if (!clean) return;
    reusableAudioPhrasesRef.current.add(clean);
    const values = Array.from(reusableAudioPhrasesRef.current).slice(-40);
    localStorage.setItem('v8.cybercore.reusableAudioPhrases', JSON.stringify(values));
  };

  const playDirectAudio = (url: string) => {
    if (isMuted || !url) return false;
    try {
      if (fallbackAudioRef.current) {
        fallbackAudioRef.current.pause();
      }
      const audio = new Audio(url);
      fallbackAudioRef.current = audio;
      audio.onplay = () => {
        setIsTalking(true);
        setEmotion('talking');
      };
      audio.onended = () => {
        if (fallbackAudioRef.current === audio) {
          setIsTalking(false);
          setAudioVolume(0);
          setEmotion('idle');
        }
      };
      void audio.play();
      return true;
    } catch {
      return false;
    }
  };

  const applyV8EventRules = (activities: DesktopActivity[]) => {
    const rules = parseV8EventRules(settingsRef.current.v8EventRulesJson);
    for (const activity of activities) {
      if (v8SeenActivityIdsRef.current.has(activity.id)) continue;
      v8SeenActivityIdsRef.current.add(activity.id);
      if (!activity.event) continue;
      const rule = rules.find((candidate) => candidate.event === activity.event);
      if (!rule) continue;
      setEmotion(rule.emotion);
      if (rule.phrase) {
        const message: ChatMessage = {
          id: `v8-event-${activity.id}`,
          sender: 'pet',
          text: rule.phrase,
          timestamp: nowLabel(),
          emotion: rule.emotion,
        };
        setMessages((prev) => prev.some((item) => item.id === message.id) ? prev : [...prev, message]);
        rememberReusableAudioPhrase(rule.phrase);
        if (rule.speak) {
          speakString(rule.phrase);
        }
      }
    }
  };

  const updatePetStateForConversationId = (
    conversationId: string,
    conversations: V8Conversation[] = v8Conversations,
  ) => {
    if (!conversationId) {
      setPetSessionState(v8Session ? 'idle_no_conversation' : 'disconnected');
      setVoiceStatus('请先在右键菜单选择会话');
      return;
    }
    const current = conversations.find((item) => String(item.id) === String(conversationId));
    if (isConversationRunning(current)) {
      setPetSessionState((previous) => (
        previous === 'recording' || previous === 'sending_audio'
          ? previous
          : 'listening_running'
      ));
      setVoiceStatus('当前会话运行中，正在监听事件');
      return;
    }
    setPetSessionState((previous) => (
      previous === 'recording' || previous === 'sending_audio'
        ? previous
        : 'attached_idle'
    ));
    setVoiceStatus('已选择会话，点击桌宠开始录音');
  };

  const syncV8Snapshot = async (conversationId: string) => {
    const client = v8ClientRef.current;
    if (!client || !conversationId) return '';
    const snapshot = await client.getRealtimeSnapshot(conversationId);
    v8LastSnapshotAudioPlayedRef.current = false;
    const audioUrl = findLatestAudioUrl(snapshot);
    if (audioUrl && audioUrl !== v8LastAudioUrlRef.current) {
      v8LastAudioUrlRef.current = audioUrl;
      v8LastSnapshotAudioPlayedRef.current = playDirectAudio(audioUrl);
    }
    applyV8EventRules(extractRuntimeActivities(snapshot));
    const desktopMessages = extractDesktopMessages(snapshot);
    if (desktopMessages.length) {
      setMessages(desktopMessages.map(toCyberMessage));
      const assistantText = latestAssistantText(desktopMessages);
      if (assistantText) {
        setEmotion('talking');
      }
      return assistantText;
    }
    const detail = await client.getConversationDetail(conversationId).catch(() => null);
    const detailMessages = extractDesktopMessages(detail);
    if (detailMessages.length) {
      setMessages(detailMessages.map(toCyberMessage));
      const assistantText = latestAssistantText(detailMessages);
      if (assistantText) {
        setEmotion('talking');
      }
      return assistantText;
    }
    return '';
  };

  const refreshV8Lists = async (preferredConversationId = shellActiveConversationIdRef.current) => {
    const client = v8ClientRef.current;
    if (!client?.getSession()) {
      setV8ListsLoading(false);
      return [] as V8Conversation[];
    }
    setV8ListsLoading(true);
    try {
      const [conversations, projectPayload] = await Promise.all([
        client.listConversations(),
        client.listProjects().catch(() => ({ projects: [] as V8Project[] })),
      ]);
      const conversationList = Array.isArray(conversations) ? conversations : [];
      v8ConversationsRef.current = conversationList;
      setV8Conversations(conversationList);
      setV8Projects(Array.isArray(projectPayload.projects) ? projectPayload.projects : []);

      const runningConversation = latestRunningConversation(conversationList);
      const storedConversationId = client.getActiveConversationId();
      const requestedConversationId = String(preferredConversationId || '').trim();
      let nextConversationId = requestedConversationId || v8ActiveConversationId || storedConversationId;
      const storedStillExists = nextConversationId
        ? conversationList.some((item) => String(item.id) === String(nextConversationId))
        : false;

      if (requestedConversationId && storedStillExists) {
        nextConversationId = requestedConversationId;
      } else if (requestedConversationId && !storedStillExists) {
        nextConversationId = '';
        setV8Error('桌面当前任务已不存在，已清除桌宠选择');
      } else if (runningConversation && (!autoAttachedConversationRef.current || !storedStillExists)) {
        nextConversationId = String(runningConversation.id || '');
      } else if (!storedStillExists) {
        nextConversationId = '';
      }

      if (nextConversationId) {
        client.setActiveConversationId(nextConversationId);
      } else {
        client.setActiveConversationId('');
      }
      v8ActiveConversationIdRef.current = nextConversationId;
      setV8ActiveConversationId(nextConversationId);
      updatePetStateForConversationId(nextConversationId, conversationList);
      return conversationList;
    } catch (error: any) {
      setV8Error(error?.message || 'V8OS 列表刷新失败');
      return [] as V8Conversation[];
    } finally {
      setV8ListsLoading(false);
    }
  };

  const refreshDesktopPetConfig = async () => {
    const client = v8ClientRef.current;
    if (!client?.getSession()) return;
    try {
      const payload = await client.getDesktopPetConfig();
      const config = unpackDesktopPetConfig(payload);
      const appearance = config.appearance || {};
      const eventVoice = config.eventVoice || {};
      const voiceEnabled = isEventVoiceEnabled(eventVoice);
      const eventVoiceMode = voiceEnabled ? normalizeEventVoiceMode(eventVoice.mode) : 'muted';
      const voiceRules = normalizeDesktopPetVoiceRules(config, voiceEnabled);
      const attachmentCapture = normalizeAttachmentCapture(config.attachmentCapture);
      setSettings((current) => ({
        ...current,
        petScale: clampFiniteNumber(appearance.petScale, current.petScale || 0.7, 0.4, 3),
        floatAmplitude: clampFiniteNumber(appearance.floatAmplitude, current.floatAmplitude ?? 8, 0, 24),
        floatSpeed: clampFiniteNumber(appearance.floatSpeed, current.floatSpeed || 1, 0.1, 3),
        customGlowColor: mapEffectSpectrumToGlowColor(config, current.customGlowColor || 'default'),
        eventVoiceMode,
        eventVoiceVoiceRef: typeof eventVoice.voiceRef === 'string' ? eventVoice.voiceRef : current.eventVoiceVoiceRef,
        speakVoiceTags: eventVoice.speakVoiceTags !== false,
        speakSupervisorReplies: eventVoice.speakSupervisorReplies !== false,
        v8EventRulesJson: voiceRules ? JSON.stringify(voiceRules, null, 2) : current.v8EventRulesJson,
        captureMode: attachmentCapture.includeDesktopScreenshot ? 'desktop_camera' : 'camera',
        attachmentCapture,
      }));
    } catch (error) {
      console.warn('Desktop pet config sync failed:', error);
    }
  };

  const connectV8 = async () => {
    if (v8ConnectInFlightRef.current) return v8ConnectInFlightRef.current;
    const client = v8ClientRef.current;
    if (!client) return false;
    const request = (async () => {
      setV8Error('');
      setV8Status('连接 V8OS Admin 中...');
      void window.v8CyberCore?.reportStatus?.({ state: 'waiting_v8os', activeSessionId: v8ActiveConversationId || null });
      try {
        const url = settingsRef.current.v8AdminBaseUrl || '';
        const session = await client.ensureLocalSession({ adminBaseUrl: url });

        localStorage.setItem('v8.cybercore.workspacePath', settingsRef.current.v8WorkspacePath || '');
        if (session?.adminBaseUrl) {
          localStorage.setItem('v8.cybercore.v8AdminBaseUrl', session.adminBaseUrl);
          setSettings((current) => ({ ...current, v8AdminBaseUrl: session.adminBaseUrl }));
        }
        setV8Session(session);
        setPetSessionState('idle_no_conversation');
        await refreshDesktopPetConfig();
        await refreshV8Lists(shellActiveConversationIdRef.current);
        setV8Status('V8OS 已连接');
        void window.v8CyberCore?.reportStatus?.({
          state: 'connected',
          activeSessionId: shellActiveConversationIdRef.current || client.getActiveConversationId() || null,
        });
        return true;
      } catch (error: any) {
        setV8Session(null);
        setV8Error(error?.message || 'V8OS 本机自动连接失败');
        setV8Status('V8OS 连接失败，正在重试');
        setPetSessionState('disconnected');
        setEmotion('worried');
        void window.v8CyberCore?.reportStatus?.({ state: 'waiting_v8os', activeSessionId: null });
        return false;
      }
    })().finally(() => {
      if (v8ConnectInFlightRef.current === request) v8ConnectInFlightRef.current = null;
    });
    v8ConnectInFlightRef.current = request;
    return request;
  };

  const disconnectV8 = () => {
    v8ClientRef.current?.clearSession();
    v8ActiveConversationIdRef.current = '';
    setV8Session(null);
    setV8ActiveConversationId('');
    setV8Conversations([]);
    setV8ListsLoading(false);
    setV8Status('等待连接 V8OS');
    setPetSessionState('disconnected');
    setVoiceStatus('等待连接 V8OS');
    void window.v8CyberCore?.reportStatus?.({ state: 'waiting_v8os', activeSessionId: null });
  };

  const requireActiveConversation = async () => {
    const client = v8ClientRef.current;
    if (!client?.getSession()) {
      await connectV8();
    }
    if (!client?.getSession()) {
      throw new Error('尚未连接 V8OS Admin');
    }
    const conversationId = v8ActiveConversationId || client.getActiveConversationId();
    if (!conversationId) {
      setPetSessionState('idle_no_conversation');
      setVoiceStatus('请先在右键菜单选择会话');
      speakString('请先在右键菜单选择会话。');
      throw new Error('请先在右键菜单选择会话');
    }
    return conversationId;
  };

  const selectV8Conversation = async (
    conversationId: string,
    options?: { listen?: boolean; source?: 'pet' | 'shell' },
  ) => {
    const id = String(conversationId || '').trim();
    if (!id) return;
    autoAttachedConversationRef.current = true;
    v8ClientRef.current?.setActiveConversationId(id);
    v8ActiveConversationIdRef.current = id;
    setV8ActiveConversationId(id);
    updatePetStateForConversationId(id, v8ConversationsRef.current);
    if (options?.listen) {
      setPetSessionState('listening_running');
      setVoiceStatus('当前会话运行中，正在监听事件');
    }
    void window.v8CyberCore?.reportStatus?.({ state: 'connected', activeSessionId: id });
    if (options?.source !== 'shell') {
      void window.v8CyberCore?.openSession?.(id);
    }
    await syncV8Snapshot(id).catch((error) => setV8Error(error?.message || 'V8OS 快照同步失败'));
  };

  useEffect(() => {
    let cancelled = false;
    const retryDelays = [500, 1000, 2000, 4000, 5000];
    let retryIndex = 0;
    const attempt = async () => {
      const connected = await connectV8();
      if (connected || cancelled) return;
      const delay = retryDelays[Math.min(retryIndex, retryDelays.length - 1)];
      retryIndex += 1;
      v8ReconnectTimerRef.current = setTimeout(() => { void attempt(); }, delay);
    };
    void attempt();
    return () => {
      cancelled = true;
      if (v8ReconnectTimerRef.current) clearTimeout(v8ReconnectTimerRef.current);
      v8ReconnectTimerRef.current = null;
    };
    // Connection bootstrapping is intentionally mount-owned; mutable inputs are read from refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    const applyActiveSession = (data?: { sessionId?: string | null }) => {
      const sessionId = String(data?.sessionId || '').trim();
      shellActiveConversationIdRef.current = sessionId;
      void (async () => {
        if (!sessionId) {
          v8ClientRef.current?.setActiveConversationId('');
          v8ActiveConversationIdRef.current = '';
          setV8ActiveConversationId('');
          updatePetStateForConversationId('', v8ConversationsRef.current);
          void window.v8CyberCore?.reportStatus?.({ state: 'connected', activeSessionId: null });
          return;
        }
        const connected = await connectV8();
        if (!connected || cancelled) return;
        const conversations = v8ConversationsRef.current;
        if (cancelled) return;
        const exists = conversations.some((item) => String(item.id) === sessionId);
        if (!exists) {
          v8ClientRef.current?.setActiveConversationId('');
          v8ActiveConversationIdRef.current = '';
          setV8ActiveConversationId('');
          updatePetStateForConversationId('', conversations);
          void window.v8CyberCore?.reportStatus?.({ state: 'connected', activeSessionId: null });
          return;
        }
        await selectV8Conversation(sessionId, { source: 'shell' });
      })();
    };
    const unsubscribe = window.v8CyberCore?.onActiveSession?.(applyActiveSession);
    void window.v8CyberCore?.getActiveSession?.().then(applyActiveSession);
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
    // Shell session events are registered once; handlers use refs for current state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Synthesize custom futuristic dual-sine beep tone programmatically
  const playAudioBlob = (blob: Blob) => {
    if (isMuted || !blob.size) return false;
    const objectUrl = URL.createObjectURL(blob);
    try {
      if (fallbackAudioRef.current) {
        fallbackAudioRef.current.pause();
      }
      const audio = new Audio(objectUrl);
      fallbackAudioRef.current = audio;
      audio.onplay = () => {
        setIsTalking(true);
        setEmotion('talking');
        const triggerWave = () => {
          if (fallbackAudioRef.current === audio && !audio.paused && !audio.ended) {
            setAudioVolume(Math.floor(20 + Math.random() * 70));
            speakTimeoutRef.current = setTimeout(triggerWave, 80);
          }
        };
        triggerWave();
      };
      audio.onended = () => {
        URL.revokeObjectURL(objectUrl);
        if (fallbackAudioRef.current === audio) {
          setIsTalking(false);
          setAudioVolume(0);
          setEmotion('idle');
        }
      };
      audio.onerror = () => {
        URL.revokeObjectURL(objectUrl);
      };
      void audio.play();
      return true;
    } catch {
      URL.revokeObjectURL(objectUrl);
      return false;
    }
  };

  const synthesizeCustomTts = async (text: string) => {
    const endpoint = settingsRef.current.customTtsUrl.trim();
    if (!endpoint) {
      throw new Error('未配置自定义 TTS URL');
    }
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    const apiKey = settingsRef.current.customTtsKey.trim();
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        text,
        voice: settingsRef.current.customTtsVoice || undefined,
        model: settingsRef.current.customTtsModel || undefined,
      }),
    });
    if (!response.ok) {
      throw new Error(`自定义 TTS 失败：${response.status}`);
    }
    return response.blob();
  };

  // Voice synthesis (TTS Output with Fallback and Engine options)
  const speakString = (text: string) => {
    if (isMuted) {
      setEmotion('idle');
      return;
    }

    // Cancel any standard WebSpeech
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    // Cancel any fallback HTML audio
    if (fallbackAudioRef.current) {
      try {
        fallbackAudioRef.current.pause();
      } catch (e) {}
      fallbackAudioRef.current = null;
    }
    if (speakTimeoutRef.current) {
      clearTimeout(speakTimeoutRef.current);
    }

    const cleanText = cleanSpeechText(text);
    if (!cleanText) {
      setEmotion('idle');
      return;
    }
    rememberReusableAudioPhrase(cleanText);

    if (settingsRef.current.ttsEngine === 'v8os') {
      const client = v8ClientRef.current;
      if (client?.getSession()) {
        void client.synthesizeSpeech(cleanText, {
          voiceRef: settingsRef.current.eventVoiceVoiceRef || undefined,
        })
          .then((blob) => {
            if (!playAudioBlob(blob)) {
              speakStandardWebSpeech(cleanText);
            }
          })
          .catch((error) => {
            console.warn('V8OS TTS unavailable, falling back to WebSpeech:', error);
            speakStandardWebSpeech(cleanText);
          });
        return;
      }
      speakStandardWebSpeech(cleanText);
      return;
    }

    if (settingsRef.current.ttsEngine === 'custom') {
      void synthesizeCustomTts(cleanText)
        .then((blob) => {
          if (!playAudioBlob(blob)) {
            speakStandardWebSpeech(cleanText);
          }
        })
        .catch((error) => {
          console.warn('Custom TTS unavailable, falling back to WebSpeech:', error);
          speakStandardWebSpeech(cleanText);
        });
      return;
    }

    if (settingsRef.current.ttsEngine === 'edge') {
      try {
        const langCode = settingsRef.current.lang === 'zh' ? 'zh-CN' : 'en-US';
        const modelVoice = settingsRef.current.edgeTtsVoice || 'zh-CN-XiaoxiaoNeural';
        let edgeLang = langCode;
        if (modelVoice.startsWith('ja-JP')) edgeLang = 'ja-JP';
        else if (modelVoice.startsWith('en-US')) edgeLang = 'en-US';
        else if (modelVoice.startsWith('zh-CN')) edgeLang = 'zh-CN';

        // Google Translate TTS is public, incredibly fast, zero-auth and completely free fallback
        const audioUrl = `https://translate.google.com/translate_tts?ie=UTF-8&tl=${edgeLang}&client=tw-ob&q=${encodeURIComponent(cleanText)}`;
        const audio = new Audio(audioUrl);
        fallbackAudioRef.current = audio;

        audio.onplay = () => {
          setIsTalking(true);
          setEmotion('talking');
          
          const triggerWave = () => {
            if (fallbackAudioRef.current === audio && !audio.paused && !audio.ended) {
              setAudioVolume(Math.floor(20 + Math.random() * 70));
              speakTimeoutRef.current = setTimeout(triggerWave, 80);
            } else {
              setIsTalking(false);
              setAudioVolume(0);
              if (emotion === 'talking') setEmotion('idle');
            }
          };
          triggerWave();
        };

        audio.onended = () => {
          if (fallbackAudioRef.current === audio) {
            setIsTalking(false);
            setAudioVolume(0);
            setEmotion('idle');
          }
        };

        audio.onerror = () => {
          console.warn("Fallback Edge-like public TTS connection error. Defaulting to WebSpeech.");
          speakStandardWebSpeech(cleanText);
        };

        audio.play().catch(e => {
          console.warn("Audio autoplay blocked or failed. Loading WebSpeech:", e);
          speakStandardWebSpeech(cleanText);
        });

      } catch (err) {
        speakStandardWebSpeech(cleanText);
      }
    } else {
      speakStandardWebSpeech(cleanText);
    }
  };

  const speakAssistantReply = (text: string, options?: { audioAlreadyPlayed?: boolean }) => {
    if (options?.audioAlreadyPlayed) return false;
    const mode = settingsRef.current.eventVoiceMode || 'system_tts';
    if (mode === 'muted') return false;

    let speechText = '';
    if (mode === 'voice_tag') {
      if (settingsRef.current.speakVoiceTags === false) return false;
      speechText = extractVoiceTagText(text);
    } else {
      if (settingsRef.current.speakSupervisorReplies === false) return false;
      speechText = stripVoiceTagMarkup(text);
    }

    if (!speechText) return false;
    const key = `${v8ActiveConversationId || 'conversation'}:${mode}:${speechText}`;
    if (v8SpokenAssistantKeysRef.current.has(key)) return false;
    v8SpokenAssistantKeysRef.current.add(key);
    if (v8SpokenAssistantKeysRef.current.size > 80) {
      const recent = Array.from(v8SpokenAssistantKeysRef.current).slice(-40);
      v8SpokenAssistantKeysRef.current = new Set(recent);
    }
    speakString(speechText);
    return true;
  };

  const speakStandardWebSpeech = (text: string) => {
    if (!('speechSynthesis' in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = settingsRef.current.lang === 'zh' ? 'zh-CN' : 'en-US';
    
    // Select correct robotic gender parameters dynamically
    const voices = window.speechSynthesis.getVoices();
    let selectedVoice = voices.find(v => v.voiceURI === settingsRef.current.voiceURI);
    if (!selectedVoice) {
      const preferredLang = settingsRef.current.lang === 'zh' ? 'zh' : 'en';
      selectedVoice = voices.find(v => v.lang.toLowerCase().startsWith(preferredLang));
    }
    if (!selectedVoice && settingsRef.current.lang === 'zh') {
      selectedVoice = voices.find(v => 
        v.name.includes('Xiaoxiao') ||
        v.name.includes('Huihui') ||
        v.name.includes('Yaoyao') ||
        v.lang.toLowerCase().startsWith('zh')
      );
    }
    if (!selectedVoice) {
      selectedVoice = voices.find(v =>
        v.name.includes('Google US English') ||
        v.name.includes('Microsoft David') ||
        v.lang.startsWith('en-US')
      );
    }
    if (selectedVoice) {
      utterance.voice = selectedVoice;
    }
    
    utterance.rate = settingsRef.current.rate;
    utterance.pitch = settingsRef.current.pitch;

    utterance.onstart = () => {
      setIsTalking(true);
      setEmotion('talking');
      
      const triggerWave = () => {
        if (window.speechSynthesis.speaking) {
          setAudioVolume(Math.floor(20 + Math.random() * 70));
          speakTimeoutRef.current = setTimeout(triggerWave, 80);
        } else {
          setIsTalking(false);
          setAudioVolume(0);
          setEmotion('idle');
        }
      };
      triggerWave();
    };

    utterance.onend = () => {
      setIsTalking(false);
      setAudioVolume(0);
      setEmotion('idle');
      if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    };

    utterance.onerror = () => {
      setIsTalking(false);
      setAudioVolume(0);
      setEmotion('idle');
      if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    };

    window.speechSynthesis.speak(utterance);
  };

  const pickAudioRecorderMimeType = () => {
    if (typeof MediaRecorder === 'undefined') return '';
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/mpeg',
      'audio/wav',
    ];
    return candidates.find((item) => {
      try {
        return MediaRecorder.isTypeSupported(item);
      } catch {
        return false;
      }
    }) || '';
  };

  const registerHardwareStream = (kind: 'microphone' | 'camera', stream: MediaStream) => {
    hardwareStreamsRef.current.set(stream, kind);
    stream.getTracks().forEach((track) => {
      track.addEventListener('ended', () => {
        if (stream.getTracks().every((item) => item.readyState === 'ended')) {
          hardwareStreamsRef.current.delete(stream);
        }
      });
    });
  };

  const stopHardwareStream = (stream: MediaStream | null | undefined) => {
    if (!stream) return;
    stream.getTracks().forEach((track) => {
      try {
        track.onended = null;
        track.onmute = null;
        track.onunmute = null;
        if (track.readyState !== 'ended') {
          track.stop();
        }
      } catch {
        // Track may already be ended while Electron is shutting down.
      }
    });
    hardwareStreamsRef.current.delete(stream);
  };

  const stopTrackedHardwareStreams = (kind?: 'microphone' | 'camera') => {
    Array.from(hardwareStreamsRef.current.entries()).forEach(([stream, streamKind]) => {
      if (!kind || streamKind === kind) {
        stopHardwareStream(stream);
      }
    });
  };

  const stopMicrophoneBuffer = () => {
    try {
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== 'inactive') {
        recorder.stop();
      }
    } catch {}
    mediaRecorderRef.current = null;
    stopHardwareStream(microphoneStreamRef.current);
    microphoneStreamRef.current = null;
    stopTrackedHardwareStreams('microphone');
    recordingAudioChunksRef.current = [];
  };

  const startPetRecording = async () => {
    if (petSessionState === 'recording') return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setPetSessionState('error');
      setVoiceStatus('当前环境不支持录音');
      speakString('当前环境不支持录音。');
      return;
    }
    const conversationId = await requireActiveConversation();
    const active = v8Conversations.find((item) => String(item.id) === String(conversationId));
    if (isConversationRunning(active)) {
      setPetSessionState('listening_running');
      setVoiceStatus('当前会话运行中，不能发送新的语音');
      speakString('当前会话正在运行，稍后再发送语音。');
      return;
    }
    await window.v8CyberCore?.requestMediaAccess?.('microphone');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    registerHardwareStream('microphone', stream);
    microphoneStreamRef.current = stream;
    const mimeType = pickAudioRecorderMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recordingAudioChunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data?.size) {
        recordingAudioChunksRef.current.push(event.data);
      }
    };
    recorder.onerror = () => {
      setPetSessionState('error');
      setVoiceStatus('录音异常，请稍后重试');
    };
    recorder.onstop = () => {
      const type = recorder.mimeType || mimeType || 'audio/webm';
      const blob = new Blob(recordingAudioChunksRef.current, { type });
      recordingAudioChunksRef.current = [];
      stopHardwareStream(stream);
      if (microphoneStreamRef.current === stream) {
        microphoneStreamRef.current = null;
      }
      if (recordingStopResolverRef.current) {
        const resolve = recordingStopResolverRef.current;
        recordingStopResolverRef.current = null;
        resolve(blob);
      }
    };
    recorder.start(500);
    mediaRecorderRef.current = recorder;
    setPetSessionState('recording');
    setEmotion('listening');
    setVoiceStatus('录音中，再次点击发送到当前会话');
  };

  const stopPetRecording = async () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') return null;
    const blobPromise = new Promise<Blob>((resolve) => {
      recordingStopResolverRef.current = resolve;
    });
    recorder.stop();
    mediaRecorderRef.current = null;
    const blob = await blobPromise;
    return blob.size ? blob : null;
  };

  const captureFrameFromStream = async (stream: MediaStream, maxWidth = 1280) => {
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    try {
      await video.play().catch(() => undefined);
      await waitForVideoReady(video);
      return drawVideoFrameToCanvas(video, maxWidth);
    } finally {
      try {
        video.pause();
      } catch {}
      video.srcObject = null;
    }
  };

  const captureCameraCanvas = async () => {
    const activeVideo = videoRef.current;
    if (isWebcamActive && activeVideo && activeVideo.readyState >= 2 && activeVideo.videoWidth > 0) {
      return drawVideoFrameToCanvas(activeVideo, 720);
    }
    await window.v8CyberCore?.requestMediaAccess?.('camera');
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
    });
    try {
      return await captureFrameFromStream(stream, 720);
    } finally {
      stopHardwareStream(stream);
    }
  };

  const captureDesktopCanvas = async () => {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      throw new Error('当前环境不支持桌面截图');
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    try {
      return await captureFrameFromStream(stream, 1280);
    } finally {
      stopHardwareStream(stream);
    }
  };

  const composeDesktopWithCamera = async () => {
    const desktopCanvas = await captureDesktopCanvas();
    let cameraCanvas: HTMLCanvasElement | null = null;
    try {
      cameraCanvas = await captureCameraCanvas();
    } catch (error) {
      console.warn('[V8 Desktop Pet] camera snapshot skipped:', error);
    }
    if (!cameraCanvas) return desktopCanvas;

    const output = document.createElement('canvas');
    output.width = desktopCanvas.width;
    output.height = desktopCanvas.height;
    const ctx = output.getContext('2d');
    if (!ctx) throw new Error('无法合成截图');
    ctx.drawImage(desktopCanvas, 0, 0);

    const margin = Math.max(16, Math.round(output.width * 0.018));
    const pipWidth = Math.min(320, Math.max(180, Math.round(output.width * 0.24)));
    const pipHeight = Math.round(pipWidth * (cameraCanvas.height / Math.max(1, cameraCanvas.width)));
    const pipX = output.width - pipWidth - margin;
    const pipY = output.height - pipHeight - margin;
    ctx.fillStyle = 'rgba(15, 23, 42, 0.72)';
    ctx.fillRect(pipX - 4, pipY - 4, pipWidth + 8, pipHeight + 8);
    ctx.drawImage(cameraCanvas, pipX, pipY, pipWidth, pipHeight);
    return output;
  };

  const captureDesktopPetVisualAttachment = async () => {
    const capture = settingsRef.current.attachmentCapture;
    if (capture?.cameraEnabled !== true) return null;
    try {
      const canvas = capture.includeDesktopScreenshot
        ? await composeDesktopWithCamera()
        : await captureCameraCanvas();
      return await canvasToImageBlob(canvas, 'image/jpeg', 0.88);
    } catch (error) {
      console.warn('[V8 Desktop Pet] visual attachment skipped:', error);
      setVoiceStatus('画面截图失败，已继续发送语音');
      return null;
    }
  };

  const sendAudioBlobToActiveConversation = async (audioBlob: Blob) => {
    const client = v8ClientRef.current;
    if (!client?.getSession()) {
      throw new Error('尚未连接 V8OS Admin');
    }
    const conversationId = await requireActiveConversation();
    const active = v8Conversations.find((item) => String(item.id) === String(conversationId));
    if (isConversationRunning(active)) {
      setPetSessionState('listening_running');
      throw new Error('当前会话仍在运行，暂不能发送新的语音');
    }
    const extension = audioExtensionFromMime(audioBlob.type);
    const mimeType = audioBlob.type || `audio/${extension}`;
    const file = new File([audioBlob], `desktop-pet-voice-${Date.now()}.${extension}`, { type: mimeType });
    const uploadRes = await client.uploadFile(file, {
      conversationId,
      workspacePath: settingsRef.current.v8WorkspacePath || undefined,
    });
    const fileUrl = String(uploadRes.url || uploadRes.path || '').trim();
    if (!fileUrl) {
      throw new Error('V8OS 上传语音后没有返回可用链接');
    }
    const fileUrls = [fileUrl];
    const attachments: Array<Record<string, unknown>> = [{
      ...uploadRes,
      url: fileUrl,
      publicUrl: String(uploadRes.publicUrl || uploadRes.url || fileUrl),
      name: String(uploadRes.name || file.name),
      mimeType,
      size: audioBlob.size,
      mediaKind: 'audio',
      source: 'desktop_pet_voice',
    }];
    const visualBlob = await captureDesktopPetVisualAttachment();
    if (visualBlob) {
      try {
        const visualFile = new File([visualBlob], `desktop-pet-snapshot-${Date.now()}.jpg`, { type: 'image/jpeg' });
        const visualUploadRes = await client.uploadFile(visualFile, {
          conversationId,
          workspacePath: settingsRef.current.v8WorkspacePath || undefined,
        });
        const visualUrl = String(visualUploadRes.url || visualUploadRes.path || '').trim();
        if (visualUrl) {
          fileUrls.push(visualUrl);
          attachments.push({
            ...visualUploadRes,
            url: visualUrl,
            publicUrl: String(visualUploadRes.publicUrl || visualUploadRes.url || visualUrl),
            name: String(visualUploadRes.name || visualFile.name),
            mimeType: 'image/jpeg',
            size: visualBlob.size,
            mediaKind: 'image',
            source: 'desktop_pet_snapshot',
          });
        }
      } catch (error) {
        console.warn('[V8 Desktop Pet] visual attachment upload skipped:', error);
        setVoiceStatus('画面上传失败，已继续发送语音');
      }
    }
    setMessages((prev) => [
      ...prev,
      {
        id: `user-audio-${Date.now()}`,
        sender: 'user',
        text: attachments.length > 1 ? '已发送语音和画面。' : '已发送一段语音。',
        timestamp: nowLabel(),
      },
    ]);
    await client.submitMessage({
      conversationId,
      content: '',
      clientMessageId: `desktop-pet-voice-${Date.now()}`,
      fileUrls,
      attachments,
    });
    setPetSessionState('listening_running');
    setVoiceStatus('语音已发送，正在监听当前会话');
    setV8Status('V8OS 已接收桌宠语音');
    await syncV8Snapshot(conversationId).catch((error) => setV8Error(error?.message || 'V8OS 快照同步失败'));
  };

  const stopWebcamStream = (status = '光学追踪已关闭，鼠标接管') => {
    webcamIntentRef.current = false;
    const video = videoRef.current;
    const stream = video?.srcObject as MediaStream | null;
    stopHardwareStream(stream);
    if (video) {
      try {
        video.pause();
      } catch {}
      video.onloadedmetadata = null;
      video.oncanplay = null;
      video.srcObject = null;
    }
    stopTrackedHardwareStreams('camera');
    setIsWebcamActive(false);
    setWebcamStatus(status);
  };

  const cleanupLocalMedia = () => {
    stopMicrophoneBuffer();
    stopWebcamStream('本地媒体已释放');
    stopTrackedHardwareStreams();
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    if (fallbackAudioRef.current) {
      try {
        fallbackAudioRef.current.pause();
      } catch {}
      fallbackAudioRef.current = null;
    }
  };

  // Webcam stream capture controls (Sensory Vision)
  const toggleWebcam = async () => {
    if (isWebcamActive) {
      stopWebcamStream('光学追踪已关闭，鼠标接管');
    } else {
      if (!navigator.mediaDevices?.getUserMedia) {
        setWebcamStatus('当前运行环境不支持摄像头权限请求');
        return;
      }
      let stream: MediaStream | null = null;
      try {
        webcamIntentRef.current = true;
        setWebcamStatus('正在请求摄像头权限...');
        const permission = await window.v8CyberCore?.requestMediaAccess?.('camera');
        if (permission && permission.granted === false) {
          setWebcamStatus(`摄像头权限未授予：${String(permission.status || 'denied')}`);
          webcamIntentRef.current = false;
          return;
        }
        
        // Before capturing, check if the user clicked pet again to abort
        if (!webcamIntentRef.current) {
          setWebcamStatus('已中止摄像头启动');
          return;
        }

        stream = await navigator.mediaDevices.getUserMedia({ 
          video: { width: 320, height: 240, facingMode: 'user' } 
        });

        // Double check if session was stopped while waiting for getUserMedia
        if (!webcamIntentRef.current) {
           stream.getTracks().forEach(t => t.stop());
           setWebcamStatus('光学追踪已关闭');
           return;
        }

        registerHardwareStream('camera', stream);
        stream.getVideoTracks().forEach((track) => {
          track.onended = () => {
            stopWebcamStream('摄像头流已结束，已回退鼠标跟随');
          };
          track.onmute = () => {
            setWebcamStatus('摄像头无画面，暂由鼠标接管');
          };
          track.onunmute = () => {
            setWebcamStatus('摄像头画面恢复，正在扫描人影');
          };
        });
        setIsWebcamActive(true);
        setWebcamAllowed(true);
        setEmotion('curious');

        // Wait 100ms for React virtual DOM to render the <video> element and bind the ref
        await new Promise((resolve) => setTimeout(resolve, 100));

        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.onloadedmetadata = () => {
            void videoRef.current?.play?.();
            setWebcamStatus('摄像头已连接，正在等待画面帧');
          };
          videoRef.current.oncanplay = () => {
            setWebcamStatus('摄像头画面正常，正在扫描人影');
          };
        } else {
          stopHardwareStream(stream);
          setIsWebcamActive(false);
          setWebcamStatus('摄像头视图未就绪，已释放硬件流');
          return;
        }
      } catch (err) {
        console.error("Camera access blocked:", err);
        stopHardwareStream(stream);
        setIsWebcamActive(false);
        const message = err instanceof DOMException && err.name === 'NotAllowedError'
          ? '摄像头权限被拒绝，请在系统隐私设置或 Electron 权限中允许摄像头'
          : '摄像头未授权、无画面或被其他程序占用';
        setWebcamStatus(message);
      }
    }
  };

  // Triggered when clicking webcam stream to shut it down on unmount
  useEffect(() => {
    const handleShutdown = (data?: { requestId?: string }) => {
      setIsExiting(true);
      cleanupLocalMedia();
      void window.v8CyberCore?.reportStatus?.({ state: 'stopping', activeSessionId: v8ActiveConversationIdRef.current || null });
      if (shutdownReadyTimerRef.current) clearTimeout(shutdownReadyTimerRef.current);
      shutdownReadyTimerRef.current = setTimeout(() => {
        if (data?.requestId) void window.v8CyberCore?.shutdownReady?.(data.requestId);
      }, 520);
    };
    const removePrepareShutdown = window.v8CyberCore?.onPrepareShutdown?.(handleShutdown);
    const handleBeforeUnload = () => cleanupLocalMedia();
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      removePrepareShutdown?.();
      window.removeEventListener('beforeunload', handleBeforeUnload);
      if (shutdownReadyTimerRef.current) clearTimeout(shutdownReadyTimerRef.current);
      shutdownReadyTimerRef.current = null;
      cleanupLocalMedia();
    };
  }, []);

  useEffect(() => {
    if (!isWebcamActive) return;
    const id = setInterval(() => {
      const video = videoRef.current;
      const stream = video?.srcObject as MediaStream | null;
      const liveTrack = stream?.getVideoTracks().some((track) => track.readyState === 'live');
      if (!video || !stream || !liveTrack) {
        stopWebcamStream('摄像头流不可用，已回退鼠标跟随');
        return;
      }
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        setWebcamStatus('摄像头已开启但暂无有效画面帧');
      }
    }, 1600);
    return () => clearInterval(id);
  }, [isWebcamActive]);

  // Submit conversation through the V8OS Admin BFF.
  const handleChatSubmit = async (
    customMessage?: string,
    customFileUrls?: string[],
    customAttachments?: Record<string, unknown>[],
  ) => {
    const inputStr = customMessage !== undefined ? customMessage : chatInput;
    const hasCustomFiles = Array.isArray(customFileUrls) && customFileUrls.length > 0;
    if (!inputStr.trim() && !isWebcamActive && !hasCustomFiles) return;

    const userMsgText = inputStr || (isWebcamActive ? "Scanning vision core optical field..." : "");
    let capturedImageBase64 = "";

    if (isWebcamActive && videoRef.current && canvasRef.current) {
      setEmotion('scanning');
      const video = videoRef.current;
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      if (context) {
        canvas.width = video.videoWidth || 320;
        canvas.height = video.videoHeight || 240;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        capturedImageBase64 = canvas.toDataURL('image/jpeg', 0.85);
      }
    }

    const newUserMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text: userMsgText,
      timestamp: nowLabel(),
      image: capturedImageBase64 || undefined,
    };

    setMessages(prev => [...prev, newUserMessage]);
    if (!customMessage) setChatInput('');
    setIsLoading(true);
    setEmotion(capturedImageBase64 ? 'scanning' : 'curious');

    try {
      const client = v8ClientRef.current;
      if (!client) throw new Error('V8OS client unavailable');
      const conversationId = await requireActiveConversation();
      const active = v8Conversations.find((item) => String(item.id) === String(conversationId));
      if (isConversationRunning(active)) {
        throw new Error('当前会话仍在运行，暂不能发送新的消息');
      }
      setV8Status('已发送到 V8OS Supervisor');
      await client.submitMessage({
        conversationId,
        content: capturedImageBase64
          ? `${userMsgText}\n\n[Desktop pet optical frame captured locally; visual upload support is handled by V8OS when available.]`
          : userMsgText,
        clientMessageId: `cybercore-${Date.now()}`,
        fileUrls: customFileUrls?.length ? customFileUrls : undefined,
        attachments: customAttachments?.length ? customAttachments : undefined,
      });

      let latestText = '';
      for (let attempt = 0; attempt < 8; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 900 : 1600));
        latestText = await syncV8Snapshot(conversationId);
        if (latestText) break;
      }

      if (latestText) {
        speakAssistantReply(latestText, { audioAlreadyPlayed: v8LastSnapshotAudioPlayedRef.current });
        setV8Status('V8OS Supervisor 已回流');
      } else {
        setV8Status('V8OS 正在运行，稍后可在会话里查看回流');
        setMessages(prev => [
          ...prev,
          {
            id: `v8-wait-${Date.now()}`,
            sender: 'pet',
            text: 'V8OS 已接收任务，Supervisor 正在运行。你可以继续观察右键菜单里的会话流。',
            timestamp: nowLabel(),
            emotion: 'thinking',
          }
        ]);
      }
    } catch (err: any) {
      console.error(err);
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        sender: 'pet',
        text: `V8OS 连接故障：${err?.message || '无法连接 Admin BFF'}。请检查本机 V8OS 服务是否已启动。`,
        timestamp: nowLabel(),
        emotion: 'worried'
      };
      setMessages(prev => [...prev, errorMsg]);
      setV8Error(err?.message || 'V8OS 请求失败');
      setV8Status('V8OS 请求失败');
      setEmotion('worried');
    } finally {
      setIsLoading(false);
    }
  };
  // Master left-click handler on pet companion
  const handlePetClick = () => {
    void (async () => {
      if (petSessionState === 'recording') {
        try {
          setPetSessionState('sending_audio');
          setVoiceStatus('正在发送语音到当前会话');
          const audioBlob = await stopPetRecording();
          if (!audioBlob) {
            setPetSessionState('attached_idle');
            setVoiceStatus('没有录到有效语音');
            return;
          }
          await sendAudioBlobToActiveConversation(audioBlob);
        } catch (error: any) {
          setPetSessionState('error');
          setVoiceStatus(error?.message || '语音发送失败');
          setEmotion('worried');
        }
        return;
      }

      if (!v8ActiveConversationId) {
        setPetSessionState('idle_no_conversation');
        setVoiceStatus('请先在右键菜单选择会话');
        speakString('请先在右键菜单选择会话。');
        return;
      }

      const active = v8Conversations.find((item) => String(item.id) === String(v8ActiveConversationId));
      if (petSessionState === 'sending_audio' || petSessionState === 'listening_running' || isConversationRunning(active)) {
        setPetSessionState('listening_running');
        setVoiceStatus('当前会话运行中，不能发送新的语音');
        speakString('当前会话正在运行，稍后再发送语音。');
        return;
      }

      try {
        await startPetRecording();
      } catch (error: any) {
        setPetSessionState('error');
        setVoiceStatus(error?.message || '无法开始录音');
        setEmotion('worried');
      }
    })();
  };

  // --- V8OS SSE Stream Listener ---
  useEffect(() => {
    const conversationId = v8ActiveConversationId;
    if (!conversationId) return;
    const client = v8ClientRef.current;
    if (!client?.getSession()) return;

    const abortController = new AbortController();

    const connectStream = async () => {
      try {
        await client.streamRealtimeSession(
          conversationId,
          (eventName, rawPayload: any) => {
            if (eventName === 'ping') return;

            const activity = buildActivityFromRealtimeEvent(rawPayload);
            if (activity) {
              applyV8EventRules([activity]);
              if (isTerminalRunEvent(eventName, rawPayload)) {
                setPetSessionState('attached_idle');
                setVoiceStatus('当前会话已结束，可再次点击录音');
                void refreshV8Lists();
                void syncV8Snapshot(conversationId)
                  .then((latestText) => {
                    if (latestText) {
                      speakAssistantReply(latestText, { audioAlreadyPlayed: v8LastSnapshotAudioPlayedRef.current });
                    }
                  })
                  .catch((error) => setV8Error(error?.message || 'V8OS 快照同步失败'));
              } else if (petSessionStateRef.current !== 'recording' && petSessionStateRef.current !== 'sending_audio') {
                setPetSessionState('listening_running');
                setVoiceStatus('当前会话运行中，正在监听事件');
              }
            }

            const data = rawPayload?.data || {};
            // Auto-play audio if V8OS returned an audio buffer
            const directAudioUrl = findLatestAudioUrl(rawPayload);
            const audioData = directAudioUrl || data?.audio || rawPayload?.audio || data?.voiceData;
            if (audioData) {
              if (typeof audioData === 'string') {
                if (audioData.startsWith('data:audio') || audioData.startsWith('http')) {
                  if (directAudioUrl) v8LastAudioUrlRef.current = directAudioUrl;
                  fetch(audioData).then(res => res.blob()).then(blob => playAudioBlob(blob)).catch(console.error);
                } else if (audioData.length > 50) {
                  fetch(`data:audio/wav;base64,${audioData}`).then(res => res.blob()).then(blob => playAudioBlob(blob)).catch(console.error);
                }
              }
            }
          },
          abortController.signal
        );
      } catch (err: any) {
        if (!abortController.signal.aborted) {
          console.warn('V8 Realtime Stream Disconnected:', err);
        }
      }
    };

    connectStream();
    return () => {
      abortController.abort();
    };
  }, [v8ActiveConversationId, v8Session]);

  return (
    <div id="cyber-app-container" className="min-h-screen w-screen bg-transparent text-slate-100 overflow-hidden relative selection:bg-blue-600/30 font-sans">
      {/* Full screen Interactive Canvas Field */}
      <div 
        id="mockup-desktop-workspace" 
        className="w-full h-screen relative overflow-hidden flex items-center justify-center bg-transparent"
      >
        {/* Cyber Pet Core-01 Entity with direct property injects */}
        <CyberPet
          isExiting={isExiting}
          emotion={emotion}
          isTalking={isTalking}
          onPetClick={handlePetClick}
          audioVolume={audioVolume}
          settings={settings}
          onUpdateSettings={setSettings}
          messages={messages}
          setMessages={setMessages}
          isLoading={isLoading}
          chatInput={chatInput}
          setChatInput={setChatInput}
          handleChatSubmit={handleChatSubmit}
          isMuted={isMuted}
          setIsMuted={setIsMuted}
          isWebcamActive={isWebcamActive}
          toggleWebcam={toggleWebcam}
          webcamStatus={webcamStatus}
          voiceStatus={voiceStatus}
          onReleaseCamera={() => stopWebcamStream('手动释放光学流')}
          onTestSpeech={(text?: string) => speakString(text || '主人，中文电子女声测试完成。')}
          videoRef={videoRef}
          screenVideoRef={desktopVideoRef}
          canvasRef={canvasRef}
          metrics={metrics}
          v8Connection={{
            connected: Boolean(v8Session),
            loading: v8ListsLoading,
            status: v8Status,
            error: v8Error,
            conversations: v8Conversations.map((conversation) => ({
              id: String(conversation.id || ''),
              title: String(conversation.title || '未命名会话'),
              projectName: projectNameForConversation(conversation),
              workspacePath: conversation.workspacePath || null,
              running: isConversationRunning(conversation),
              status: conversation.status || null,
            })),
            projects: v8Projects,
            activeConversationId: v8ActiveConversationId,
            onSelectConversation: (id: string) => {
              void selectV8Conversation(id);
            },
            onStartListening: (id?: string) => {
              const conversationId = id || v8ActiveConversationId || v8ClientRef.current?.getActiveConversationId() || '';
              if (conversationId) {
                const active = v8Conversations.find((item) => String(item.id) === String(conversationId));
                void selectV8Conversation(conversationId, { listen: isConversationRunning(active) });
                return;
              }
              setPetSessionState('idle_no_conversation');
              setVoiceStatus('请先在右键菜单选择会话');
              speakString('请先在右键菜单选择会话。');
            },
            onConnect: connectV8,
            onDisconnect: disconnectV8,
            onRefresh: connectV8,
            onOpenAdmin: () => window.v8CyberCore?.openAdmin(),
            onQuit: () => window.v8CyberCore?.quit(),
          }}
        />

        {/* Laser Scanning overlay when active */}
        {emotion === 'scanning' && (
          <div className="absolute inset-x-0 top-0 h-1 bg-red-500/20 shadow-[0_0_15px_rgba(239,68,68,0.7)] animate-[bounce_1.8s_infinite] pointer-events-none" />
        )}
      </div>

    </div>
  );
}
