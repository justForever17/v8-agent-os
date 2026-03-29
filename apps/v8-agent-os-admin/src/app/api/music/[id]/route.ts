import { NextRequest, NextResponse } from "next/server";

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
        return { response, tracks: [] as Track[] };
    }
    const payload = (data && typeof data === "object" ? data : {}) as { data?: { tracks?: Track[] } };
    return {
        response,
        tracks: Array.isArray(payload.data?.tracks) ? payload.data!.tracks : [],
    };
}

async function saveMusicTracks(tracks: Track[]) {
    return proxyEngineJson("/config-registry/music", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: { tracks } }),
    });
}

type RouteContext = {
    params: Promise<{ id: string }>;
};

export async function DELETE(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { id } = await context.params;
        const { response, tracks } = await loadMusicTracks();
        if (!response.ok) {
            return NextResponse.json({ error: "Failed to load current music config" }, { status: response.status });
        }
        const { response: saveResponse } = await saveMusicTracks(tracks.filter((track) => track.id !== id));
        if (!saveResponse.ok) {
            return NextResponse.json({ error: "Failed to save music config" }, { status: saveResponse.status });
        }
        return NextResponse.json({ success: true });
    } catch (error) {
        console.error("Failed to delete track:", error);
        return NextResponse.json({ error: "Failed to delete track" }, { status: 500 });
    }
}

export async function PUT(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { id } = await context.params;
        const { isEnabled } = await req.json();
        const { response, tracks } = await loadMusicTracks();
        if (!response.ok) {
            return NextResponse.json({ error: "Failed to load current music config" }, { status: response.status });
        }

        let updatedTrack: Track | null = null;
        const nextTracks = tracks.map((track) => {
            if (track.id !== id) {
                return track;
            }
            updatedTrack = { ...track, isEnabled: Boolean(isEnabled) };
            return updatedTrack;
        });
        const { response: saveResponse } = await saveMusicTracks(nextTracks);
        if (!saveResponse.ok) {
            return NextResponse.json({ error: "Failed to save music config" }, { status: saveResponse.status });
        }
        return NextResponse.json(updatedTrack || {});
    } catch (error) {
        console.error("Failed to update track:", error);
        return NextResponse.json({ error: "Failed to update track" }, { status: 500 });
    }
}
