import * as SQLite from 'expo-sqlite';

const MESSAGE_DELETIONS_CURSOR_RESET_MIGRATION = 'message_deletions_cursor_reset_v1';
const COMPACT_MESSAGE_SURFACE_MIGRATION = 'compact_message_surface_v1';
const MAX_LOCAL_MESSAGE_JSON_CHARS = 1_000_000;

export function buildLocalSessionIndexNamespace(adminBaseUrl: string, ownerId: string) {
    const normalizedBaseUrl = String(adminBaseUrl || '').trim().replace(/\/+$/, '').toLowerCase();
    const normalizedOwner = String(ownerId || '').trim().toLowerCase() || 'local';
    return `${normalizedBaseUrl || 'local'}::${normalizedOwner}`;
}

export type LocalMessage = {
    id: string;
    session_id: string;
    ordinal: number;
    created_at: string;
    raw_json: string;
};

class LocalDatabaseService {
    private db: SQLite.SQLiteDatabase | null = null;
    private initialized = false;
    private initPromise: Promise<void> | null = null;

    async init() {
        if (this.initialized) return;
        if (this.initPromise) return this.initPromise;

        this.initPromise = (async () => {
            this.db = await SQLite.openDatabaseAsync('v8_agent_os.db');
            await this.db.execAsync(`
                CREATE TABLE IF NOT EXISTS local_messages (
                    id TEXT PRIMARY KEY NOT NULL,
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    turn_id TEXT,
                    turn_position INTEGER,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_messages_session_ordinal ON local_messages (session_id, ordinal);
                CREATE TABLE IF NOT EXISTS local_sync_cursors (
                    session_id TEXT PRIMARY KEY NOT NULL,
                    sync_cursor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_message_deletions (
                    session_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS local_schema_migrations (
                    key TEXT PRIMARY KEY NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_session_indexes (
                    cache_namespace TEXT PRIMARY KEY NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            `);
            const messageColumns = await this.db.getAllAsync<{ name: string }>('PRAGMA table_info(local_messages)');
            const messageColumnNames = new Set(messageColumns.map((column) => column.name));
            if (!messageColumnNames.has('turn_id')) {
                await this.db.execAsync('ALTER TABLE local_messages ADD COLUMN turn_id TEXT');
            }
            if (!messageColumnNames.has('turn_position')) {
                await this.db.execAsync('ALTER TABLE local_messages ADD COLUMN turn_position INTEGER');
            }
            await this.db.execAsync('CREATE INDEX IF NOT EXISTS idx_local_messages_session_turn ON local_messages (session_id, turn_position, ordinal)');
            const migration = await this.db.getFirstAsync<{ key: string }>(
                'SELECT key FROM local_schema_migrations WHERE key = ?',
                [MESSAGE_DELETIONS_CURSOR_RESET_MIGRATION],
            );
            if (!migration) {
                await this.db.runAsync('DELETE FROM local_sync_cursors');
                await this.db.runAsync(
                    'INSERT OR REPLACE INTO local_schema_migrations (key, applied_at) VALUES (?, ?)',
                    [MESSAGE_DELETIONS_CURSOR_RESET_MIGRATION, new Date().toISOString()],
                );
            }
            const compactSurfaceMigration = await this.db.getFirstAsync<{ key: string }>(
                'SELECT key FROM local_schema_migrations WHERE key = ?',
                [COMPACT_MESSAGE_SURFACE_MIGRATION],
            );
            if (!compactSurfaceMigration) {
                await this.db.runAsync(
                    `DELETE FROM local_sync_cursors
                     WHERE session_id IN (
                         SELECT DISTINCT session_id
                         FROM local_messages
                         WHERE LENGTH(raw_json) > ?
                     )`,
                    [MAX_LOCAL_MESSAGE_JSON_CHARS],
                );
                await this.db.runAsync(
                    'DELETE FROM local_messages WHERE LENGTH(raw_json) > ?',
                    [MAX_LOCAL_MESSAGE_JSON_CHARS],
                );
                await this.db.runAsync(
                    'INSERT OR REPLACE INTO local_schema_migrations (key, applied_at) VALUES (?, ?)',
                    [COMPACT_MESSAGE_SURFACE_MIGRATION, new Date().toISOString()],
                );
            }
            this.initialized = true;
        })();
        return this.initPromise;
    }

    async getSyncCursor(sessionId: string): Promise<string> {
        if (!this.db) await this.init();
        const row = await this.db!.getFirstAsync<{ sync_cursor: string }>(
            'SELECT sync_cursor FROM local_sync_cursors WHERE session_id = ?',
            [sessionId]
        );
        return row ? row.sync_cursor : '';
    }

    async setSyncCursor(sessionId: string, syncCursor: string) {
        if (!this.db) await this.init();
        await this.db!.runAsync(
            'INSERT OR REPLACE INTO local_sync_cursors (session_id, sync_cursor) VALUES (?, ?)',
            [sessionId, syncCursor]
        );
    }

    async upsertMessages(sessionId: string, messages: any[]) {
        if (!this.db) await this.init();
        if (messages.length === 0) return;

        const messageIds = messages
            .map((msg) => String(msg?.id || '').trim())
            .filter(Boolean);
        const deletedIds = new Set<string>();
        if (messageIds.length > 0) {
            const placeholders = messageIds.map(() => '?').join(',');
            const rows = await this.db!.getAllAsync<{ message_id: string }>(
                `SELECT message_id FROM local_message_deletions WHERE session_id = ? AND message_id IN (${placeholders})`,
                [sessionId, ...messageIds],
            );
            for (const row of rows) {
                if (row.message_id) {
                    deletedIds.add(row.message_id);
                }
            }
        }
        
        const statement = await this.db!.prepareAsync(
            'INSERT OR REPLACE INTO local_messages (id, session_id, ordinal, created_at, turn_id, turn_position, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)'
        );
        
        try {
            for (const msg of messages) {
                const messageId = String(msg?.id || '').trim();
                if (!messageId || deletedIds.has(messageId)) {
                    continue;
                }
                await statement.executeAsync([
                    messageId,
                    sessionId,
                    msg.ordinal || 0,
                    msg.createdAt || msg.created_at || '',
                    String(msg.turnId || msg.turn_id || '').trim() || null,
                    Number(msg.turnPosition || msg.turn_position || 0) || null,
                    JSON.stringify(msg)
                ]);
            }
        } finally {
            await statement.finalizeAsync();
        }
    }

    async deleteMessages(sessionId: string, messageIds: string[]) {
        if (!this.db) await this.init();
        if (messageIds.length === 0) return;

        const normalizedMessageIds = messageIds.map((id) => String(id || '').trim()).filter(Boolean);
        if (normalizedMessageIds.length === 0) return;

        const deletedAt = new Date().toISOString();
        const tombstoneStatement = await this.db!.prepareAsync(
            'INSERT OR REPLACE INTO local_message_deletions (session_id, message_id, deleted_at) VALUES (?, ?, ?)'
        );
        try {
            for (const messageId of normalizedMessageIds) {
                await tombstoneStatement.executeAsync([sessionId, messageId, deletedAt]);
            }
        } finally {
            await tombstoneStatement.finalizeAsync();
        }
        
        const placeholders = normalizedMessageIds.map(() => '?').join(',');
        await this.db!.runAsync(
            `DELETE FROM local_messages WHERE session_id = ? AND id IN (${placeholders})`,
            [sessionId, ...normalizedMessageIds]
        );
    }

    async getMessages(sessionId: string, limit: number = 50, offset: number = 0): Promise<any[]> {
        if (!this.db) await this.init();
        const rows = await this.db!.getAllAsync<{ raw_json: string }>(
            `SELECT raw_json
             FROM local_messages
             WHERE session_id = ?
               AND id NOT IN (
                   SELECT message_id
                   FROM local_message_deletions
                   WHERE session_id = ?
               )
             ORDER BY ordinal ASC, created_at ASC
             LIMIT ? OFFSET ?`,
            [sessionId, sessionId, limit, offset]
        );
        return rows.map(r => JSON.parse(r.raw_json));
    }

    async getLatestTurnMessages(sessionId: string): Promise<any[]> {
        if (!this.db) await this.init();
        const latestTurn = await this.db!.getFirstAsync<{ turn_id: string }>(
            `SELECT turn_id
             FROM local_messages
             WHERE session_id = ? AND turn_id IS NOT NULL AND turn_id <> ''
               AND id NOT IN (
                   SELECT message_id FROM local_message_deletions WHERE session_id = ?
               )
             ORDER BY COALESCE(turn_position, 0) DESC, ordinal DESC
             LIMIT 1`,
            [sessionId, sessionId],
        );
        if (!latestTurn?.turn_id) {
            return [];
        }
        const rows = await this.db!.getAllAsync<{ raw_json: string }>(
            `SELECT raw_json
             FROM local_messages
             WHERE session_id = ? AND turn_id = ?
               AND id NOT IN (
                   SELECT message_id FROM local_message_deletions WHERE session_id = ?
               )
             ORDER BY ordinal ASC, created_at ASC`,
            [sessionId, latestTurn.turn_id, sessionId],
        );
        return rows.map((row) => JSON.parse(row.raw_json));
    }

    async getSessionIndex<T>(cacheNamespace: string): Promise<T[]> {
        if (!this.db) await this.init();
        const row = await this.db!.getFirstAsync<{ raw_json: string }>(
            'SELECT raw_json FROM local_session_indexes WHERE cache_namespace = ?',
            [cacheNamespace],
        );
        if (!row?.raw_json) return [];
        try {
            const parsed = JSON.parse(row.raw_json);
            return Array.isArray(parsed) ? parsed as T[] : [];
        } catch {
            return [];
        }
    }

    async setSessionIndex<T>(cacheNamespace: string, sessions: T[]) {
        if (!this.db) await this.init();
        await this.db!.runAsync(
            `INSERT OR REPLACE INTO local_session_indexes (cache_namespace, raw_json, updated_at)
             VALUES (?, ?, ?)`,
            [cacheNamespace, JSON.stringify(sessions), new Date().toISOString()],
        );
    }

    async deleteSessionData(sessionId: string) {
        if (!this.db) await this.init();
        await this.db!.withTransactionAsync(async () => {
            await this.db!.runAsync('DELETE FROM local_messages WHERE session_id = ?', [sessionId]);
            await this.db!.runAsync('DELETE FROM local_sync_cursors WHERE session_id = ?', [sessionId]);
            await this.db!.runAsync('DELETE FROM local_message_deletions WHERE session_id = ?', [sessionId]);
        });
    }
}

export const localDatabase = new LocalDatabaseService();
