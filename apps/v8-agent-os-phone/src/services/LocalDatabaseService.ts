import * as SQLite from "expo-sqlite";
import { phoneSessionKey } from "@/src/lib/phone-identity";

export function buildLocalSessionIndexNamespace(authorityKey: string, servingInstanceId: string) {
    if (!authorityKey || !servingInstanceId) throw new Error("Cache requires a paired identity");
    return JSON.stringify([authorityKey, servingInstanceId]);
}

export const PHONE_CACHE_SCHEMA = `
    CREATE TABLE IF NOT EXISTS messages (
        session_key TEXT NOT NULL, id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        created_at TEXT NOT NULL, turn_id TEXT, turn_position INTEGER, raw_json TEXT NOT NULL,
        PRIMARY KEY (session_key, id)
    );
    CREATE INDEX IF NOT EXISTS messages_turn ON messages (session_key, turn_position, ordinal);
    CREATE TABLE IF NOT EXISTS cursors (session_key TEXT PRIMARY KEY, sync_cursor TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deletions (
        session_key TEXT NOT NULL, message_id TEXT NOT NULL, deleted_at TEXT NOT NULL,
        PRIMARY KEY (session_key, message_id)
    );
    CREATE TABLE IF NOT EXISTS indexes (namespace TEXT PRIMARY KEY, raw_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cache_usage (session_key TEXT PRIMARY KEY, touched_at INTEGER NOT NULL);
`;

let connection: Promise<SQLite.SQLiteDatabase> | null = null;
let writes: Promise<unknown> = Promise.resolve();
function database() {
    if (!connection) connection = (async () => {
        // Legacy unscoped server cache is quarantined in v8_agent_os.db. It cannot
        // be attributed to a pairing, and is never imported into this database.
        const db = await SQLite.openDatabaseAsync("v8_phone_cache_v2.db");
        await db.execAsync(PHONE_CACHE_SCHEMA);
        return db;
    })().catch((error) => { connection = null; throw error; });
    return connection;
}

function write<T>(operation: (db: SQLite.SQLiteDatabase) => Promise<T>): Promise<T> {
    const next = writes.catch(() => undefined).then(async () => operation(await database()));
    writes = next;
    return next;
}

/** Immutable handle: a late A request can never acquire B's cache namespace. */
export function createLocalDatabase(authorityKey: string, servingInstanceId: string) {
    return new LocalDatabaseService(authorityKey, servingInstanceId);
}

export class LocalDatabaseService {
    constructor(private readonly authorityKey: string, private readonly servingInstanceId: string) {}
    private key(sessionId: string) { return phoneSessionKey(this.authorityKey, this.servingInstanceId, sessionId); }
    async init() { await database(); }

    async getSyncCursor(sessionId: string): Promise<string> {
        const row = await (await database()).getFirstAsync<{ sync_cursor: string }>(
            "SELECT sync_cursor FROM cursors WHERE session_key = ?", [this.key(sessionId)],
        );
        return row?.sync_cursor || "";
    }
    async setSyncCursor(sessionId: string, cursor: string) {
        const key = this.key(sessionId);
        await write((db) => db.runAsync("INSERT OR REPLACE INTO cursors VALUES (?, ?)", [key, cursor]));
    }
    async upsertMessages(sessionId: string, messages: any[]) {
        const key = this.key(sessionId);
        if (!messages.length) return;
        await write(async (db) => {
            await db.withTransactionAsync(async () => {
                const statement = await db.prepareAsync(`INSERT OR REPLACE INTO messages
                    (session_key, id, ordinal, created_at, turn_id, turn_position, raw_json)
                    SELECT ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (
                        SELECT 1 FROM deletions WHERE session_key = ? AND message_id = ?
                    )`);
                try {
                    for (const message of messages) {
                        const id = String(message?.id || "");
                        if (!id) continue;
                        await statement.executeAsync([key, id, message.ordinal || 0,
                            message.createdAt || message.created_at || "",
                            message.turnId || message.turn_id || null,
                            message.turnPosition || message.turn_position || null,
                            JSON.stringify(message), key, id]);
                    }
                } finally { await statement.finalizeAsync(); }
                await db.runAsync("INSERT OR REPLACE INTO cache_usage VALUES (?, ?)", [key, Date.now()]);
            });
            await this.prune(db, key);
        });
    }
    private async prune(db: SQLite.SQLiteDatabase, activeKey: string) {
        // History only: drafts / pending intents live separately and are never evicted.
        const size = await db.getFirstAsync<{ bytes: number; count: number }>(
            "SELECT COALESCE(SUM(LENGTH(CAST(raw_json AS BLOB))), 0) AS bytes, COUNT(*) AS count FROM messages",
        );
        if ((size?.bytes || 0) <= 32 * 1024 * 1024 && (size?.count || 0) <= 50_000) return;
        const rows = await db.getAllAsync<{ session_key: string }>(
            "SELECT session_key FROM cache_usage WHERE session_key <> ? ORDER BY touched_at ASC", [activeKey],
        );
        for (const row of rows) {
            await this.deleteKey(db, row.session_key);
            const remaining = await db.getFirstAsync<{ bytes: number; count: number }>(
                "SELECT COALESCE(SUM(LENGTH(CAST(raw_json AS BLOB))), 0) AS bytes, COUNT(*) AS count FROM messages",
            );
            if ((remaining?.bytes || 0) <= 24 * 1024 * 1024 && (remaining?.count || 0) <= 40_000) break;
        }
    }
    async deleteMessages(sessionId: string, ids: string[]) {
        const key = this.key(sessionId);
        await write(async (db) => db.withTransactionAsync(async () => {
            for (const id of ids.filter(Boolean)) {
                await db.runAsync("INSERT OR REPLACE INTO deletions VALUES (?, ?, ?)", [key, id, new Date().toISOString()]);
                await db.runAsync("DELETE FROM messages WHERE session_key = ? AND id = ?", [key, id]);
            }
        }));
    }
    async getMessages(sessionId: string, limit = 50, offset = 0): Promise<any[]> {
        const rows = await (await database()).getAllAsync<{ raw_json: string }>(
            "SELECT raw_json FROM messages WHERE session_key = ? ORDER BY ordinal ASC, created_at ASC LIMIT ? OFFSET ?",
            [this.key(sessionId), limit, offset],
        );
        return rows.map((row) => JSON.parse(row.raw_json));
    }
    async getLatestTurnMessages(sessionId: string): Promise<any[]> {
        const key = this.key(sessionId);
        const db = await database();
        const latest = await db.getFirstAsync<{ turn_id: string }>(
            `SELECT turn_id FROM messages WHERE session_key = ? AND turn_id IS NOT NULL AND turn_id <> ''
             ORDER BY COALESCE(turn_position, 0) DESC, ordinal DESC LIMIT 1`, [key],
        );
        if (!latest) return [];
        const rows = await db.getAllAsync<{ raw_json: string }>(
            "SELECT raw_json FROM messages WHERE session_key = ? AND turn_id = ? ORDER BY ordinal ASC, created_at ASC",
            [key, latest.turn_id],
        );
        return rows.map((row) => JSON.parse(row.raw_json));
    }
    async getSessionIndex<T>(namespace: string): Promise<T[]> {
        const row = await (await database()).getFirstAsync<{ raw_json: string }>(
            "SELECT raw_json FROM indexes WHERE namespace = ?", [namespace],
        );
        return row ? JSON.parse(row.raw_json) as T[] : [];
    }
    async setSessionIndex<T>(namespace: string, sessions: T[]) {
        await write((db) => db.runAsync("INSERT OR REPLACE INTO indexes VALUES (?, ?)", [namespace, JSON.stringify(sessions)]));
    }
    private async deleteKey(db: SQLite.SQLiteDatabase, key: string) {
        await db.withTransactionAsync(async () => {
            for (const table of ["messages", "cursors", "deletions", "cache_usage"]) {
                await db.runAsync(`DELETE FROM ${table} WHERE session_key = ?`, [key]);
            }
        });
    }
    async deleteSessionData(sessionId: string) {
        const key = this.key(sessionId);
        await write((db) => this.deleteKey(db, key));
    }
}
