import { NextRequest, NextResponse } from "next/server";

import { hashPassword } from "@/lib/password";
import { isAdminStorageUnavailableError } from "@/lib/storage";
import { createUserRecord, hasOwner, isOwnerAlreadyInitializedError } from "@/lib/users";

function ownerAlreadyInitializedResponse() {
    return NextResponse.json({
        error: "管理台已完成首次设置",
        code: "owner_already_initialized",
    }, { status: 409 });
}

export async function POST(req: NextRequest) {
    try {
        if (hasOwner()) {
            return ownerAlreadyInitializedResponse();
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
        if (isOwnerAlreadyInitializedError(error)) {
            return ownerAlreadyInitializedResponse();
        }
        if (isAdminStorageUnavailableError(error)) {
            return NextResponse.json({
                error: "管理员状态不可用，请先恢复 users.json",
                code: error.code,
            }, { status: 503 });
        }
        return NextResponse.json({ error: error instanceof Error ? error.message : "首次设置失败" }, { status: 500 });
    }
}
