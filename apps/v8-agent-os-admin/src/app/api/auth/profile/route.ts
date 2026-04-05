import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { updateUserRecord, findUserByIdentifier, getSessionIdentifier } from "@/lib/users";
import { verifyServiceAuth } from "@/lib/service-auth";

async function resolveCurrentUser(req: NextRequest) {
    const serviceIdentifier = await verifyServiceAuth(req);
    if (serviceIdentifier) {
        return findUserByIdentifier(serviceIdentifier);
    }
    const session = await auth();
    const identifier = String(session?.user?.email || session?.user?.login || "").trim();
    if (!identifier) return null;
    return findUserByIdentifier(identifier);
}

export async function GET(req: NextRequest) {
    const user = await resolveCurrentUser(req);
    if (!user) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    return NextResponse.json({
        id: user.id,
        login: user.login,
        email: user.email || user.login,
        name: user.name || "",
        image: user.image || "",
        role: user.role,
        mustChangePassword: Boolean(user.mustChangePassword),
    });
}

export async function PATCH(req: NextRequest) {
    const user = await resolveCurrentUser(req);
    if (!user) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await req.json().catch(() => ({}));
    const name = typeof body.name === "string" ? body.name : user.name || "";
    const image = typeof body.image === "string" ? body.image : user.image || "";
    const email = typeof body.email === "string" ? body.email : user.email || "";

    const updated = updateUserRecord(user.id, { name, image, email });
    return NextResponse.json({
        success: true,
        user: {
            id: updated.id,
            login: updated.login,
            email: getSessionIdentifier(updated),
            name: updated.name || "",
            image: updated.image || "",
            role: updated.role,
            mustChangePassword: Boolean(updated.mustChangePassword),
        },
    });
}
