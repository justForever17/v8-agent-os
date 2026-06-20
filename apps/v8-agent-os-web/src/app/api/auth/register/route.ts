import { NextResponse } from "next/server";

export async function POST() {
    return NextResponse.json(
        { error: "Public registration is disabled. Use Owner bootstrap and device pairing." },
        { status: 410 },
    );
}
