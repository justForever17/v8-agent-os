import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { buildStoredCredential, mapEngineProvider } from "@/lib/models/provider-admin";
import { canonicalizeOauthCredentialReference } from "@/lib/models/oauth-store";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const res = await fetch(`${ENGINE_URL}/models/public`, { cache: "no-store" });
        if (!res.ok) throw new Error(`Python API returned ${res.status}`);
        const routesData = await res.json();
        const providersDict = routesData.providers || {};
        const providersList = Object.keys(providersDict).map((providerKey) =>
            mapEngineProvider(providerKey, providersDict[providerKey])
        );
        return NextResponse.json(providersList);
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        console.error("Failed to fetch providers via Python API:", message);
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const data = await req.json() as Record<string, unknown>;
        const providerCode = String(data.code || data.name || "")
            .toLowerCase()
            .replace(/[^a-z0-9]/g, "-");
        if (!providerCode) {
            return NextResponse.json({ error: "provider code is required" }, { status: 400 });
        }

        const providerPatch: Record<string, unknown> = {
            name: data.name || providerCode,
            description: data.description ?? "",
            icon: data.icon ?? "",
            base_url: data.baseUrl ?? "",
            api_standard: data.apiStandard || "openai",
            type: data.type || "API",
            is_enabled: data.isEnabled !== undefined ? data.isEnabled : true,
            credential_mode: data.credentialMode || "apiKey",
            oauth_preset: data.platformLoginPreset || "",
            local_backend_preset: data.localBackendPreset || "",
        };
        let storedCredential = buildStoredCredential({
            providerType: String(data.type || ""),
            credentialMode: String(data.credentialMode || ""),
            apiKey: String(data.apiKey || ""),
            oauthPath: String(data.oauthPath || ""),
            existingRawCredential: "",
        });
        if (storedCredential.startsWith("oauth:")) {
            const canonicalized = await canonicalizeOauthCredentialReference({
                providerId: providerCode,
                rawReference: storedCredential,
                platformLoginPreset: (String(data.platformLoginPreset || "") || "codex") as "codex",
            });
            storedCredential = canonicalized.storedCredential;
            providerPatch.oauth_ref = canonicalized.oauthRef;
        }
        if (storedCredential) providerPatch.api_key = storedCredential;

        const response = await fetch(`${ENGINE_URL}/models/providers/${encodeURIComponent(providerCode)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(providerPatch),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        console.error("Failed to save provider via Python API:", message);
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
