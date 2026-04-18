import { NextRequest, NextResponse } from "next/server";
import {
    normalizeAuthoritativeSessionHistoryList,
    normalizeAuthoritativeSessionHistoryRecord,
} from "@v8/session-realtime/history";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();
const ENGINE_NOW_HEADER = "x-v8-engine-now";

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const response = await fetch(`${ENGINE_URL}/sessions`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!response.ok) {
            console.error("[Client Conversations] Failed to fetch sessions:", await response.text());
            return NextResponse.json([]);
        }

        const data = await response.json().catch(() => ({}));
        const sessions = normalizeAuthoritativeSessionHistoryList(
            Array.isArray(data.sessions) ? data.sessions : [],
        );
        return NextResponse.json(sessions, {
            headers: response.headers.get(ENGINE_NOW_HEADER)
                ? { [ENGINE_NOW_HEADER]: response.headers.get(ENGINE_NOW_HEADER)! }
                : undefined,
        });
    } catch (error) {
        console.error("[Client Conversations] Engine communication failed:", error);
        return NextResponse.json([]);
    }
}

export async function POST(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const body = await req.json().catch(() => ({}));
        const nextTitle = typeof body?.title === "string" ? body.title : undefined;
        const response = await fetch(`${ENGINE_URL}/sessions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ...(nextTitle !== undefined ? { title: nextTitle } : {}),
                userId: userEmail,
                projectId: body?.projectId,
                workspaceId: body?.workspaceId,
                workspacePath: body?.workspacePath,
                scopeHint: body?.scopeHint,
                scopeMode: body?.scopeMode || "explicit",
            }),
        });

        if (!response.ok) {
            throw new Error(`Engine returned ${response.status}`);
        }

        return NextResponse.json(
            normalizeAuthoritativeSessionHistoryRecord(await response.json().catch(() => ({}))),
            {
                headers: response.headers.get(ENGINE_NOW_HEADER)
                    ? { [ENGINE_NOW_HEADER]: response.headers.get(ENGINE_NOW_HEADER)! }
                    : undefined,
            },
        );
    } catch (error) {
        console.error("[Client Conversations] Create session failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const listResponse = await fetch(`${ENGINE_URL}/sessions`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        if (!listResponse.ok) {
            throw new Error(`Failed to list sessions: ${listResponse.status}`);
        }

        const listData = await listResponse.json().catch(() => ({}));
        const sessions = Array.isArray(listData?.sessions) ? listData.sessions : [];
        let deleted = 0;

        for (const sessionRow of sessions) {
            const sessionId = String(sessionRow?.id || "");
            if (!sessionId) continue;
            const deleteResponse = await fetch(`${ENGINE_URL}/sessions/${sessionId}`, {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
            });
            if (deleteResponse.ok) {
                deleted += 1;
            }
        }

        return NextResponse.json({ success: true, deleted });
    } catch (error) {
        console.error("[Client Conversations] Clear sessions failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
