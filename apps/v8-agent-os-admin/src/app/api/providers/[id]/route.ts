import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { buildStoredCredential, mapEngineProvider } from "@/lib/models/provider-admin";
import { canonicalizeOauthCredentialReference } from "@/lib/models/oauth-store";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id } = await params;
        
        const res = await fetch(`${ENGINE_URL}/models`);
        if (!res.ok) throw new Error(`Python API returned ${res.status}`);
        
        const routesData = await res.json();
        const providerData = routesData.providers?.[id];

        if (!providerData) {
            return NextResponse.json({ error: "Provider not found" }, { status: 404 });
        }

        return NextResponse.json(mapEngineProvider(id, providerData));

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
        console.error("Failed to fetch provider via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id } = await params;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const data = await req.json() as any;
        
        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (!routesData.providers || !routesData.providers[id]) {
            return NextResponse.json({ error: "Provider not found" }, { status: 404 });
        }

        const existingProvider = routesData.providers[id].provider || {};
        let currentCredential = buildStoredCredential({
            providerType: data.type || existingProvider.type,
            credentialMode: data.credentialMode,
            apiKey: data.apiKey,
            oauthPath: data.oauthPath,
            existingRawCredential: existingProvider.api_key,
        });
        let oauthRef = String(existingProvider.oauth_ref || "").trim();
        if (currentCredential.startsWith("oauth:")) {
            const canonicalized = (
                await canonicalizeOauthCredentialReference({
                    providerId: id,
                    rawReference: currentCredential,
                    platformLoginPreset: data.platformLoginPreset,
                })
            );
            currentCredential = canonicalized.storedCredential;
            oauthRef = canonicalized.oauthRef;
        }

        routesData.providers[id].provider = {
            ...existingProvider,
            name: data.name ?? existingProvider.name ?? id,
            description: data.description ?? existingProvider.description ?? "",
            icon: data.icon ?? existingProvider.icon ?? "",
            base_url: data.baseUrl ?? existingProvider.base_url ?? "",
            api_key: currentCredential,
            type: data.type || existingProvider.type || "API",
            api_standard: data.apiStandard || existingProvider.api_standard || "openai",
            is_enabled: data.isEnabled !== undefined ? data.isEnabled : (existingProvider.is_enabled !== false),
            credential_mode: data.credentialMode || existingProvider.credential_mode || "apiKey",
            oauth_preset: data.platformLoginPreset || existingProvider.oauth_preset || "",
            oauth_ref: oauthRef,
        };

        // Save back
        const resPost = await fetch(`${ENGINE_URL}/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routesData),
        });

        if (!resPost.ok) throw new Error(`Python API returned ${resPost.status} on POST`);

        return NextResponse.json(mapEngineProvider(id, routesData.providers[id]));

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
        console.error("Failed to update provider via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id } = await params;
        
        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (routesData.providers && routesData.providers[id]) {
            delete routesData.providers[id];
            
            const resPost = await fetch(`${ENGINE_URL}/models`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(routesData),
            });

            if (!resPost.ok) throw new Error(`Python API returned ${resPost.status} on POST`);
        }

        return NextResponse.json({ success: true });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
        console.error("Failed to delete provider via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}
