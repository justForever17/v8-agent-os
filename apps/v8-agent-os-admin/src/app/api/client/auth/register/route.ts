import { NextRequest, NextResponse } from "next/server";

import { issueMobileSessionForUser } from "@/lib/mobile-auth";
import { hashPassword } from "@/lib/password";
import { createUserRecord, findUserByIdentifier, hasUsers } from "@/lib/users";

export async function POST(req: NextRequest) {
    try {
        const payload = await req.json().catch(() => ({}));
        const login = String(payload?.login || payload?.email || "").trim();
        const password = String(payload?.password || "");
        const name = String(payload?.name || payload?.nickname || "").trim();
        const image = typeof payload?.image === "string" ? payload.image.trim() : "";
        const deviceName = String(payload?.deviceName || "").trim();

        if (!login || !password || !name) {
            return NextResponse.json({ error: "请填写登录名、昵称和密码" }, { status: 400 });
        }
        if (password.length < 6) {
            return NextResponse.json({ error: "密码至少需要 6 位" }, { status: 400 });
        }
        if (findUserByIdentifier(login)) {
            return NextResponse.json({ error: "该用户已存在" }, { status: 400 });
        }

        const hashedPassword = await hashPassword(password);
        const role = !hasUsers() ? "ADMIN" : "USER";
        const user = createUserRecord({
            login,
            email: typeof payload?.email === "string" && payload.email.trim() ? payload.email.trim() : undefined,
            password: hashedPassword,
            name,
            image: image || undefined,
            role,
            mustChangePassword: Boolean(payload?.mustChangePassword),
        });

        return NextResponse.json(issueMobileSessionForUser(user, deviceName));
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "注册失败" },
            { status: 500 },
        );
    }
}
