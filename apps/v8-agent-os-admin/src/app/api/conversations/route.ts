import { NextRequest, NextResponse } from "next/server";
import {
    normalizeAuthoritativeSessionHistoryList,
    normalizeAuthoritativeSessionHistoryRecord,
} from "@v8/session-realtime/history";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const res = await fetch(`${ENGINE_URL}/sessions`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!res.ok) {
            console.error("Failed to fetch sessions from Python engine:", await res.text());
            return NextResponse.json([]);
        }

        const data = await res.json().catch(() => ({}));
        const sessions = normalizeAuthoritativeSessionHistoryList(
            Array.isArray(data.sessions) ? data.sessions : [],
        );
        return NextResponse.json(sessions);
    } catch (error) {
        console.error("Error communicating with Python engine:", error);
        return NextResponse.json([]);
    }
}

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const body = await req.json().catch(() => ({}));
        const res = await fetch(`${ENGINE_URL}/sessions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title: body?.title || "New Chat",
                userId: userEmail,
                projectId: body?.projectId,
                workspaceId: body?.workspaceId,
                workspacePath: body?.workspacePath,
                scopeHint: body?.scopeHint,
                scopeMode: body?.scopeMode || "mixed",
            }),
        });

        if (!res.ok) {
            throw new Error(`Engine returned ${res.status}`);
        }

        return NextResponse.json(
            normalizeAuthoritativeSessionHistoryRecord(await res.json().catch(() => ({}))),
        );
    } catch (error) {
        console.error("Error creating session in Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const listRes = await fetch(`${ENGINE_URL}/sessions`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        if (!listRes.ok) {
            throw new Error(`Failed to list sessions: ${listRes.status}`);
        }
        const listData = await listRes.json().catch(() => ({}));
        const sessions = Array.isArray(listData?.sessions) ? listData.sessions : [];

        let deleted = 0;
        for (const sessionRow of sessions) {
            const sessionId = String(sessionRow?.id || "");
            if (!sessionId) continue;
            const deleteRes = await fetch(`${ENGINE_URL}/sessions/${sessionId}`, {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
            });
            if (deleteRes.ok) {
                deleted += 1;
            }
        }

        return NextResponse.json({ success: true, deleted });
    } catch (error) {
        console.error("Error clearing sessions in Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
