import { NextRequest, NextResponse } from "next/server";
import {
    normalizeAuthoritativeSessionHistoryList,
    normalizeAuthoritativeSessionHistoryRecord,
} from "@v8/session-realtime/history";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { jsonSizeBytes, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { readSessionStateError } from "@/lib/server/session-state-error";

const ENGINE_URL = resolveEngineBaseUrl();
const ENGINE_NOW_HEADER = "x-v8-engine-now";

export async function GET(req: NextRequest) {
    const startedAt = Date.now();
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        let response = await fetch(`${ENGINE_URL}/sessions/quick-index`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!response.ok) {
            console.warn("[Client Conversations] Quick index unavailable, falling back to live sessions:", response.status);
            response = await fetch(`${ENGINE_URL}/sessions`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
            });
        }

        if (!response.ok) {
            const failure = await readSessionStateError(response);
            console.error("[Client Conversations] Failed to fetch sessions:", failure.error.code);
            return NextResponse.json(failure, { status: response.status >= 500 ? 503 : response.status });
        }

        const data = await response.json().catch(() => ({}));
        const sessions = normalizeAuthoritativeSessionHistoryList(
            Array.isArray(data.sessions) ? data.sessions : [],
        );
        const elapsedMs = Date.now() - startedAt;
        const payloadBytes = jsonSizeBytes(sessions);
        recordAdminApiMetric({
            route: "client.conversations.list",
            status: response.status,
            elapsedMs,
            payloadBytes,
        });
        return NextResponse.json(sessions, {
            headers: {
                ...(response.headers.get(ENGINE_NOW_HEADER)
                    ? { [ENGINE_NOW_HEADER]: response.headers.get(ENGINE_NOW_HEADER)! }
                    : {}),
                ...(data?.degraded === true ? {
                    "x-v8-state-degraded": "1",
                    "x-v8-state-degradation-code": String(data?.degradation?.code || "state_database_unavailable"),
                } : {}),
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(payloadBytes),
            },
        });
    } catch (error) {
        console.error("[Client Conversations] Engine communication failed:", error);
        return NextResponse.json({
            error: {
                code: "state_database_unavailable",
                message: "本地运行状态数据库暂时不可用，已有状态未被覆盖。请稍后重试。",
                retryable: true,
            },
        }, { status: 503 });
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
        const metadata = body?.metadata && typeof body.metadata === "object" && !Array.isArray(body.metadata)
            ? body.metadata
            : undefined;
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
                externalSurface: body?.externalSurface,
                clientGroup: body?.clientGroup,
                source: body?.source,
                metadata,
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
