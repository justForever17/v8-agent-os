import { NextRequest, NextResponse } from "next/server";
import { v4 as uuidv4 } from "uuid";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type Track = {
    id: string;
    title: string;
    url: string;
    isEnabled?: boolean;
    order?: number;
};

async function loadMusicTracks() {
    const { response, data } = await proxyEngineJson("/config-registry/music");
    if (!response.ok) {
        return { response, tracks: [] as Track[], data };
    }
    const payload = (data && typeof data === "object" ? data : {}) as { data?: { tracks?: Track[] } };
    const tracks = Array.isArray(payload.data?.tracks) ? payload.data!.tracks : [];
    return { response, tracks, data: payload };
}

async function saveMusicTracks(tracks: Track[]) {
    return proxyEngineJson("/config-registry/music", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: { tracks } }),
    });
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { response, tracks } = await loadMusicTracks();
        return NextResponse.json(
            tracks.sort((a, b) => (a.order || 0) - (b.order || 0)),
            { status: response.status, headers: { "Cache-Control": "no-store" } },
        );
    } catch (error) {
        console.error("Failed to fetch music tracks:", error);
        return NextResponse.json({ error: "Failed to fetch music tracks" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { title, url } = await req.json();
        const { response, tracks } = await loadMusicTracks();
        if (!response.ok) {
            return NextResponse.json({ error: "Failed to load current music config" }, { status: response.status });
        }

        const nextTrack: Track = {
            id: uuidv4(),
            title: String(title || "").trim(),
            url: String(url || "").trim(),
            isEnabled: true,
            order: tracks.length,
        };
        const { response: saveResponse } = await saveMusicTracks([...tracks, nextTrack]);
        if (!saveResponse.ok) {
            return NextResponse.json({ error: "Failed to save music config" }, { status: saveResponse.status });
        }
        return NextResponse.json(nextTrack);
    } catch (error) {
        console.error("Failed to create music track:", error);
        return NextResponse.json({ error: "Failed to create music track" }, { status: 500 });
    }
}
