import { NextRequest, NextResponse } from "next/server";
import { resolveClientUserEmail, unauthorizedClientJson } from "./client-request-auth";
import { engineFetch } from "./engine-fetch";
import { resolveEngineBaseUrl } from "./runtime-config";

export async function mutateConversation(req: NextRequest, sessionId: string, messageId?: string) {
    const userId = await resolveClientUserEmail(req);
    if (!userId) return unauthorizedClientJson();
    const input = await req.json().catch(() => null);
    if (!input || typeof input !== "object" || Array.isArray(input)) return NextResponse.json({ error: "invalid_request" }, { status: 400 });
    const body = messageId ? {
        content: input.content, expectedMessageVersion: input.expectedMessageVersion,
        expectedTranscriptRevision: input.expectedTranscriptRevision, tailPolicy: input.tailPolicy, userId,
    } : {
        turnId: input.turnId, expectedMessageVersion: input.expectedMessageVersion,
        expectedTranscriptRevision: input.expectedTranscriptRevision, messageId: input.messageId, content: input.content, userId,
    };
    try {
        const suffix = messageId ? `messages/${encodeURIComponent(messageId)}` : "branches";
        const response = await engineFetch(`${resolveEngineBaseUrl()}/sessions/${encodeURIComponent(sessionId)}/${suffix}`, {
            method: messageId ? "PATCH" : "POST",
            headers: { "Content-Type": "application/json", "x-v8-agent-os-user-email": userId },
            body: JSON.stringify(body), cache: "no-store",
        });
        return NextResponse.json(await response.json().catch(() => ({ error: "invalid_engine_response" })), { status: response.status });
    } catch {
        return NextResponse.json({ error: "engine_unavailable" }, { status: 502 });
    }
}
