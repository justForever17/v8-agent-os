import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { hashPassword, verifyPassword } from "@/lib/password";
import { findUserByIdentifier, updateUserRecord } from "@/lib/users";
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

export async function POST(req: NextRequest) {
    const user = await resolveCurrentUser(req);
    if (!user) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await req.json().catch(() => ({}));
    const oldPassword = String(body.oldPassword || "");
    const newPassword = String(body.newPassword || "");
    const forceMode = Boolean(body.forceMode);

    if (!newPassword || newPassword.length < 6) {
        return NextResponse.json({ error: "新密码至少需要 6 位" }, { status: 400 });
    }

    if (!forceMode) {
        const isValid = await verifyPassword(oldPassword, user.password || "");
        if (!isValid) {
            return NextResponse.json({ error: "当前密码不正确" }, { status: 400 });
        }
    }

    const password = await hashPassword(newPassword);
    updateUserRecord(user.id, {
        password,
        mustChangePassword: false,
    });

    return NextResponse.json({ success: true });
}
