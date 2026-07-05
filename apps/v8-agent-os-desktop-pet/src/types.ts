export type PetEmotion =
  | "idle"
  | "talking"
  | "listening"
  | "curious"
  | "scanning"
  | "happy"
  | "worried"
  | "resting"
  | "thinking"
  | "tool_calling";

export type ChatMessage = {
  id: string;
  sender: "user" | "pet" | "system";
  text: string;
  timestamp: number;
  emotion?: PetEmotion;
  audioUrl?: string;
};

export type V8EventActionRule = {
  id: string;
  match: string;
  emotion: PetEmotion;
  voice?: string;
  spectrum?: "default" | "cyan" | "violet" | "amber" | "emerald" | "rose";
};

export type PetSettings = {
  lang: "zh" | "en";
  petScale: number;
  floatAmplitude: number;
  floatSpeed: number;
  customGlowColor: "default" | "neon_blue" | "emerald_green" | "crimson_red" | "cyber_purple" | "golden_amber";
  ttsEnabled: boolean;
  muted: boolean;
  isWakewordActive: boolean;
  wakeword: string;
  wakeWindowMs: number;
  sttLanguage: "zh-CN" | "en-US" | "auto";
  v8EventRulesJson: string;
};

export type DesktopConversationSummary = {
  id: string;
  title: string;
  projectName?: string;
  workspacePath?: string | null;
  running?: boolean;
};

export type TrayContextPayload = {
  activeConversationId?: string;
  conversations: DesktopConversationSummary[];
};

declare global {
  interface Window {
    v8CyberCore?: {
      platform?: string;
      openAdmin?: (url?: string) => Promise<boolean>;
      setClickThrough?: (enabled: boolean) => Promise<boolean>;
      setPanelOpen?: (enabled: boolean) => Promise<unknown>;
      setCompanionScale?: (scale: number) => Promise<unknown>;
      readLocalConfig?: (key?: string) => Promise<unknown>;
      writeLocalConfig?: (key: string, value: unknown) => Promise<boolean>;
      getMediaPermissionStatus?: (kind: "microphone" | "camera") => Promise<unknown>;
      requestMediaAccess?: (kind: "microphone" | "camera") => Promise<unknown>;
      openMediaPrivacySettings?: (kind: "microphone" | "camera") => Promise<boolean>;
      updateTrayContext?: (payload: TrayContextPayload) => Promise<boolean>;
      onTraySelectConversation?: (callback: (conversationId: string) => void) => () => void;
      onTrayStartListening?: (callback: () => void) => () => void;
      onPrepareShutdown?: (callback: () => void) => () => void;
      quit?: () => Promise<boolean>;
    };
  }
}

