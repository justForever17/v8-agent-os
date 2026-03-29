import { NextRequest, NextResponse } from "next/server";

import { hashPassword } from "@/lib/password";
import { createUserRecord, hasUsers } from "@/lib/users";

export async function POST(req: NextRequest) {
    try {
        if (hasUsers()) {
            return NextResponse.json({ error: "管理台已完成首次设置" }, { status: 409 });
        }

        const { login, name, password } = await req.json();
        const nextLogin = String(login || "").trim();
        const nextName = String(name || "").trim();
        const nextPassword = String(password || "");

        if (!nextLogin || !nextName || !nextPassword) {
            return NextResponse.json({ error: "请填写登录名、昵称和密码" }, { status: 400 });
        }

        const passwordHash = await hashPassword(nextPassword);
        const user = createUserRecord({
            login: nextLogin,
            name: nextName,
            password: passwordHash,
            role: "ADMIN",
            mustChangePassword: false,
        });

        return NextResponse.json({
            success: true,
            user: {
                id: user.id,
                login: user.login,
                name: user.name,
            },
        });
    } catch (error) {
        console.error("Bootstrap registration error:", error);
        return NextResponse.json({ error: error instanceof Error ? error.message : "首次设置失败" }, { status: 500 });
    }
}
