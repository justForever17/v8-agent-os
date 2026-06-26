import * as SQLite from 'expo-sqlite';

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
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_messages_session_ordinal ON local_messages (session_id, ordinal);
                CREATE TABLE IF NOT EXISTS local_sync_cursors (
                    session_id TEXT PRIMARY KEY NOT NULL,
                    sync_cursor TEXT NOT NULL
                );
            `);
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
        
        const statement = await this.db!.prepareAsync(
            'INSERT OR REPLACE INTO local_messages (id, session_id, ordinal, created_at, raw_json) VALUES (?, ?, ?, ?, ?)'
        );
        
        try {
            for (const msg of messages) {
                await statement.executeAsync([
                    msg.id || '',
                    sessionId,
                    msg.ordinal || 0,
                    msg.createdAt || msg.created_at || '',
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
        
        const placeholders = messageIds.map(() => '?').join(',');
        await this.db!.runAsync(
            `DELETE FROM local_messages WHERE session_id = ? AND id IN (${placeholders})`,
            [sessionId, ...messageIds]
        );
    }

    async getMessages(sessionId: string, limit: number = 50, offset: number = 0): Promise<any[]> {
        if (!this.db) await this.init();
        const rows = await this.db!.getAllAsync<{ raw_json: string }>(
            'SELECT raw_json FROM local_messages WHERE session_id = ? ORDER BY ordinal ASC, created_at ASC LIMIT ? OFFSET ?',
            [sessionId, limit, offset]
        );
        return rows.map(r => JSON.parse(r.raw_json));
    }
}

export const localDatabase = new LocalDatabaseService();
