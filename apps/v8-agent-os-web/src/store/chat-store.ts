import { create } from 'zustand';
import { Message } from './chat-types';
import { normalizeMessagesForState } from '@/lib/chat-stream-state';

interface ChatState {
    messages: Message[];
    isLoading: boolean;
    setMessages: (messages: Message[] | ((prev: Message[]) => Message[])) => void;
    setIsLoading: (isLoading: boolean) => void;
    clearMessages: () => void;
    activeArtifactId: string | null;
    setActiveArtifactId: (id: string | null) => void;
}

export const useChatStore = create<ChatState>((set) => ({
    messages: [],
    isLoading: false,
    activeArtifactId: null,
    setMessages: (updater) => set((state) => ({
        messages: normalizeMessagesForState(
            typeof updater === 'function' ? updater(state.messages) : updater
        )
    })),
    setIsLoading: (isLoading) => set({ isLoading }),
    clearMessages: () => set({ messages: [] }),
    setActiveArtifactId: (id) => set({ activeArtifactId: id })
}));
