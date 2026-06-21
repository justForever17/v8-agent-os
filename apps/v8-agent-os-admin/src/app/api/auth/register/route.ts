import { NextResponse } from "next/server";

function notFound() {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
}

export async function GET() {
    return notFound();
}

export async function POST() {
    return notFound();
}
