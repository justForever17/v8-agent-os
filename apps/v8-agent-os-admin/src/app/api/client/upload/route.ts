import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        if (!req.body) {
            return NextResponse.json({ error: "缺少上传内容" }, { status: 400 });
        }
        const headers = new Headers();
        for (const name of ["content-type", "content-length", "x-v8-upload-default-source-kind"]) {
            const value = req.headers.get(name);
            if (value) headers.set(name, value);
        }
        const response = await fetchClientEngine(req, "/chat/upload", {
            method: "POST",
            headers,
            body: req.body,
            signal: req.signal,
            duplex: "half",
        } as RequestInit & { duplex: "half" });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "上传失败" },
            { status: 502 },
        );
    }
}
