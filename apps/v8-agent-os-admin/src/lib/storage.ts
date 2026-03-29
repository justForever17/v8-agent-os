import fs from 'fs';
import path from 'path';
import os from 'os';

export const getBaseDir = () => path.join(os.homedir(), '.v8-agent-os');
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

export const writeJson = <T>(filename: string, data: T) => {
    try {
        const filePath = path.join(getBaseDir(), filename);
        fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
    } catch (e) {
        console.error(`Failed to write ${filename}:`, e);
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
