import fs from "fs/promises";
import os from "os";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

const VOICE_SAMPLE_DIR = path.join(os.homedir(), ".v8-agent-os", "tmp", "voice-samples");

const CONTENT_TYPES: Record<string, string> = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
};

async function cleanupExpiredVoiceSamples() {
    const now = Date.now();
    let entries: string[] = [];
    try {
        entries = await fs.readdir(VOICE_SAMPLE_DIR);
    } catch {
        return;
    }
    await Promise.all(entries.map(async (name) => {
        const expiresAt = Number(name.split("-", 1)[0]);
        if (!Number.isFinite(expiresAt) || expiresAt > now) return;
        try {
            await fs.rm(path.join(VOICE_SAMPLE_DIR, name), { force: true });
        } catch {
            // Best effort cleanup only.
        }
    }));
}

function isSafeToken(value: string) {
    return /^[a-f0-9]{48}$/i.test(value);
}

export async function GET(
    _req: NextRequest,
    { params }: { params: Promise<{ token: string }> },
) {
    const resolvedParams = await params;
    const token = String(resolvedParams.token || "").trim();
    if (!isSafeToken(token)) {
        return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    await cleanupExpiredVoiceSamples();

    let entries: string[] = [];
    try {
        entries = await fs.readdir(VOICE_SAMPLE_DIR);
    } catch {
        return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    const filename = entries.find((name) => name.includes(`-${token}`));
    if (!filename) {
        return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    const expiresAt = Number(filename.split("-", 1)[0]);
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
        return NextResponse.json({ error: "Expired" }, { status: 410 });
    }

    const fullPath = path.join(VOICE_SAMPLE_DIR, filename);
    const buffer = await fs.readFile(fullPath);
    const extension = path.extname(filename).toLowerCase();
    return new NextResponse(buffer, {
        headers: {
            "Content-Type": CONTENT_TYPES[extension] || "application/octet-stream",
            "Cache-Control": "private, max-age=0, no-store",
        },
    });
}
