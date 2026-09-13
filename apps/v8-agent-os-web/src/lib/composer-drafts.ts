// Unsent user content only. Runs, receipts and queued messages remain Engine owned.
export type DraftRecord = {
    key: string; values: Record<string, unknown>; revision: number; updatedAt: number;
    saved: boolean; error: boolean; hydrated: boolean; contentRevision?: number;
    submission?: { revision: number; clientMessageId: string };
};
const records = new Map<string, DraftRecord>();
const listeners = new Map<string, Set<() => void>>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const pending = new Map<string, Promise<void>>();
const loads = new Map<string, Promise<void>>();
const RETENTION_MS = 30 * 86400_000;
let database: Promise<IDBDatabase> | undefined;
function db() {
    return database ??= new Promise((resolve, reject) => {
        const request = indexedDB.open("v8-composer-drafts-v1", 1);
        request.onupgradeneeded = () => request.result.createObjectStore("drafts", { keyPath: "key" });
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => { database = undefined; reject(request.error); };
    });
}
export function draftOwnerKey(instance: string, principal: string, workspace: string, session: string) {
    return instance && principal && workspace && session ? JSON.stringify([instance, principal, workspace, session]) : "";
}
export function readDraft(key: string): DraftRecord {
    if (!records.has(key)) records.set(key, { key, values: {}, revision: 0, updatedAt: 0, saved: true, error: false, hydrated: !key });
    return records.get(key)!;
}
function publish(record: DraftRecord) {
    records.set(record.key, record);
    listeners.get(record.key)?.forEach((listener) => listener());
}
export function subscribeDraft(key: string, listener: () => void) {
    const set = listeners.get(key) ?? new Set();
    listeners.set(key, set); set.add(listener);
    return () => { set.delete(listener); if (!set.size) listeners.delete(key); };
}
export function setDraftField<T>(key: string, field: string, value: T | ((previous: T) => T), initial: T) {
    if (!key) return;
    const record = readDraft(key);
    const previous = (record.values[field] ?? initial) as T;
    const next = typeof value === "function" ? (value as (previous: T) => T)(previous) : value;
    if (Object.is(previous, next)) return;
    publish({ ...record, values: { ...record.values, [field]: next }, revision: record.revision + 1, contentRevision: (record.contentRevision || 0) + (["selection", "scroll", "uploading"].includes(field) ? 0 : 1), updatedAt: Date.now(), saved: false });
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => { timers.delete(key); void flushDraft(key); }, 300));
}
export function persistentDraft(record: DraftRecord) {
    const values = { ...record.values };
    // File handles survive switches in memory. After a renderer restart only their
    // descriptors survive; authorized uploaded source refs are stored separately.
    values.files = ((values.files || []) as File[]).map((file) => ({ name: file.name, type: file.type, size: file.size, reselect: true }));
    delete values.uploading;
    return { ...record, values, saved: true, error: false, hydrated: true };
}
export function flushDraft(key: string): Promise<void> {
    clearTimeout(timers.get(key)); timers.delete(key);
    const work = (pending.get(key) ?? Promise.resolve()).then(async () => {
        const record = readDraft(key);
        if (!key || record.saved) return;
        try {
            const connection = await db();
            await new Promise<void>((resolve, reject) => {
                const tx = connection.transaction("drafts", "readwrite");
                tx.objectStore("drafts").put(persistentDraft(record));
                tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
            });
            const current = readDraft(key);
            if (current.revision === record.revision) publish({ ...current, saved: true, error: false });
        } catch {
            publish({ ...readDraft(key), error: true });
        }
    });
    pending.set(key, work);
    void work.finally(() => { if (pending.get(key) === work) pending.delete(key); });
    return work;
}
export function hydrateDraft(key: string): Promise<void> {
    if (!key || readDraft(key).hydrated) return Promise.resolve();
    if (loads.has(key)) return loads.get(key)!;
    const revision = readDraft(key).revision;
    const work = (async () => {
        try {
            const connection = await db();
            const saved = await new Promise<DraftRecord | undefined>((resolve, reject) => {
                const request = connection.transaction("drafts").objectStore("drafts").get(key);
                request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
            });
            const current = readDraft(key);
            if (saved && saved.key === key && Date.now() - saved.updatedAt < RETENTION_MS && current.revision === revision) {
                publish({ ...saved, hydrated: true, error: false, saved: true });
            } else publish({ ...current, hydrated: true });
        } catch { publish({ ...readDraft(key), hydrated: true, error: true }); }
    })();
    loads.set(key, work);
    void work.finally(() => loads.delete(key));
    return work;
}
export function beginDraftSubmission(key: string) {
    const record = readDraft(key);
    const revision = record.contentRevision || 0;
    const submission = record.submission?.revision === revision ? record.submission : { revision, clientMessageId: crypto.randomUUID() };
    publish({ ...record, submission, saved: false });
    void flushDraft(key);
    return submission;
}
export function acknowledgeDraft(key: string, revision: number) {
    const record = readDraft(key);
    if ((record.contentRevision || 0) !== revision) return false;
    publish({ ...record, values: { scroll: record.values.scroll }, submission: undefined, revision: record.revision + 1, contentRevision: revision + 1, updatedAt: Date.now(), saved: false });
    void flushDraft(key); return true;
}
export async function removeDrafts(predicate: (key: string) => boolean) {
    for (const key of records.keys()) if (predicate(key)) {
        clearTimeout(timers.get(key)); timers.delete(key);
        await pending.get(key);
        records.delete(key); listeners.get(key)?.forEach((listener) => listener());
    }
    try {
        const connection = await db();
        const tx = connection.transaction("drafts", "readwrite");
        const cursor = tx.objectStore("drafts").openCursor();
        cursor.onsuccess = () => { const entry = cursor.result; if (!entry) return; if (predicate(String(entry.key)) || Date.now() - entry.value.updatedAt >= RETENTION_MS) entry.delete(); entry.continue(); };
    } catch { /* Memory was already cleared. Persistence failure stays local. */ }
}
export function flushAllDrafts() { return Promise.all([...records.keys()].map(flushDraft)); }
