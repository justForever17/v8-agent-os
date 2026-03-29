import { NextRequest, NextResponse } from "next/server";
import { hashPassword } from "@/lib/password";
import { createUserRecord, findUserByIdentifier, hasUsers } from "@/lib/users";

export async function POST(req: NextRequest) {
    try {
        const { login, email, password, name, avatar, role, mustChangePassword } = await req.json();
        const nextLogin = String(login || email || "").trim();

        if (!nextLogin || !password || !name) {
            return NextResponse.json({ error: "Missing required fields" }, { status: 400 });
        }

        if (findUserByIdentifier(nextLogin)) {
            return NextResponse.json({ error: "User already exists" }, { status: 400 });
        }

        const hashedPassword = await hashPassword(password);
        const targetRole = String(role || "").toUpperCase() === "ADMIN" || !hasUsers() ? "ADMIN" : "USER";
        const user = createUserRecord({
            login: nextLogin,
            email: typeof email === "string" && email.trim() ? email.trim() : undefined,
            password: hashedPassword,
            name,
            image: avatar,
            role: targetRole,
            mustChangePassword: Boolean(mustChangePassword),
        });

        return NextResponse.json({
            success: true,
            user: {
                id: user.id,
                login: user.login,
                email: user.email || user.login,
                name: user.name,
            },
        });
    } catch (error) {
        console.error("Registration error:", error);
        return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to register user" }, { status: 500 });
    }
}
