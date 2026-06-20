import { NextResponse } from "next/server";

export async function POST() {
    return NextResponse.json(
        {
            error: "Public registration is disabled. Create the Owner during Admin bootstrap, then pair devices.",
            nextAction: "open_admin_or_use_pairing",
        },
        { status: 410 },
    );
}
