import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { applyCanonicalSourceGroup, deriveCanonicalSourceGroup } from "@/lib/server/source-group";

const ENGINE_URL = resolveEngineBaseUrl();

function parseMetadata(metadata: unknown): Record<string, unknown> {
    if (!metadata) return {};
    if (typeof metadata === "string") {
        try {
            return JSON.parse(metadata) as Record<string, unknown>;
        } catch {
            return {};
        }
    }
    if (typeof metadata === "object") {
        return metadata as Record<string, unknown>;
    }
    return {};
}

function coerceString(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function deriveScopeTags(parsedMetadata: Record<string, unknown>, record: Record<string, unknown>) {
    const explicitScopeTags = [
        ...(Array.isArray(parsedMetadata.scopeTags) ? parsedMetadata.scopeTags : []),
        ...(Array.isArray(parsedMetadata.scope_tags) ? parsedMetadata.scope_tags : []),
        ...(Array.isArray(record.scopeTags) ? record.scopeTags : []),
        ...(Array.isArray(record.scope_tags) ? record.scope_tags : []),
    ]
        .map((value) => String(value || "").trim())
        .filter(Boolean);

    if (explicitScopeTags.length > 0) {
        return Array.from(new Set(explicitScopeTags));
    }

    const tags: string[] = [];
    const projectId = parsedMetadata.project_id || parsedMetadata.projectId || record.projectId || record.project_id;
    const resolvedScope = parsedMetadata.resolved_scope || parsedMetadata.resolvedScope || record.resolvedScope || record.resolved_scope;
    for (const value of [projectId, resolvedScope]) {
        const normalized = String(value || "").trim();
        if (normalized && !tags.includes(normalized)) {
            tags.push(normalized);
        }
    }
    return tags;
}

function normalizeConversationSummary(raw: unknown) {
    const record = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const canonicalRecord = applyCanonicalSourceGroup(record);
    const parsedMetadata = parseMetadata(canonicalRecord.metadata);
    const previewExcerpt = coerceString(canonicalRecord.previewExcerpt) || coerceString(canonicalRecord.lastNarrativeExcerpt);

    return {
        ...canonicalRecord,
        parsedMetadata,
        sourceGroup: deriveCanonicalSourceGroup(canonicalRecord),
        channelType: coerceString(canonicalRecord.channelType),
        channelName: coerceString(canonicalRecord.channelName),
        channelDomain: coerceString(canonicalRecord.channelDomain),
        chatType: coerceString(canonicalRecord.chatType),
        accountId: coerceString(canonicalRecord.accountId),
        defaultAccount: coerceString(canonicalRecord.defaultAccount),
        scopeTags: deriveScopeTags(parsedMetadata, canonicalRecord),
        previewExcerpt,
        lastNarrativeExcerpt: coerceString(canonicalRecord.lastNarrativeExcerpt) || previewExcerpt,
        pendingApprovalCount: Number(canonicalRecord.pendingApprovalCount || 0) || 0,
        hasPendingApproval: Boolean(canonicalRecord.hasPendingApproval),
        ownerRuntime: coerceString(canonicalRecord.ownerRuntime),
        currentStepTitle: coerceString(canonicalRecord.currentStepTitle),
        lastRuntimeSummary: coerceString(canonicalRecord.lastRuntimeSummary),
        workflowStatus: coerceString(canonicalRecord.workflowStatus),
        statusLabel: coerceString(canonicalRecord.statusLabel),
    };
}

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

        const data = await response.json();
        const sessions = Array.isArray(data.sessions) ? data.sessions.map((item: unknown) => normalizeConversationSummary(item)) : [];
        return NextResponse.json(sessions);
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
                scopeMode: body?.scopeMode || "mixed",
            }),
        });

        if (!response.ok) {
            throw new Error(`Engine returned ${response.status}`);
        }

        const newSession = await response.json();
        return NextResponse.json(normalizeConversationSummary({
            id: newSession.id,
            userId: newSession.userId || newSession.user_id,
            title: newSession.title,
            createdAt: newSession.createdAt || newSession.created_at,
            updatedAt: newSession.updatedAt || newSession.updated_at,
            metadata: newSession.metadata,
            source: newSession.source,
        }));
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
