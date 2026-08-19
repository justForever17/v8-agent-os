export {};

declare global {
  interface Window {
    v8CyberCore?: {
      platform?: string;
      readonly transport?: {
        readonly engineWebSocketUrl: string;
      } | null;
      openAdmin?: () => Promise<void>;
      reportStatus?: (payload: {
        state: 'waiting_v8os' | 'connected' | 'stopping' | 'error';
        activeSessionId?: string | null;
      }) => Promise<boolean>;
      openSession?: (sessionId: string) => Promise<boolean>;
      getActiveSession?: () => Promise<{ sessionId?: string | null }>;
      shutdownReady?: (requestId: string) => Promise<boolean>;
      quit?: () => Promise<void>;
      setAlwaysOnTop?: (enabled: boolean) => Promise<boolean>;
      setPanelOpen?: (enabled: boolean) => Promise<unknown>;
      setClickThrough?: (enabled: boolean) => Promise<boolean>;
      setInteractionRegions?: (regions: Array<{ x: number; y: number; width: number; height: number }>) => void;
      setCompanionScale?: (scale: number) => Promise<boolean | { width: number; height: number }>;
      moveWindowBy?: (dx: number, dy: number) => Promise<boolean>;
      readLocalConfig?: (key: string) => Promise<Record<string, unknown> | null>;
      writeLocalConfig?: (key: string, value: Record<string, unknown>) => Promise<boolean>;
      getMediaPermissionStatus?: (kind: 'microphone' | 'camera') => Promise<Record<string, unknown>>;
      requestMediaAccess?: (kind: 'microphone' | 'camera') => Promise<Record<string, unknown>>;
      openMediaPrivacySettings?: (kind: 'microphone' | 'camera') => Promise<boolean>;
      onPrepareShutdown?: (callback: (data?: { requestId?: string }) => void) => () => void;
      onActiveSession?: (callback: (data?: { sessionId?: string | null }) => void) => () => void;
      onDesktopPetConfigChanged?: (callback: (data?: { domain?: string; changedAt?: number }) => void) => () => void;
      onPanelExpandDirection?: (callback: (data: {
        isLeft: boolean;
        isTop: boolean;
        offsetX?: number;
        offsetY?: number;
        closedWidth?: number;
        closedHeight?: number;
      }) => void) => () => void;
    };
  }
}
