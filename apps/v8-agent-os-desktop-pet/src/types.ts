export type PetEmotion =
  | 'idle'
  | 'talking'
  | 'listening'
  | 'curious'
  | 'scanning'
  | 'happy'
  | 'worried'
  | 'resting'
  | 'thinking'
  | 'tool_calling';

export interface ChatMessage {
  id: string;
  sender: 'user' | 'pet';
  text: string;
  timestamp: string;
  emotion?: PetEmotion;
  image?: string; // Base64 or URL if webcam frame was sent with it
  thinking?: string; // Optional reasoning content
  toolCall?: string; // Optional tool execution logs
}

export interface ElectronConfigSnippet {
  title: string;
  description: string;
  filename: string;
  code: string;
}

export interface SystemMetric {
  label: string;
  value: string;
  level: number; // 0 to 100
}

export interface PetSettings {
  lang: 'zh' | 'en';
  gender: 'robotic_male' | 'robotic_female' | 'autonomous_ai' | 'charming';
  pitch: number; // 0.5 to 2.0
  rate: number; // 0.5 to 2.0
  voiceURI: string;
  customSystemPrompt: string;
  floatAmplitude: number; // 0 to 20
  floatSpeed: number; // 0.1 to 3
  petScale: number; // 0.4 to 3.0, 0.7 keeps the current default size
  customGlowColor: 'default' | 'neon_blue' | 'emerald_green' | 'crimson_red' | 'cyber_purple' | 'golden_amber';
  gazeTracking: boolean;
  
  captureMode?: 'camera' | 'desktop_camera';
  v8AdminBaseUrl: string;
  v8WorkspacePath: string;
  v8EventRulesJson: string;
  
  // Custom TTS engine options
  ttsEngine: 'v8os' | 'custom' | 'webspeech' | 'edge';
  edgeTtsVoice: 'zh-CN-XiaoxiaoNeural' | 'zh-CN-YunxiNeural' | 'en-US-AriaNeural' | 'en-US-GuyNeural' | 'ja-JP-NanamiNeural';
  customTtsUrl: string;
  customTtsKey: string;
  customTtsVoice: string;
  customTtsModel: string;
  sttLanguage: 'zh-CN' | 'en-US' | 'auto';
}
