
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import fs from "fs";
import path from "path";

// Helper: Ensure we are accessing safe paths
function isSafePath(base: string, target: string) {
    const relative = path.relative(base, target);
    return relative && !relative.startsWith('..') && !path.isAbsolute(relative);
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
    const resolvedParams = await params;
    // 1. Authentication Check
    const session = await auth();
    if (!session || !session.user || !session.user.id) {
        return new NextResponse("Unauthorized", { status: 401 });
    }

    const pathSegments = resolvedParams.path;

    // 2. Authorization & Isolation Check
    // We strictly enforce that the file must reside in storage/jt/<user_id>/...
    // The URL structure is /api/files/<user_id>/filename...
    // Users can only access their own user directory.

    const requestUserId = pathSegments[0];

    // Admin Override (Optional, kept strict for now)
    // Admin Override (Optional, kept strict for now)
    // const isAdmin = (session.user as any).role === "ADMIN";

    // Strict isolation: Even admins should act as users unless explicitly debugging?
    // Let's enforce strict Owner Access for now. 
    if (requestUserId !== session.user.id) {
        // If strict, reject. 
        // NOTE: If we want to allow sharing, we'd need a DB check for permission.
        return new NextResponse("Forbidden: Access denied to other user's workspace", { status: 403 });
    }

    // 3. Resolve Path
    const storageRoot = path.resolve(process.cwd(), "storage/jt");
    const requestedPath = path.join(storageRoot, ...pathSegments);

    // 4. Input Validation (Path Traversal)
    if (!isSafePath(storageRoot, requestedPath)) {
        console.warn(`[Security] Path traversal attempt detected: ${requestUserId} -> ${requestedPath}`);
        return new NextResponse("Invalid path", { status: 400 });
    }

    if (!fs.existsSync(requestedPath)) {
        return new NextResponse("File not found", { status: 404 });
    }

    // Check if directory
    if (fs.statSync(requestedPath).isDirectory()) {
        return new NextResponse("Cannot download directory", { status: 400 });
    }

    // 5. Stream Response
    // We use Node.js streams which Next.js App Router supports via NextResponse
    // Need to cast to any/ReadableStream because of type mismatches in some Next.js versions
    const fileStream = fs.createReadStream(requestedPath);

    const stat = fs.statSync(requestedPath);
    const filename = pathSegments[pathSegments.length - 1];

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return new NextResponse(fileStream as any, {
        headers: {
            'Content-Length': stat.size.toString(),
            'Content-Disposition': `attachment; filename="${filename}"`,
            'Content-Type': 'application/octet-stream' // Or detect mime type
        }
    });
}
