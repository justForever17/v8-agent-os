import { NextRequest, NextResponse } from "next/server";
import { verifyPassword } from "@/lib/password";
import { resolveInternalSecret } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";
import { findUserByIdentifier, getSessionIdentifier } from "@/lib/users";

export async function POST(req: NextRequest) {
    try {
        // Ensure this endpoint is also protected by service secret (User from Web -> Web Server -> Admin)
        // Or strictly checking credentials implies public access?
        // Actually, for LOGIN, the Web Server calls this with the User's credentials. 
        // We should restrict it to be callable only by the Web Server (with Secret) to prevent public enumeration.

        await verifyServiceAuth(req);
        // Note: For login, we might not have 'x-v8-agent-os-user-email' yet because we are authenticating.
        // So we might only check the SECRET here.

        const headersList = req.headers;
        const secret = headersList.get("x-v8-agent-os-secret");
        if (secret !== resolveInternalSecret()) {
            return NextResponse.json({ error: "Unauthorized Service Call" }, { status: 401 });
        }

        const { login, email, password } = await req.json();
        const identifier = String(login || email || "").trim();

        if (!identifier || !password) {
            return NextResponse.json({ error: "Missing credentials" }, { status: 400 });
        }

        const user = findUserByIdentifier(identifier);

        if (!user || !user.password) {
            return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
        }

        const isValid = await verifyPassword(password, user.password as string);

        if (!isValid) {
            return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
        }

        return NextResponse.json({
            success: true,
            user: {
                id: user.id,
                email: getSessionIdentifier(user),
                login: user.login,
                name: user.name,
                image: user.image,
                role: user.role,
                mustChangePassword: Boolean(user.mustChangePassword),
            }
        });

    } catch (error) {
        console.error("[VerifyCredentials] Error:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
