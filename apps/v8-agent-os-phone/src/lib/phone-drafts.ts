import { readMetadata, writeMetadata } from "@/src/lib/mobile-storage";

export type DraftSnapshot = {
    revision: number;
    composerRevision: number;
    values: Record<string, unknown>;
    loaded: boolean;
    error: string;
};
type Entry = { snapshot: DraftSnapshot; listeners: Set<() => void>; timer?: ReturnType<typeof setTimeout>;
    loading?: Promise<void>; writing?: Promise<void>; savedRevision: number };

export class PhoneDraftStore {
    private entries = new Map<string, Entry>();
    constructor(private readonly storage: { read: typeof readMetadata; write: typeof writeMetadata }) {}
    private entry(key: string) {
        if (!key) throw new Error("A draft requires a complete session identity");
        let entry = this.entries.get(key);
        if (!entry) {
            entry = { snapshot: { revision: 0, composerRevision: 0, values: {}, loaded: false, error: "" }, listeners: new Set(), savedRevision: 0 };
            this.entries.set(key, entry);
        }
        return entry;
    }
    get = (key: string) => this.entry(key).snapshot;
    subscribe = (key: string, listener: () => void) => {
        const entry = this.entry(key);
        entry.listeners.add(listener);
        return () => { entry.listeners.delete(listener); };
    };
    private notify(entry: Entry, snapshot: DraftSnapshot) {
        entry.snapshot = snapshot;
        for (const listener of entry.listeners) listener();
    }
    async hydrate(key: string) {
        const entry = this.entry(key);
        if (entry.snapshot.loaded) return;
        if (entry.loading) return entry.loading;
        entry.loading = (async () => {
            try {
                const raw = await this.storage.read(`v8.phone.draft.v2.${key}`);
                const saved = raw ? JSON.parse(raw) as DraftSnapshot : null;
                const pending = saved?.values?.pendingIntent as Record<string, unknown> | undefined;
                if (pending?.state === "submitting") {
                    // A previous process cannot still confirm this submission.
                    // Preserve its id and fingerprint for reconciliation / retry.
                    saved!.values.pendingIntent = { ...pending, state: "acceptance_unknown" };
                }
                const current = entry.snapshot;
                // Fields edited while storage was loading win; untouched fields restore.
                this.notify(entry, { ...current, loaded: true, error: "",
                    revision: Math.max(saved?.revision || 0, current.revision),
                    composerRevision: Math.max(saved?.composerRevision || 0, current.composerRevision),
                    values: { ...saved?.values, ...current.values } });
            } catch {
                this.notify(entry, { ...entry.snapshot, error: "Draft storage could not be read. Retry before leaving this connection." });
                throw new Error(entry.snapshot.error);
            } finally { entry.loading = undefined; }
        })();
        return entry.loading;
    }
    set(key: string, field: string, value: unknown | ((previous: unknown) => unknown), fallback?: unknown) {
        const entry = this.entry(key);
        const previous = entry.snapshot.values[field] ?? fallback;
        const next = typeof value === "function" ? (value as (previous: unknown) => unknown)(previous) : value;
        if (Object.is(previous, next)) return;
        this.notify(entry, { ...entry.snapshot, revision: entry.snapshot.revision + 1,
            composerRevision: entry.snapshot.composerRevision + (["input", "files", "command", "skills", "families", "plugins", "contextSessionRefs", "specMode"].includes(field) ? 1 : 0),
            values: { ...entry.snapshot.values, [field]: next } });
        if (entry.timer) clearTimeout(entry.timer);
        entry.timer = setTimeout(() => { entry.timer = undefined; void this.flush(key).catch(() => undefined); }, 120);
    }
    compareAndSet(key: string, revision: number, values: Record<string, unknown>) {
        const entry = this.entry(key);
        if (entry.snapshot.composerRevision !== revision) return false;
        this.notify(entry, { ...entry.snapshot, revision: entry.snapshot.revision + 1, composerRevision: revision + 1,
            values: { ...entry.snapshot.values, ...values } });
        void this.flush(key).catch(() => undefined);
        return true;
    }
    async flush(key: string): Promise<void> {
        const entry = this.entry(key);
        if (entry.timer) { clearTimeout(entry.timer); entry.timer = undefined; }
        if (entry.writing) { await entry.writing; return this.flush(key); }
        await this.hydrate(key);
        if (entry.savedRevision === entry.snapshot.revision && !entry.snapshot.error) return;
        const snapshot = entry.snapshot;
        const request = (async () => {
            try {
                await this.storage.write(`v8.phone.draft.v2.${key}`, JSON.stringify({ ...snapshot, error: "" }));
                entry.savedRevision = snapshot.revision;
                if (entry.snapshot.error) this.notify(entry, { ...entry.snapshot, error: "" });
            } catch {
                this.notify(entry, { ...entry.snapshot, error: "Draft was not saved. Keep this connection open and retry." });
                throw new Error(entry.snapshot.error);
            }
        })();
        entry.writing = request;
        try { await request; } finally { if (entry.writing === request) entry.writing = undefined; }
    }
    async flushAll() { for (const key of this.entries.keys()) await this.flush(key); }
    evictSavedInactive() {
        if (this.entries.size <= 128) return;
        for (const [key, entry] of this.entries) {
            if (entry.listeners.size || entry.timer || entry.loading || entry.writing || entry.snapshot.error
                || entry.savedRevision !== entry.snapshot.revision) continue;
            this.entries.delete(key);
            if (this.entries.size <= 128) break;
        }
    }
}

export const phoneDrafts = new PhoneDraftStore({ read: readMetadata, write: writeMetadata });
