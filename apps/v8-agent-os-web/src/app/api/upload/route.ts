import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const formData = await req.formData();

        // Headers manipulation: Next.js/Browser fetch automatically sets Content-Type boundary for FormData
        // But we are proxying. We need to pass the incoming body stream or recreate FormData?
        // Node/Next Fetch with FormData is tricky.

        const res = await fetch(`${adminApiBaseUrl}/upload`, {
            method: "POST",
            headers: {
                // Do NOT set Content-Type here, let fetch handle the boundary
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email
            },
            body: formData
        });

        if (!res.ok) {
            const errorText = await res.text();
            console.error("Upload proxy failed:", errorText);
            return NextResponse.json({ error: "Upload failed" }, { status: res.status });
        }

        const data = await res.json();
        // Normalize response to what InputArea expects (Admin returns file object with 'url')
        // InputArea (old) expected { url, publicUrl }.
        // InputArea (new) will expect { url: finalUrl }.

        // Admin returns the File object (Prisma). It has 'url' property.
        return NextResponse.json({ url: data.url, publicUrl: data.url });

    } catch (error) {
        console.error("Upload Proxy Error:", error);
        return NextResponse.json({ error: "Upload Service Unavailable" }, { status: 502 });
    }
}
