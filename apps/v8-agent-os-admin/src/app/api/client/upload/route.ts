import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        const formData = await req.formData();
        const response = await fetchClientEngine(req, "/chat/upload", {
            method: "POST",
            body: formData,
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "上传失败" },
            { status: 502 },
        );
    }
}
