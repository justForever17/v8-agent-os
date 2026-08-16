import fs from 'fs';
import path from 'path';
import os from 'os';
import { randomUUID } from 'node:crypto';

export class AdminStorageUnavailableError extends Error {
    readonly code = 'owner_state_unavailable';

    constructor(readonly filename: string, operation: 'read' | 'write') {
        super(`Admin storage ${operation} failed for ${filename}`);
        this.name = 'AdminStorageUnavailableError';
    }
}

export const isAdminStorageUnavailableError = (error: unknown): error is AdminStorageUnavailableError => (
    error instanceof AdminStorageUnavailableError
);

export const getBaseDir = () => {
    const configured = String(process.env.V8_AGENT_OS_HOME || '').trim();
    return configured ? path.resolve(configured) : path.join(os.homedir(), '.v8-agent-os');
};
const LEGACY_STORAGE_FILES = ['sessions.json', 'files.json', 'codes.json'] as const;

const ensureDir = (dir: string) => {
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
};

const ensureFile = (file: string, defaultContent: string) => {
    if (!fs.existsSync(file)) {
        fs.writeFileSync(file, defaultContent, 'utf-8');
    }
};

const cleanupLegacyFiles = (baseDir: string) => {
    for (const filename of LEGACY_STORAGE_FILES) {
        const filePath = path.join(baseDir, filename);
        if (!fs.existsSync(filePath)) {
            continue;
        }
        try {
            fs.unlinkSync(filePath);
        } catch (e) {
            console.warn(`Failed to remove legacy admin storage file ${filename}:`, e);
        }
    }
};

export const initializeStorage = () => {
    const baseDir = getBaseDir();
    ensureDir(baseDir);
    cleanupLegacyFiles(baseDir);
    ensureFile(path.join(baseDir, 'users.json'), JSON.stringify({ users: [] }, null, 2));
};

export const readJson = <T>(filename: string, defaultValue: T): T => {
    try {
        const filePath = path.join(getBaseDir(), filename);
        if (!fs.existsSync(filePath)) return defaultValue;
        const content = fs.readFileSync(filePath, 'utf-8');
        return JSON.parse(content) as T;
    } catch (e) {
        console.error(`Failed to read ${filename}:`, e);
        return defaultValue;
    }
};

export const readJsonStrict = <T>(filename: string): T => {
    const filePath = path.join(getBaseDir(), filename);
    try {
        return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as T;
    } catch (error) {
        console.error(`Failed to read required admin storage ${filename}:`, error);
        throw new AdminStorageUnavailableError(filename, 'read');
    }
};

export const writeJson = <T>(filename: string, data: T) => {
    try {
        const filePath = path.join(getBaseDir(), filename);
        fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
    } catch (e) {
        console.error(`Failed to write ${filename}:`, e);
    }
};

export const writeJsonStrict = <T>(filename: string, data: T) => {
    const filePath = path.join(getBaseDir(), filename);
    const directory = path.dirname(filePath);
    const temporaryFile = path.join(directory, `.${path.basename(filePath)}.${process.pid}.${randomUUID()}.tmp`);
    let temporaryFileDescriptor: number | null = null;
    try {
        ensureDir(directory);
        let temporaryFileMode = 0o600;
        try {
            temporaryFileMode = fs.statSync(filePath).mode & 0o777;
        } catch (statError) {
            if ((statError as NodeJS.ErrnoException)?.code !== 'ENOENT') {
                throw statError;
            }
        }
        temporaryFileDescriptor = fs.openSync(temporaryFile, 'wx', temporaryFileMode);
        fs.writeFileSync(temporaryFileDescriptor, JSON.stringify(data, null, 2), 'utf-8');
        fs.fsyncSync(temporaryFileDescriptor);
        fs.closeSync(temporaryFileDescriptor);
        temporaryFileDescriptor = null;
        fs.renameSync(temporaryFile, filePath);
    } catch (error) {
        if (temporaryFileDescriptor !== null) {
            try {
                fs.closeSync(temporaryFileDescriptor);
            } catch {}
        }
        try {
            fs.unlinkSync(temporaryFile);
        } catch (cleanupError) {
            if ((cleanupError as NodeJS.ErrnoException)?.code !== 'ENOENT') {
                console.warn(`Failed to clean temporary admin storage ${filename}:`, cleanupError);
            }
        }
        console.error(`Failed to write required admin storage ${filename}:`, error);
        throw new AdminStorageUnavailableError(filename, 'write');
    }
};

export const appendJsonl = <T>(filename: string, data: T) => {
    try {
        const filePath = path.join(getBaseDir(), filename);
        ensureDir(path.dirname(filePath));
        fs.appendFileSync(filePath, JSON.stringify(data) + '\n', 'utf-8');
    } catch (e) {
        console.error(`Failed to append to JSONL ${filename}:`, e);
    }
};

export const readJsonl = <T>(filename: string): T[] => {
    try {
        const filePath = path.join(getBaseDir(), filename);
        if (!fs.existsSync(filePath)) return [];
        const content = fs.readFileSync(filePath, 'utf-8');
        return content.split('\n').filter(line => line.trim()).map(line => {
             try { return JSON.parse(line) as T; } catch { return null; }
        }).filter(Boolean) as T[];
    } catch (e) {
        console.error(`Failed to read JSONL ${filename}:`, e);
        return [];
    }
};

// Auto-initialize when required
initializeStorage();
