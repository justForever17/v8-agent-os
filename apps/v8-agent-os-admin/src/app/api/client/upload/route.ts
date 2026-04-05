import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        const formData = await req.formData();
        const response = await fetchClientAdmin(req, "/upload", {
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
