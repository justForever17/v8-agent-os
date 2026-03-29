import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest) {
    let userEmail: string | undefined | null;

    // 1. Try Service Auth first (From Web App)
    userEmail = await verifyServiceAuth(req);

    // 2. Fallback to Admin Session (Direct Admin Access)
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }

    if (!userEmail) {
        return NextResponse.json([]);
    }

    try {
        const res = await fetch(`${ENGINE_URL}/sessions`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store'
        });
        
        if (!res.ok) {
            console.error("Failed to fetch sessions from Python engine:", await res.text());
            return NextResponse.json([]);
        }

        const data = await res.json();
        // The frontend expects the userId to match their current logged in user.
        // For local usage (B2B/DevTool), we might just return all, or filter by a default user.
        // Currently we just return all sessions for the command center unified view.
        return NextResponse.json(data.sessions || []);
    } catch (error) {
        console.error("Error communicating with Python engine:", error);
        return NextResponse.json([]);
    }
}

export async function POST(req: NextRequest) {
    let userEmail: string | undefined | null;
    userEmail = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }

    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const body = await req.json().catch(() => ({}));
        const res = await fetch(`${ENGINE_URL}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: body?.title || "New Chat",
                userId: userEmail,
                projectId: body?.projectId,
                workspaceId: body?.workspaceId,
                workspacePath: body?.workspacePath,
                scopeHint: body?.scopeHint,
                scopeMode: body?.scopeMode || "mixed",
            })
        });
        
        if (!res.ok) {
            throw new Error(`Engine returned ${res.status}`);
        }

        const newSession = await res.json();
        // Ensure returning camelCase for frontend 
        return NextResponse.json({
            id: newSession.id,
            userId: newSession.userId || newSession.user_id,
            title: newSession.title,
            createdAt: newSession.createdAt || newSession.created_at,
            updatedAt: newSession.updatedAt || newSession.updated_at
        });
    } catch (error) {
        console.error("Error creating session in Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest) {
    let userEmail: string | undefined | null;
    userEmail = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }

    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
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
