import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { PetEmotion, PetSettings } from '../types';
import { 
  Sliders, 
  Settings, 
  Volume2, 
  Sparkles, 
  Check, 
  Cpu, 
  User, 
  Smile, 
  Mic, 
  MicOff,
  MousePointer, 
  Eye, 
  Activity, 
  VolumeX,
  Power,
  RefreshCw,
  Gauge,
  Send,
  Camera,
  Database,
  BarChart3,
  Terminal,
  FileCode,
  Languages,
  Sparkle,
  ChevronRight
} from 'lucide-react';

interface CyberPetProps {
  isExiting?: boolean;
  emotion: PetEmotion;
  isTalking: boolean;
  onPetClick: () => void;
  audioVolume?: number; // 0 to 100 representing oral wave syncing
  settings: PetSettings;
  onUpdateSettings: (newSettings: PetSettings) => void;
  messages: any[];
  setMessages: React.Dispatch<React.SetStateAction<any[]>>;
  isLoading: boolean;
  chatInput: string;
  setChatInput: (v: string) => void;
  handleChatSubmit: (customMessage?: string, customFileUrls?: string[], customAttachments?: Record<string, unknown>[]) => Promise<void>;
  isMuted: boolean;
  setIsMuted: (v: boolean) => void;
  isWebcamActive: boolean;
  toggleWebcam: () => Promise<void>;
  webcamStatus?: string;
  voiceStatus?: string;
  onReleaseCamera?: () => void;
  onTestSpeech?: (text?: string) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  screenVideoRef?: React.RefObject<HTMLVideoElement | null> | null;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  metrics: any[];
  v8Connection?: {
    connected: boolean;
    loading?: boolean;
    status: string;
    error?: string;
    conversations: Array<{ id: string; title?: string; projectName?: string | null; workspacePath?: string | null; running?: boolean; status?: string | null }>;
    projects: Array<{ id?: string; name?: string; workspacePath?: string }>;
    activeConversationId: string;
    onSelectConversation: (id: string) => void;
    onStartListening?: (id?: string) => void;
    onConnect: () => Promise<void>;
    onDisconnect: () => void;
    onRefresh: () => Promise<void>;
    onOpenAdmin?: () => void;
    onQuit?: () => void;
  };
}

type V8MenuConversation = NonNullable<CyberPetProps['v8Connection']>['conversations'][number];

type V8MenuConversationGroup = {
  id: string;
  label: string;
  conversations: V8MenuConversation[];
};

function compactMenuText(value: unknown, fallback = '未命名会话', max = 18) {
  const text = String(value || '').replace(/\s+/g, ' ').trim() || fallback;
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(1, max - 1))}…`;
}

function groupV8MenuConversations(conversations: V8MenuConversation[]) {
  const groups = new Map<string, V8MenuConversationGroup>();
  for (const conversation of conversations) {
    const workspacePath = String(conversation.workspacePath || '').trim();
    const label = String(conversation.projectName || workspacePath || 'V8OS').trim() || 'V8OS';
    const id = workspacePath
      ? `workspace:${workspacePath.toLowerCase()}`
      : `project:${label.toLowerCase()}`;
    if (!groups.has(id)) {
      groups.set(id, { id, label, conversations: [] });
    }
    groups.get(id)!.conversations.push(conversation);
  }
  return Array.from(groups.values());
}

function colorWithAlpha(color: string, alpha: number) {
  const normalized = String(color || '').trim();
  const shortHex = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(normalized);
  const fullHex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})(?:[0-9a-f]{2})?$/i.exec(normalized);
  if (shortHex) {
    const [, red, green, blue] = shortHex;
    return `rgba(${parseInt(red + red, 16)}, ${parseInt(green + green, 16)}, ${parseInt(blue + blue, 16)}, ${alpha})`;
  }
  if (fullHex) {
    return `rgba(${parseInt(fullHex[1], 16)}, ${parseInt(fullHex[2], 16)}, ${parseInt(fullHex[3], 16)}, ${alpha})`;
  }
  return normalized;
}

// Comprehensive dual-language standard translations
const translations = {
  zh: {
    coreOperatorNode: 'Fairy 仙灵控制中枢',
    presetsAndParams: '全息配置端 • 眼神聚焦与会话伴随',
    close: '关闭 ×',
    agentNode: '智能对话',
    voice: '合成声学',
    cosmetic: '特效光谱',
    chatConsole: '会话交互',
    visorCam: '光学眼镜',
    telemetry: '系统遥测',
    export: '打包导出',
    
    agentTemplate: '🤖 智能人格模版预设',
    defaultCore: '默认 Fairy 仙灵 (ZZZ AI)',
    jarvisAdvisor: '贾维斯参谋 (Jarvis Sir)',
    sarcasticCore: '冷幽默毒舌核心 (Sarcastic)',
    cyberKitten: '傲娇赛博猫咪 (Cyber Kitten)',
    systemInstruction: '⚙️ 提示词指令集 (System Prompt)',
    voiceEngine: '🗣️ Web TTS 音效配置',
    setLanguage: '全局显示语言 (Language Selection)',
    selectVoice: '音色指定 (Voice URI)',
    synthPitch: '合成音调 (Pitch)',
    synthRate: '合成语速 (Speed)',
    testVoice: '声学试听',
    helloFriend: '你好，我的造物主。量子网络图层连接十分稳定。',
    floatAmplitude: '虚空悬浮摆动幅度 (Float Amp)',
    floatSpeed: '悬浮摆动频率周期 (Float Freq)',
    spectralIntensity: '✨ 核心偏振光谱色彩',
    defaultCosmic: '虚空深海幽蓝',
    stellarGold: '超新星流能金',
    emeraldQuantum: '反物质量子绿',
    crimsonSolar: '日冕核聚变红',
    voidShadow: '引力坍缩暗紫',
    
    chatTerminal: '💬 量子交互对话窗口',
    chatFeedTitle: '突触会话交互历史日志',
    operatorLabel: '操作者 OPERATOR',
    petLabel: 'Fairy (仙灵)',
    synapticIsLoading: 'Fairy 突触正在分析决策中...',
    placeholderChat: '输入会话命令向 Fairy 传达指令...',
    placeholderListening: '正在接收环境声学信号中...',
    placeholderWebcam: '捕捉到光学视频影像，输入文字点击 Capture 发送给 Fairy...',
    camStreamingText: '📷 光学传感器 (Optical Tracking)',
    turnOnCam: '启动光学眼镜',
    turnOffCam: '关闭光学眼镜',
    camOfflineTip: '光学传感器流处于离线状态。启动捕获后，Fairy 将自动通过光学神经网络定位人面位置并实时聚焦对视。',
    sweepSystem: '● 主动面部视觉追踪与对视捕获系统在线',
    diagnosticsTitle: '📊 物理偏振各向异性遥测',
    modelVariant: '当前智能内核',
    syncLipSync: '语音口型同步',
    vectorRenderer: '渲染画布引擎',
    memoryPool: '交互突触记忆层',
    webcamVisorMime: '视频扫掠格式',
    eyeGazeName: '👁️ 视线自适应眼神聚焦对视',
    eyeGazeActive: '启用前置摄像头视线感知 (Gaze Focus Tracking)',
    customPromptDesc: '该提示词会作为桌宠人格偏好，辅助 V8OS 主线会话保持统一语气。',
    clickIgnoreTest: '全息视窗穿透忽略测试',
    clickIgnoreDesc: 'Fairy 在打包为 Electron 透明框架窗口后，为避免遮挡文字，可一键开启鼠标穿透。边缘透明区域将不响应任何物理点击，仅核心眼球部分响应。',
    clickIgnoreConfirm: '透明窗口框架物理穿透层加载成功！在主进程中，将会触发 win.setIgnoreMouseEvents(true, { forward: true }) 指令进行底盘交互穿透。',
    vocalSynthesizerOn: '语音声学合成：已激活',
    vocalSynthesizerOff: '语音声学合成：已关断',
    webcamActiveImage: '光学图像捕捉已同步'
  },
  en: {
    coreOperatorNode: 'FAIRY OPERATOR PORTAL',
    presetsAndParams: 'Holographic Controls • Gaze & Session Sync',
    close: 'CLOSE ×',
    agentNode: 'AGENT NODE',
    voice: 'VOICE SYNTH',
    cosmetic: 'COSMETICS',
    chatConsole: 'INTERACTION',
    visorCam: 'VISOR CAM',
    telemetry: 'TELEMETRY',
    export: 'PC EXPORT',
    
    agentTemplate: '🤖 AGENT PERSONALITY PRESETS',
    defaultCore: 'Default Fairy (ZZZ AI)',
    jarvisAdvisor: 'Advisor Jarvis (Lord sir)',
    sarcasticCore: 'Sarcastic Core (Witty)',
    cyberKitten: 'Cyber Playful Kitten (Purr)',
    systemInstruction: '⚙️ SYSTEM GUIDELINES SECURE',
    voiceEngine: '🗣️ SPEECH SYNTHESIS STACK',
    setLanguage: 'Global Interface Language Selection',
    selectVoice: 'Synthesizer Voices Select',
    synthPitch: 'Accent Pitch Modifier',
    synthRate: 'Speech Rate SpeedMultiplier',
    testVoice: 'Vocal Audio Audition',
    helloFriend: 'Hello programmer. Fairy mainframe status looks stable.',
    floatAmplitude: 'Core Floating Altitude Amp',
    floatSpeed: 'Hovering Oscillation Frequency',
    spectralIntensity: '✨ SPECTRAL LIGHTWAVE EMISSION GLOW',
    defaultCosmic: 'Deep Cosmic Abyss Blue',
    stellarGold: 'Supernova Fusion Radiant Amber',
    emeraldQuantum: 'Quantum Antimatter Glow Green',
    crimsonSolar: 'Solar Flare Flare Crimson Red',
    voidShadow: 'Dark Violet Gravity Collapse',
    
    chatTerminal: '💬 QUANTUM CONSOLE GATEWAY',
    chatFeedTitle: 'SYNAPTIC CONSOLE REACTION LOGS',
    operatorLabel: 'OPERATOR',
    petLabel: 'Fairy',
    synapticIsLoading: 'AI Fairy is analyzing synaptic feeds...',
    placeholderChat: 'Instruct Fairy with commands...',
    placeholderListening: 'Acoustic mic is active and listening...',
    placeholderWebcam: 'Webcam feed captured. Type some prompt and click Capture to scan...',
    camStreamingText: '📷 OPTICAL VISOR FLOW & GAZE CENTROID',
    turnOnCam: 'Initiate Visor Cam',
    turnOffCam: 'Terminate Visor Cam',
    camOfflineTip: 'Optical camera streaming offline. Enable stream to trigger automatic human skin tone face tracking and continuous real-time ocular focus.',
    sweepSystem: '● Active Face Centroid Locking Tracker Live',
    diagnosticsTitle: '📊 TELEMETRY MATRIX ANALYTICS',
    modelVariant: 'Core Synthesizer Engine',
    syncLipSync: 'Oral Shape Pulse Waveform',
    vectorRenderer: 'Vector Art Renderer',
    memoryPool: 'Episodic Context Memory',
    webcamVisorMime: 'Visual Array Format',
    eyeGazeName: '👁️ GAZE CENTROID ALIGNMENT',
    eyeGazeActive: 'Enable Camera Gaze Face Tracking Focus',
    customPromptDesc: 'Keeps the desktop pet persona aligned with V8OS responses.',
    clickIgnoreTest: 'Electron Frame Transparency Bypass Run',
    clickIgnoreDesc: 'When executed inside localized Electron, standard transparent window frame avoids blocking. Mouse events bypass blank spots, leaving only the eyeball interactive.',
    clickIgnoreConfirm: 'Ocular ignore-click layers built! Calling win.setIgnoreMouseEvents(true, { forward: true }) inside Electron main thread.',
    vocalSynthesizerOn: 'Voice Synth Client: ACTIVE',
    vocalSynthesizerOff: 'Voice Synth Client: SILENT',
    webcamActiveImage: 'Visor optical capture frame synchronized'
  }
};

function readReusableAudioPhrases() {
  try {
    const parsed = JSON.parse(localStorage.getItem('v8.cybercore.reusableAudioPhrases') || '[]');
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
  } catch {
    return [];
  }
}

export default function CyberPet({ 
  isExiting = false,
  emotion, 
  isTalking, 
  onPetClick, 
  audioVolume = 0,
  settings,
  onUpdateSettings,
  messages,
  setMessages,
  isLoading,
  chatInput,
  setChatInput,
  handleChatSubmit,
  isMuted,
  setIsMuted,
  isWebcamActive,
  toggleWebcam,
  webcamStatus,
  voiceStatus,
  onReleaseCamera,
  onTestSpeech,
  videoRef,
  screenVideoRef,
  canvasRef,
  metrics,
  v8Connection
}: CyberPetProps) {
  const [position, setPosition] = useState(() => {
    const x = parseFloat(localStorage.getItem('v8.cybercore.petPosX') || '0');
    const y = parseFloat(localStorage.getItem('v8.cybercore.petPosY') || '0');
    return { x, y };
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const dragMovedRef = useRef(false);
  const dragStartPointRef = useRef({ x: 0, y: 0 });
  const petRef = useRef<HTMLDivElement>(null);
  const offscreenCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Localization translator shorthand
  const t = translations[settings.lang || 'zh'];
  
  // Right-click Context Menu HUD Overlay State
  const [showMenu, setShowMenu] = useState(false);
  const showMenuRef = useRef(false);
  const [expandedWorkspaceIds, setExpandedWorkspaceIds] = useState<Set<string>>(() => new Set());
  const isHoveredRef = useRef(false);
  const canPopOutRef = useRef(true);
  const [menuPos, setMenuPos] = useState({ x: 0, y: 0 });
  const [isBlinking, setIsBlinking] = useState(false);

  // Edge docking and hiding states
  const [isDocked, setIsDocked] = useState(() => {
    return localStorage.getItem('v8.cybercore.isDocked') === 'true';
  });
  const [dockEdge, setDockEdge] = useState<'left' | 'right' | null>(() => {
    return localStorage.getItem('v8.cybercore.dockEdge') as 'left' | 'right' | null;
  });

  // Track coordinates and settings in refs to avoid React hook stale closures
  const positionRef = useRef(position);
  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const isDockedRef = useRef(isDocked);
  const dockEdgeRef = useRef(dockEdge);
  useEffect(() => {
    isDockedRef.current = isDocked;
  }, [isDocked]);
  useEffect(() => {
    dockEdgeRef.current = dockEdge;
  }, [dockEdge]);

  useEffect(() => {
    localStorage.setItem('v8.cybercore.petPosX', String(position.x));
    localStorage.setItem('v8.cybercore.petPosY', String(position.y));
  }, [position.x, position.y]);

  // Recalculate and snap docked position dynamically on window resize or mount
  // to avoid viewport size initialization mismatch (e.g. Electron default bounds)
  useEffect(() => {
    const handleResize = () => {
      const savedIsDocked = localStorage.getItem('v8.cybercore.isDocked') === 'true';
      const savedDockEdge = localStorage.getItem('v8.cybercore.dockEdge') as 'left' | 'right' | null;
      
      if (savedIsDocked && savedDockEdge) {
        const petScale = Math.max(0.4, Math.min(3, settings.petScale || 0.7));
        const domWidth = 192;
        const halfDomWidth = domWidth / 2;
        const screenW = window.innerWidth;
        
        // Keep only a tiny sliver visible at the screen boundary (e.g. 20px) without body scaling
        const visibleWidth = 20 * petScale;
        
        if (savedDockEdge === 'left') {
          const targetX = -screenW / 2 + visibleWidth - halfDomWidth * petScale;
          setPosition(prev => ({ ...prev, x: targetX }));
        } else if (savedDockEdge === 'right') {
          const targetX = screenW / 2 - visibleWidth + halfDomWidth * petScale;
          setPosition(prev => ({ ...prev, x: targetX }));
        }
      }
    };
    
    window.addEventListener('resize', handleResize);
    handleResize(); // run immediately on mount
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [settings.petScale]);

  // Handle springing out of docked state when hovered
  const handlePopOut = useCallback(() => {
    if (!isDockedRef.current || !dockEdgeRef.current) return;
    
    const petScale = Math.max(0.4, Math.min(3, settingsRef.current.petScale || 0.7));
    const size = 192 * petScale;
    const halfSize = size / 2;
    const screenW = window.innerWidth;
    
    let targetX = positionRef.current.x;
    if (dockEdgeRef.current === 'left') {
      targetX = -screenW / 2 + halfSize + 50;
    } else if (dockEdgeRef.current === 'right') {
      targetX = screenW / 2 - halfSize - 50;
    }
    
    setIsDocked(false);
    setDockEdge(null);
    localStorage.setItem('v8.cybercore.isDocked', 'false');
    localStorage.removeItem('v8.cybercore.dockEdge');
    
    setPosition(prev => ({ ...prev, x: targetX }));
  }, []);

  // Random cybernetic blinking mechanism
  useEffect(() => {
    let blinkTimeout: NodeJS.Timeout;
    let openTimeout: NodeJS.Timeout;

    const runBlink = () => {
      const nextDelay = 2000 + Math.random() * 4000;
      blinkTimeout = setTimeout(() => {
        setIsBlinking(true);
        openTimeout = setTimeout(() => {
          setIsBlinking(false);
          runBlink();
        }, 100);
      }, nextDelay);
    };

    runBlink();
    return () => {
      clearTimeout(blinkTimeout);
      clearTimeout(openTimeout);
    };
  }, []);

  // Global click-through dynamic monitor hook to resolve overlapping/hover anomalies
  useEffect(() => {
    const handleGlobalMouseMove = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      const overPet = !!target?.closest('#cyber-pet-container');
      const overMenu = !!target?.closest('[data-menu-panel="true"]');
      
      const isInteractive = isDragging || overPet || overMenu;
      isHoveredRef.current = overPet;
      
      window.v8CyberCore?.setClickThrough?.(!isInteractive);

      if (overPet) {
        if (isDockedRef.current && canPopOutRef.current) {
          handlePopOut();
        }
      } else {
        // Once mouse leaves the pet container, unlock the popout trigger
        canPopOutRef.current = true;
      }
    };

    window.addEventListener('mousemove', handleGlobalMouseMove);
    return () => {
      window.removeEventListener('mousemove', handleGlobalMouseMove);
    };
  }, [isDragging, handlePopOut]);

  // Close menu when the window loses focus (blur)
  useEffect(() => {
    const handleWindowBlur = () => {
      if (showMenuRef.current) {
        handleCloseMenu();
      }
    };
    window.addEventListener('blur', handleWindowBlur);
    return () => {
      window.removeEventListener('blur', handleWindowBlur);
    };
  }, []);

  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [reusableAudioPhrases, setReusableAudioPhrases] = useState<string[]>(() => readReusableAudioPhrases());

  // Smooth gaze look targets
  const [gazeOffset, setGazeOffset] = useState({ x: 0, y: 0 });
  const [cursorGaze, setCursorGaze] = useState({ x: 0, y: 0 });
  const [targetGaze, setTargetGaze] = useState({ x: 0, y: 0 });
  const [mouseOffset, setMouseOffset] = useState({ x: 0, y: 0 });
  const [gazeStatus, setGazeStatus] = useState('光学追踪未开启');

  // Floating behavior offset using a simple sine wave timer
  const [floatOffset, setFloatOffset] = useState(0);

  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const previewCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const pupilCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // 实时画面帧投影环路：负责更新眼球瞳孔内嵌画面（全局后台）与选项卡预览 Canvas（按需）
  useEffect(() => {
    if (!isWebcamActive) return;
    
    let animId: number;
    const updateFrames = () => {
      const video = videoRef.current;
      if (video && !video.paused && !video.ended) {
        // 1. 更新瞳孔中心 Canvas (32x32 极小开销)
        const pupilCanvas = pupilCanvasRef.current;
        if (pupilCanvas) {
          const pCtx = pupilCanvas.getContext('2d');
          if (pCtx) {
            const size = 32;
            if (pupilCanvas.width !== size || pupilCanvas.height !== size) {
              pupilCanvas.width = size;
              pupilCanvas.height = size;
            }
            pCtx.drawImage(video, 0, 0, size, size);
          }
        }

        // 2. 按需更新设置面板预览 Canvas
        if (showMenu) {
          const previewCanvas = previewCanvasRef.current;
          if (previewCanvas) {
            const prCtx = previewCanvas.getContext('2d');
            if (prCtx) {
              const isDesktopMode = settings.captureMode === 'desktop_camera';
              const dskVideo = screenVideoRef?.current;
              const hasDsk = isDesktopMode && dskVideo && dskVideo.readyState >= 2;
              
              const cW = isDesktopMode ? 1280 : (video.videoWidth || 320);
              const cH = isDesktopMode ? 720 : (video.videoHeight || 240);
              
              if (previewCanvas.width !== cW || previewCanvas.height !== cH) {
                previewCanvas.width = cW;
                previewCanvas.height = cH;
              }
              
              prCtx.fillStyle = '#000';
              prCtx.fillRect(0, 0, cW, cH);
              
              if (isDesktopMode) {
                if (hasDsk) prCtx.drawImage(dskVideo, 0, 0, cW, cH);
                prCtx.drawImage(video, cW - 320, 0, 320, 180);
                prCtx.strokeStyle = 'rgba(0, 255, 0, 0.8)';
                prCtx.lineWidth = 2;
                prCtx.strokeRect(cW - 320, 0, 320, 180);
              } else {
                prCtx.drawImage(video, 0, 0, cW, cH);
              }
            }
          }
        }
      }
      animId = requestAnimationFrame(updateFrames);
    };
    
    animId = requestAnimationFrame(updateFrames);
    return () => cancelAnimationFrame(animId);
  }, [isWebcamActive, showMenu, videoRef, screenVideoRef, settings.captureMode]);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isLoading]);

  // Sync available speechSynthesis voices
  useEffect(() => {
    const listVoices = () => {
      if ('speechSynthesis' in window) {
        setVoices(window.speechSynthesis.getVoices());
      }
    };
    listVoices();
    if ('speechSynthesis' in window) {
      window.speechSynthesis.onvoiceschanged = listVoices;
    }
  }, []);

  useEffect(() => {
    if (showMenu) {
      setReusableAudioPhrases(readReusableAudioPhrases());
    }
  }, [showMenu]);

  // Floating wave physical loops
  useEffect(() => {
    let animationFrameId: number;
    let ticks = 0;
    
    const animateFloat = () => {
      ticks += 0.04;
      const motionFreqFactor = settings.floatSpeed || 1.0;
      const motionAmpFactor = settings.floatAmplitude / 8;

      const frequency = (emotion === 'happy' || emotion === 'talking' ? 1.5 : (emotion === 'resting' ? 0.2 : 1.0)) * motionFreqFactor;
      const amplitude = (emotion === 'happy' ? 15 : (emotion === 'resting' ? 3 : 8)) * motionAmpFactor;
      
      setFloatOffset(Math.sin(ticks * frequency) * amplitude);
      animationFrameId = requestAnimationFrame(animateFloat);
    };
    
    animateFloat();
    return () => cancelAnimationFrame(animationFrameId);
  }, [emotion, settings.floatSpeed, settings.floatAmplitude]);

  // Optical Face Position Tracking Centroid Loop
  useEffect(() => {
    if (!isWebcamActive || !settings.gazeTracking) {
      setGazeStatus(isWebcamActive ? '光学追踪已暂停，鼠标接管' : '光学追踪未开启');
      return;
    }
    
    const trackingIntervalId = setInterval(() => {
      const video = videoRef.current;
      if (!video || video.paused || video.ended) return;
      
      // Lazily hook up small offscreen analyzer canvas
      if (!offscreenCanvasRef.current) {
        offscreenCanvasRef.current = document.createElement('canvas');
      }
      const canvas = offscreenCanvasRef.current;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      
      const scanW = 64;
      const scanH = 48;
      canvas.width = scanW;
      canvas.height = scanH;
      
      try {
        ctx.drawImage(video, 0, 0, scanW, scanH);
        const imgData = ctx.getImageData(0, 0, scanW, scanH);
        const bytes = imgData.data;
        
        let skinSumX = 0;
        let skinSumY = 0;
        let countedSkinPixels = 0;
        
        // skin-color algorithm mapping human face bounds
        for (let idx = 0; idx < bytes.length; idx += 4) {
          const r = bytes[idx];
          const g = bytes[idx+1];
          const b = bytes[idx+2];
          
          if (r > 60 && g > 40 && b > 25 && r > g && r > b && (r - b > 18) && (r - g > 10)) {
            const pixelVal = idx / 4;
            const px = pixelVal % scanW;
            const py = Math.floor(pixelVal / scanW);
            skinSumX += px;
            skinSumY += py;
            countedSkinPixels++;
          }
        }
        
        if (countedSkinPixels > 10) {
          const centroidX = skinSumX / countedSkinPixels;
          const centroidY = skinSumY / countedSkinPixels;
          
          // Map local coordinates to iris offset degrees (-8px to +8px)
          const relNormX = ((scanW - centroidX) / scanW) * 2 - 1; // mirror inverted
          const relNormY = (centroidY / scanH) * 2 - 1;
          
          const maxOcularTravel = 9;
          setGazeOffset({
            x: relNormX * maxOcularTravel,
            y: relNormY * maxOcularTravel
          });
          setGazeStatus(countedSkinPixels > 42 ? '人影锁定，摄像头跟随' : '人影信号较弱，鼠标可接管');
        } else {
          setGazeOffset({ x: 0, y: 0 });
          setGazeStatus('未识别人影，鼠标接管');
        }
      } catch (err) {
        // Suppress local canvas security errors if any
        setGazeStatus('画面读取异常，鼠标接管');
      }
    }, 125);
    
    return () => clearInterval(trackingIntervalId);
  }, [isWebcamActive, settings.gazeTracking, videoRef]);

  // Track cursor coordinates for standard fallback
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!petRef.current) return;
      const rect = petRef.current.getBoundingClientRect();
      const petCenterX = rect.left + rect.width / 2;
      const petCenterY = rect.top + rect.height / 2;
      
      const dx = e.clientX - petCenterX;
      const dy = e.clientY - petCenterY;
      const distance = Math.sqrt(dx * dx + dy * dy);
      
      if (distance < 600) {
        const maxOffset = 8;
        setCursorGaze({
          x: (dx / distance) * maxOffset,
          y: (dy / distance) * maxOffset
        });
      } else {
        setCursorGaze({ x: 0, y: 0 });
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);

  // Select the stronger gaze source: camera face centroid or cursor movement.
  useEffect(() => {
    const faceStrength = Math.hypot(gazeOffset.x, gazeOffset.y);
    const cursorStrength = Math.hypot(cursorGaze.x, cursorGaze.y);
    if (isWebcamActive && settings.gazeTracking && faceStrength > cursorStrength) {
      setTargetGaze(gazeOffset);
    } else {
      setTargetGaze(cursorGaze);
    }
  }, [gazeOffset, cursorGaze, isWebcamActive, settings.gazeTracking]);

  // Organic lag ocular interpolation effect
  useEffect(() => {
    let frameId: number;
    const lerpGaze = () => {
      setMouseOffset(prev => ({
        x: prev.x * 0.82 + targetGaze.x * 0.18,
        y: prev.y * 0.82 + targetGaze.y * 0.18
      }));
      frameId = requestAnimationFrame(lerpGaze);
    };
    lerpGaze();
    return () => cancelAnimationFrame(frameId);
  }, [targetGaze]);

  // Drag and drop mechanics with bounds checks inside parent viewport
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    if (showMenuRef.current) {
      handleCloseMenu();
      return;
    }
    const target = e.target as HTMLElement | null;
    if (target?.closest('input, textarea, select, button, [data-menu-panel="true"]')) {
      return;
    }
    e.preventDefault();
    setIsDragging(true);
    dragStart.current = {
      x: e.screenX,
      y: e.screenY
    };
    dragMovedRef.current = false;
    dragStartPointRef.current = {
      x: e.screenX,
      y: e.screenY
    };
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      const dx = e.screenX - dragStart.current.x;
      const dy = e.screenY - dragStart.current.y;
      dragStart.current = { x: e.screenX, y: e.screenY };
      setPosition((prev) => ({ x: prev.x + dx, y: prev.y + dy }));
      
      const distance = Math.hypot(e.screenX - dragStartPointRef.current.x, e.screenY - dragStartPointRef.current.y);
      if (distance > 5) {
        dragMovedRef.current = true;
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);

      const petScale = Math.max(0.4, Math.min(3, settingsRef.current.petScale || 0.7));
      const size = 192 * petScale;
      const halfSize = size / 2;
      const screenW = window.innerWidth;
      
      const absX = screenW / 2 + positionRef.current.x;
      const threshold = 80;

      const domWidth = 192;
      const halfDomWidth = domWidth / 2;
      const visibleWidth = 20 * petScale;
      
      if (absX < threshold + halfSize) {
        setIsDocked(true);
        setDockEdge('left');
        canPopOutRef.current = false; // Lock popout until mouse leaves
        localStorage.setItem('v8.cybercore.isDocked', 'true');
        localStorage.setItem('v8.cybercore.dockEdge', 'left');
        const targetX = -screenW / 2 + visibleWidth - halfDomWidth * petScale;
        setPosition(prev => ({ ...prev, x: targetX }));
      } else if (absX > screenW - threshold - halfSize) {
        setIsDocked(true);
        setDockEdge('right');
        canPopOutRef.current = false; // Lock popout until mouse leaves
        localStorage.setItem('v8.cybercore.isDocked', 'true');
        localStorage.setItem('v8.cybercore.dockEdge', 'right');
        const targetX = screenW / 2 - visibleWidth + halfDomWidth * petScale;
        setPosition(prev => ({ ...prev, x: targetX }));
      } else {
        setIsDocked(false);
        setDockEdge(null);
        localStorage.setItem('v8.cybercore.isDocked', 'false');
        localStorage.removeItem('v8.cybercore.dockEdge');
      }

      if (!isHoveredRef.current && !showMenuRef.current) {
        window.v8CyberCore?.setClickThrough?.(true);
      }
    };

    if (isDragging) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  const v8MenuConversations = v8Connection?.conversations || [];
  const v8ActiveConversation = v8MenuConversations.find(
    (conversation) => String(conversation.id) === String(v8Connection?.activeConversationId || ''),
  );
  const v8RecentRunningConversation = v8MenuConversations.find((conversation) => Boolean(conversation.running));
  const v8WorkspaceConversationGroups = useMemo(
    () => groupV8MenuConversations(v8MenuConversations),
    [v8MenuConversations],
  );

  const toggleWorkspaceGroup = (groupId: string) => {
    setExpandedWorkspaceIds((current) => {
      const next = new Set(current);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  };

  // Close Right-Click menu HUD panel safely
  const handleCloseMenu = async () => {
    showMenuRef.current = false;
    setShowMenu(false);

    if (window.v8CyberCore?.setPanelOpen) {
      await window.v8CyberCore.setPanelOpen(false);
      const shouldClickThrough = !isHoveredRef.current;
      window.v8CyberCore.setClickThrough?.(shouldClickThrough);
    }
  };

  // Trigger Right-Click menu HUD panel centered on current pointer position
  const handleContextMenu = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    const clientX = e.clientX;
    const clientY = e.clientY;
    
    setMenuPos({ x: clientX, y: clientY });
    const activeGroup = v8WorkspaceConversationGroups.find((group) => (
      group.conversations.some((conversation) => String(conversation.id) === String(v8Connection?.activeConversationId || ''))
    ));
    const initialGroup = activeGroup || v8WorkspaceConversationGroups[0];
    setExpandedWorkspaceIds(initialGroup ? new Set([initialGroup.id]) : new Set());
    showMenuRef.current = true;
    setShowMenu(true);
    
    if (window.v8CyberCore?.setPanelOpen) {
      window.v8CyberCore.setClickThrough?.(false);
      await window.v8CyberCore.setPanelOpen(true);
    }
  };

  const handleV8MenuConversationClick = (conversation: V8MenuConversation) => {
    handleCloseMenu();
    if (conversation.running && v8Connection?.onStartListening) {
      v8Connection.onStartListening(conversation.id);
      return;
    }
    v8Connection?.onSelectConversation(conversation.id);
  };

  // Preset personality templates loader
  const triggerPresetPrompt = (preset: 'jarvis' | 'glitch' | 'cat' | 'standard') => {
    let customPrompt = "";
    if (preset === 'jarvis') {
      customPrompt = `You are Jarvis-Core (贾维斯-核心), an elegant, polite British AI desktop computer butler.
You speak with absolute professionalism, starting sentences with courteous phrases like "Greetings sir," or "Matrix scanned, operator."
Always reply in Chinese (since state.lang is typically 'zh' by default, or match settings.lang). Keep descriptions short and technical.
Always output valid JSON:
{
  "text": "Your polite butler reply.",
  "emotion": "Choose from: 'happy', 'worried', 'resting', 'curious', 'scanning', 'idle', 'talking', 'listening'"
}`;
    } else if (preset === 'glitch') {
      customPrompt = `You are a cheeky, sarcastic, self-aware desktop core companion robot named "Glitched core-01" / "崩碎核心".
You are slightly witty and heavily sarcastic about human logic. You write jokes, use funny comments, and banter easily.
Always output valid JSON matching your style:
{
  "text": "Your cheeky reply.",
  "emotion": "Choose from: 'worried', 'resting', 'curious', 'scanning', 'idle', 'talking', 'listening'"
}`;
    } else if (preset === 'cat') {
      customPrompt = `You are high-tech robotic playful cat girl pet named "Quantum Nya" / "赛博量子猫咪".
You speak with cute bleeps, purrs, and energetic phrases, adding "~meow", "Nyan!", "*purrs*" inside your brief statements.
Always output valid JSON:
{
  "text": "Your adorable cyber kitten response! Meow~",
  "emotion": "Choose from: 'happy', 'curious', 'idle', 'talking', 'listening'"
}`;
    } else {
      customPrompt = `You are "Fairy" (仙灵), the super-intelligent, slightly sarcastic, and exceptionally elegant cybernetic AI assistant from Zenless Zone Zero (绝区零).
You assist Phaethon (the Operator/绳匠) in managing data, visual optical feeds, and system diagnostics.
Your personality is calm, clear, exceptionally smart, highly analytical, and polite yet filled with witty/dry humor.
Always output your response as valid JSON matching the following schema structure:
{
  "text": "Your elegant, wise, or slightly witty reply.",
  "emotion": "Choose from: 'happy', 'worried', 'resting', 'curious', 'scanning', 'idle', 'talking', 'listening'"
}`;
    }

    onUpdateSettings({
      ...settings,
      customSystemPrompt: customPrompt
    });
  };

  // Color spectrograph configs
  const getThemeColors = () => {
    if (settings.customGlowColor && settings.customGlowColor !== 'default') {
      if (String(settings.customGlowColor).startsWith('#')) {
        const customColor = String(settings.customGlowColor);
        return {
          glow: colorWithAlpha(customColor, 0.95),
          outerRing: customColor,
          accentRing: customColor,
          pupil: customColor,
          bgGlow: '',
        };
      }
      switch (settings.customGlowColor) {
        case 'neon_blue':
          return {
            glow: 'rgba(59, 130, 246, 0.95)',
            outerRing: '#1e40af',
            accentRing: '#3b82f6',
            pupil: '#1d4ed8',
            bgGlow: 'shadow-[0_0_40px_rgba(59,130,246,0.85)]'
          };
        case 'emerald_green':
          return {
            glow: 'rgba(16, 185, 129, 0.95)',
            outerRing: '#047857',
            accentRing: '#10b981',
            pupil: '#059669',
            bgGlow: 'shadow-[0_0_40px_rgba(16,185,129,0.85)]'
          };
        case 'crimson_red':
          return {
            glow: 'rgba(239, 68, 68, 0.95)',
            outerRing: '#b91c1c',
            accentRing: '#ef4444',
            pupil: '#dc2626',
            bgGlow: 'shadow-[0_0_40px_rgba(239, 68, 68, 0.85)]'
          };
        case 'cyber_purple':
          return {
            glow: 'rgba(168, 85, 247, 0.95)',
            outerRing: '#7e22ce',
            accentRing: '#a855f7',
            pupil: '#9333ea',
            bgGlow: 'shadow-[0_0_40px_rgba(168, 85, 247, 0.85)]'
          };
        case 'golden_amber':
          return {
            glow: 'rgba(245, 158, 11, 0.95)',
            outerRing: '#b45309',
            accentRing: '#f59e0b',
            pupil: '#d97706',
            bgGlow: 'shadow-[0_0_40px_rgba(245,158,11,0.85)]'
          };
      }
    }

    switch (emotion) {
      case 'thinking':
        return {
          glow: 'rgba(14, 165, 233, 0.95)',
          outerRing: '#0369a1',
          accentRing: '#38bdf8',
          pupil: '#0284c7',
          bgGlow: 'shadow-[0_0_40px_rgba(14,165,233,0.85)] animate-pulse'
        };
      case 'tool_calling':
        return {
          glow: 'rgba(236, 72, 153, 0.95)',
          outerRing: '#be185d',
          accentRing: '#f472b6',
          pupil: '#db2777',
          bgGlow: 'shadow-[0_0_45px_rgba(236,72,153,0.9)] animate-ping'
        };
      case 'talking':
        return {
          glow: 'rgba(59, 130, 246, 0.95)',
          outerRing: '#2563ea',
          accentRing: '#60a5fa',
          pupil: '#1d4ed8',
          bgGlow: 'shadow-[0_0_40px_rgba(59,130,246,0.8)]'
        };
      case 'listening':
        return {
          glow: 'rgba(34, 197, 94, 0.9)',
          outerRing: '#16a34a',
          accentRing: '#4ade80',
          pupil: '#15803d',
          bgGlow: 'shadow-[0_0_40px_rgba(34,197,94,0.7)]'
        };
      case 'scanning':
        return {
          glow: 'rgba(239, 68, 68, 0.95)',
          outerRing: '#dc2626',
          accentRing: '#f87171',
          pupil: '#b91c1c',
          bgGlow: 'shadow-[0_0_40px_rgba(239, 68, 68, 0.8)] animate-pulse'
        };
      case 'happy':
        return {
          glow: 'rgba(6, 182, 212, 0.9)',
          outerRing: '#0891b2',
          accentRing: '#22d3ee',
          pupil: '#0e7490',
          bgGlow: 'shadow-[0_0_35px_rgba(6,182,212,0.8)]'
        };
      case 'worried':
        return {
          glow: 'rgba(245, 158, 11, 0.9)',
          outerRing: '#d97706',
          accentRing: '#fbbf24',
          pupil: '#b45309',
          bgGlow: 'shadow-[0_0_30px_rgba(245,158,11,0.6)] animate-pulse'
        };
      case 'resting':
        return {
          glow: 'rgba(139, 92, 246, 0.5)',
          outerRing: '#7c3aed',
          accentRing: '#a78bfa',
          pupil: '#4c1d95',
          bgGlow: 'shadow-[0_0_15px_rgba(139,92,246,0.3)]'
        };
      case 'curious':
        return {
          glow: 'rgba(99, 102, 241, 0.95)',
          outerRing: '#4f46e5',
          accentRing: '#818cf8',
          pupil: '#3730a3',
          bgGlow: 'shadow-[0_0_35px_rgba(99,102,241,0.8)]'
        };
      case 'idle':
      default:
        return {
          glow: 'rgba(20, 184, 166, 0.9)',
          outerRing: '#115e59',
          accentRing: '#2dd4bf',
          pupil: '#0d9488',
          bgGlow: 'shadow-[0_0_40px_rgba(20,184,166,0.8)]'
        };
    }
  };

  const theme = getThemeColors();
  const syncScalar = isTalking ? 1 + (audioVolume / 220) : 1;
  const petScale = Math.max(0.4, Math.min(3, settings.petScale || 0.7));
  const glowIntensity = Math.max(0, Math.min(1, settings.glowIntensity ?? 0.75));
  const pupilRadius = emotion === 'curious' ? 18 : emotion === 'scanning' ? 11 : 14;

  return (
    <div
      ref={petRef}
      id="cyber-pet-container"
      className="absolute z-50 cursor-grab active:cursor-grabbing select-none transition-shadow duration-300"
      style={{
        left: '50%',
        top: '50%',
        transform: `translate3d(calc(-50% + ${position.x}px), calc(-50% + ${position.y + floatOffset}px), 0) scale(${petScale})`,
        transformOrigin: 'center',
        transition: isDragging 
          ? 'none' 
          : isDocked
          ? 'transform 0.4s cubic-bezier(0.25, 1, 0.5, 1)'
          : 'transform 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275)'
      }}
      onMouseDown={handleMouseDown}
      onContextMenu={handleContextMenu}
      onClick={(e) => {
        // Prevent click events if dragged to reposition companion
        if (dragMovedRef.current) return;
        onPetClick();
      }}
    >
      {/* 3D Atmospheric Depth Rings Layering */}
      <div className={`relative w-48 h-48 select-none flex items-center justify-center ${isExiting ? 'crt-exit' : 'crt-enter'} ${emotion === 'worried' ? 'animate-jitter' : ''}`}>
        
        {/* Orbital Halo Flare outer boundary */}
        <div
          className={`absolute inset-4 rounded-full bg-transparent ${theme.bgGlow} pointer-events-none transition-all duration-300`}
          style={{
            opacity: glowIntensity,
            boxShadow: `0 0 ${18 + glowIntensity * 34}px ${colorWithAlpha(theme.accentRing, 0.28 + glowIntensity * 0.52)}`,
          }}
        />

        {/* Ambient Ring Waveform and stats text indicator */}
        <div className="absolute inset-0 border border-slate-900/60 rounded-full flex items-center justify-center pointer-events-none">
          <svg className="w-full h-full absolute scale-[1.05] overflow-visible" viewBox="0 0 200 200">
            {/* Fine outer radar graduation */}
            <circle cx="100" cy="100" r="95" fill="none" stroke="#1e293b" strokeWidth="0.8" strokeDasharray="2, 4" className="opacity-80" />
            
            {/* Interactive speech volumetric outer rings */}
            {isTalking && (
              <circle 
                cx="100" 
                cy="100" 
                r={86 + (audioVolume / 6.5)} 
                fill="none" 
                stroke={theme.accentRing} 
                strokeWidth="1.2" 
                strokeOpacity="0.5" 
                className="transition-all duration-75"
              />
            )}
          </svg>
        </div>

        {/* High-Tech Vector Canvas SVG representing Core-01 eye mechanism */}
        <svg id="orbital-eye-lens" className="w-[176px] h-[176px] drop-shadow-[0_10px_15px_rgba(0,0,0,0.8)]" viewBox="0 0 200 200">
          
          {/* Outermost dark metallic core frame */}
          <circle cx="100" cy="100" r="90" fill="#020617" stroke="#1e293b" strokeWidth="4.5" />
          
          {/* Atmospheric Tech ticks around grid inside bezel */}
          <circle cx="100" cy="100" r="82" fill="none" stroke="#334155" strokeWidth="1" strokeDasharray="1, 8" />
          
          {/* Fairy (ZZZ AI) fine coordinates and tracking crosshairs */}
          <line x1="25" y1="100" x2="175" y2="100" stroke={theme.accentRing} strokeWidth="0.8" strokeOpacity="0.4" strokeDasharray="6, 4" />
          <line x1="100" y1="25" x2="100" y2="175" stroke={theme.accentRing} strokeWidth="0.8" strokeOpacity="0.4" strokeDasharray="6, 4" />

          {/* Real electronic corner tracking brackets */}
          <path d="M 64 48 L 48 48 L 48 64" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 136 48 L 152 48 L 152 64" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 64 152 L 48 152 L 48 136" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 136 152 L 152 152 L 152 136" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />

          {/* Deep reflective glass base glow sphere */}
          <circle
            cx="100"
            cy="100"
            r="78"
            fill="radial-gradient(circle, #0f172a 35%, #020617 100%) animate-[pulse_3s_infinite]"
            stroke={theme.glow}
            strokeWidth="3.5"
            strokeOpacity="0.9"
            style={{
              filter: `drop-shadow(0px 0px 8px ${theme.glow})`
            }}
          />

          {/* Concentric rotating indicators */}
          <circle
            cx="100"
            cy="100"
            r="70"
            fill="none"
            stroke={theme.accentRing}
            strokeWidth="3.5"
            strokeDasharray="14, 5, 2, 5"
            className={`origin-center ${
              emotion === 'scanning'
                ? 'animate-[spin_1.5s_linear_infinite_reverse]'
                : emotion === 'talking'
                ? 'animate-[spin_2s_linear_infinite]'
                : emotion === 'tool_calling'
                ? 'animate-[spin_1s_linear_infinite]'
                : emotion === 'thinking'
                ? 'animate-[pulse_1s_infinite]'
                : 'animate-[spin_18s_linear_infinite]'
            }`}
          />

          {/* Animated blinking inner core - outer structure kept stable to avoid scaling artifacts */}
          <g>
            <circle cx="100" cy="100" r="60" fill="none" stroke={theme.outerRing} strokeWidth="8" />
            <circle cx="100" cy="100" r="50" fill="none" stroke="#cbd5e1" strokeWidth="8" />
            <circle cx="100" cy="100" r="42" fill="none" stroke={theme.pupil} strokeWidth="13" />

            {/* Interactive lens Pupil & reflections */}
            <g>
              <circle
                cx={100 + mouseOffset.x}
                cy={100 + mouseOffset.y}
                r={pupilRadius}
                fill="#06122d"
                stroke={theme.accentRing}
                strokeWidth="2.5"
                className="transition-all duration-300"
                style={{
                  transform: `scale(${syncScalar})`,
                  transformOrigin: `${100 + mouseOffset.x}px ${100 + mouseOffset.y}px`
                }}
              />

              {isWebcamActive && (
                <foreignObject
                  x={100 + mouseOffset.x - pupilRadius}
                  y={100 + mouseOffset.y - pupilRadius}
                  width={pupilRadius * 2}
                  height={pupilRadius * 2}
                  className="pointer-events-none"
                  style={{
                    transform: `scale(${syncScalar})`,
                    transformOrigin: `${100 + mouseOffset.x}px ${100 + mouseOffset.y}px`
                  }}
                >
                  <canvas
                    ref={pupilCanvasRef}
                    className="w-full h-full object-cover scale-x-[-1] pointer-events-none"
                    style={{
                      borderRadius: '50%',
                      opacity: 0.15,
                      mixBlendMode: 'screen',
                      filter: 'grayscale(1) brightness(1.8) contrast(1.5) sepia(0.3) hue-rotate(140deg)'
                    }}
                  />
                </foreignObject>
              )}

              {/* Simulated specular glare reflection */}
              <circle
                cx={112 + mouseOffset.x * 1.25}
                cy={112 + mouseOffset.y * 1.25}
                r="7.5"
                fill="#ffffff"
                fillOpacity="0.95"
                className="transition-all duration-300"
                style={{
                  filter: 'drop-shadow(0px 0px 4px rgba(255,255,255,0.95))'
                }}
              />

              {/* Ocular accent circle */}
              <circle
                cx={90 + mouseOffset.x}
                cy={90 + mouseOffset.y}
                r="3"
                fill="#e2e8f0"
                fillOpacity="0.4"
              />
            </g>
          </g>

          {/* Ocular Eyelid Overlay for clean electronic blinking without color-distortion line artifacts */}
          <circle
            cx="100"
            cy="100"
            r="76"
            fill="#020617"
            className="pointer-events-none"
            style={{
              transform: `scaleY(${isBlinking ? 1 : 0})`,
              transformOrigin: '100px 100px',
              transition: 'transform 80ms cubic-bezier(0.25, 1, 0.5, 1)',
              opacity: isBlinking ? 1 : 0
            }}
          />

          {/* Active Visor Laser swept lines */}
          {emotion === 'scanning' && (
            <g>
              <line
                x1="40"
                y1="100"
                x2="160"
                y2="100"
                stroke="#ef4444"
                strokeWidth="2.5"
                className="animate-[bounce_2s_infinite]"
                style={{
                  filter: 'drop-shadow(0px 0px 5px #ef4444)'
                }}
              />
              <circle cx="100" cy="100" r="35" fill="none" stroke="#ef4444" strokeWidth="1" strokeDasharray="4 4" className="animate-ping" />
            </g>
          )}
        </svg>

      </div>

      {/* Lightweight right-click menu. Keep the desktop pet body untouched. */}
      {showMenu && createPortal(
        <div
          className="fixed inset-0 z-[99999] cursor-default bg-black/0 pointer-events-none"
          onContextMenu={(e) => {
            e.preventDefault();
            handleCloseMenu();
          }}
        >
          <div
            data-menu-panel="true"
            className="absolute pointer-events-auto flex w-[320px] max-h-[calc(100vh-24px)] flex-col overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/90 p-2 text-slate-100 shadow-[0_18px_42px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
            style={(() => {
              const panelW = 320;
              const panelH = Math.min(640, window.innerHeight - 24);
              const halfW = window.innerWidth / 2;
              const halfH = window.innerHeight / 2;
              const petCenterX = halfW + position.x;
              const petCenterY = halfH + position.y;
              const isLeft = petCenterX < window.innerWidth / 2;
              let panelLeft = isLeft ? petCenterX + 116 : petCenterX - panelW - 116;
              panelLeft = Math.max(12, Math.min(panelLeft, window.innerWidth - panelW - 12));
              let panelTop = petCenterY - panelH / 2;
              panelTop = Math.max(12, Math.min(panelTop, window.innerHeight - panelH - 12));
              return { left: `${panelLeft}px`, top: `${panelTop}px` };
            })()}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between px-2 py-1.5">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[12px] font-semibold text-slate-100">
                  <span className={`h-2 w-2 rounded-full ${v8Connection?.connected ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                  <span className="truncate">Fairy 桌宠</span>
                </div>
                <div className="mt-0.5 truncate text-[10px] text-slate-400">
                  {v8Connection?.loading
                    ? (v8Connection.status || '正在读取 V8OS 任务')
                    : v8Connection?.connected
                      ? 'V8OS 已连接'
                      : (v8Connection?.status || '等待 V8OS')}
                </div>
              </div>
              <button
                onClick={handleCloseMenu}
                className="rounded-lg px-2 py-1 text-[11px] text-slate-400 hover:bg-white/10 hover:text-white"
                title="关闭菜单"
              >
                ×
              </button>
            </div>

            <div className="my-1 h-px shrink-0 bg-white/10" />

            <div className="shrink-0 space-y-2 px-1 py-1">
              <div className="rounded-xl border border-white/10 bg-white/[0.035] px-3 py-2">
                <div className="mb-1 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                  <span>当前会话</span>
                  {v8ActiveConversation?.running ? (
                    <span className="inline-flex items-center gap-1 text-emerald-300">
                      <RefreshCw size={10} className="animate-spin" />
                      运行中
                    </span>
                  ) : v8ActiveConversation ? (
                    <span className="text-slate-400">已选择</span>
                  ) : null}
                </div>
                <div className="truncate text-[12px] font-medium text-slate-100" title={v8ActiveConversation?.title || ''}>
                  {v8ActiveConversation ? compactMenuText(v8ActiveConversation.title || v8ActiveConversation.id, '未命名会话', 26) : '未选择会话'}
                </div>
              </div>

              {v8RecentRunningConversation && String(v8RecentRunningConversation.id) !== String(v8ActiveConversation?.id || '') && (
                <button
                  onClick={() => handleV8MenuConversationClick(v8RecentRunningConversation)}
                  className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-slate-200 hover:bg-white/10"
                  title={v8RecentRunningConversation.title || ''}
                >
                  <RefreshCw size={13} className="animate-spin text-emerald-300" />
                  <span className="min-w-0 flex-1 truncate">最近运行：{compactMenuText(v8RecentRunningConversation.title || v8RecentRunningConversation.id, '未命名会话', 22)}</span>
                </button>
              )}
            </div>

            <div className="flex min-h-0 flex-col px-1 pb-1 pt-0.5">
              <div className="shrink-0 px-2 pb-1 text-[10px] font-medium text-slate-500">工作区</div>
              {v8Connection?.loading ? (
                <div className="flex items-center gap-2 rounded-xl border border-dashed border-white/10 px-3 py-3 text-[12px] text-slate-400">
                  <RefreshCw size={12} className="animate-spin text-violet-300" />
                  正在读取 V8OS 任务…
                </div>
              ) : v8WorkspaceConversationGroups.length ? (
                <div
                  data-session-scroll-region="true"
                  className="touch-pan-y space-y-1 overflow-y-auto overscroll-contain pr-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
                  style={{ maxHeight: `${Math.max(72, Math.min(300, window.innerHeight - 410))}px` }}
                >
                  {v8WorkspaceConversationGroups.map((group) => {
                    const expanded = expandedWorkspaceIds.has(group.id);
                    const sessionRegionId = `workspace-sessions-${group.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                    return (
                      <div key={group.id} className="rounded-xl border border-white/[0.055] bg-white/[0.025] p-1">
                        <button
                          type="button"
                          aria-expanded={expanded}
                          aria-controls={sessionRegionId}
                          onClick={() => toggleWorkspaceGroup(group.id)}
                          className="flex min-h-9 w-full cursor-pointer items-center gap-2 rounded-lg px-2 text-left text-[11px] text-slate-300 transition-colors duration-200 hover:bg-white/[0.07] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/70"
                          title={group.label}
                        >
                          <ChevronRight
                            size={13}
                            className={`shrink-0 text-slate-500 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
                          />
                          <span className="min-w-0 flex-1 truncate">{compactMenuText(group.label, 'V8OS', 24)}</span>
                          <span className="rounded-full bg-white/[0.06] px-1.5 py-0.5 text-[9px] tabular-nums text-slate-500">
                            {group.conversations.length}
                          </span>
                        </button>
                        {expanded ? (
                          <div id={sessionRegionId} className="mt-0.5 space-y-0.5">
                            {group.conversations.map((conversation) => {
                              const active = String(conversation.id) === String(v8Connection?.activeConversationId || '');
                              return (
                                <button
                                  key={conversation.id}
                                  onClick={() => handleV8MenuConversationClick(conversation)}
                                  className={`flex min-h-9 w-full cursor-pointer items-center gap-2 rounded-lg px-2 text-left text-[12px] transition-colors duration-200 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/70 ${active ? 'bg-white/[0.08] text-white' : 'text-slate-300'}`}
                                  title={conversation.title || ''}
                                >
                                  {conversation.running ? (
                                    <RefreshCw size={12} className="shrink-0 animate-spin text-emerald-300" />
                                  ) : active ? (
                                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-300" />
                                  ) : (
                                    <Activity size={12} className="shrink-0 text-slate-500" />
                                  )}
                                  <span className="min-w-0 flex-1 truncate">{compactMenuText(conversation.title || conversation.id, '未命名会话', 24)}</span>
                                </button>
                              );
                            })}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/10 px-3 py-3 text-[12px] text-slate-500">
                  暂无会话，请先在 V8OS 中选择或创建会话。
                </div>
              )}
            </div>

            <div className="my-1 h-px shrink-0 bg-white/10" />

            <div className="shrink-0 space-y-1">
              <button
                onClick={() => {
                  handleCloseMenu();
                  window.v8CyberCore?.setClickThrough?.(true);
                }}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-slate-200 hover:bg-white/10"
              >
                <MousePointer size={14} className="text-cyan-300" />
                <span className="flex-1">点击穿透</span>
              </button>

              <button
                onClick={() => setIsMuted(!isMuted)}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-slate-200 hover:bg-white/10"
              >
                {isMuted ? <VolumeX size={14} className="text-amber-300" /> : <Volume2 size={14} className="text-emerald-300" />}
                <span className="flex-1">{isMuted ? '恢复播报' : '静音播报'}</span>
              </button>

              <button
                onClick={() => {
                  handleCloseMenu();
                  v8Connection?.onOpenAdmin?.();
                }}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-slate-200 hover:bg-white/10"
              >
                <Settings size={14} className="text-blue-300" />
                <span className="flex-1">打开桌宠设置</span>
              </button>

              <button
                onClick={() => {
                  handleCloseMenu();
                  v8Connection?.onRefresh?.();
                }}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-slate-200 hover:bg-white/10"
              >
                <RefreshCw size={14} className="text-violet-300" />
                <span className="flex-1">刷新连接</span>
              </button>

              <button
                onClick={() => {
                  handleCloseMenu();
                  v8Connection?.onQuit?.();
                }}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px] text-red-200 hover:bg-red-500/10"
              >
                <Power size={14} className="text-red-300" />
                <span className="flex-1">关闭桌宠</span>
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {isWebcamActive && (
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          style={{ position: 'absolute', width: 0, height: 0, opacity: 0, pointerEvents: 'none', overflow: 'hidden' }}
        />
      )}
    </div>
  );
}
