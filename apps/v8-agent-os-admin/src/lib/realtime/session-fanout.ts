type SessionEventListener = (event: unknown) => void;

class SessionFanoutHub {
    private listeners = new Map<string, Set<SessionEventListener>>();

    subscribe(sessionId: string, listener: SessionEventListener) {
        const existing = this.listeners.get(sessionId) || new Set<SessionEventListener>();
        existing.add(listener);
        this.listeners.set(sessionId, existing);

        return () => {
            const current = this.listeners.get(sessionId);
            if (!current) return;
            current.delete(listener);
            if (current.size === 0) {
                this.listeners.delete(sessionId);
            }
        };
    }

    publish(sessionId: string, event: unknown) {
        const current = this.listeners.get(sessionId);
        if (!current || current.size === 0) return;

        for (const listener of current) {
            try {
                listener(event);
            } catch (error) {
                console.error("[SessionFanoutHub] listener failed:", error);
            }
        }
    }
}

export const sessionFanoutHub = new SessionFanoutHub();
