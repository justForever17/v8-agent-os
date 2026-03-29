import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface VoiceState {
  isVoiceEnabled: boolean;
  isSpeaking: boolean;
  toggleVoice: () => void;
  setSpeaking: (speaking: boolean) => void;
}

export const useVoiceStore = create<VoiceState>()(
  persist(
    (set) => ({
      isVoiceEnabled: false,
      isSpeaking: false,
      toggleVoice: () => set((state) => ({ isVoiceEnabled: !state.isVoiceEnabled })),
      setSpeaking: (speaking) => set({ isSpeaking: speaking }),
    }),
    {
      name: 'v8-agent-os-voice-storage', // unique name
      partialize: (state) => ({ isVoiceEnabled: state.isVoiceEnabled }), // Only persist isVoiceEnabled
    }
  )
);
