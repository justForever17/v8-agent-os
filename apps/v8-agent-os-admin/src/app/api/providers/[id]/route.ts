import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { buildStoredCredential, mapEngineProvider } from "@/lib/models/provider-admin";
import { canonicalizeOauthCredentialReference } from "@/lib/models/oauth-store";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    try {
        const { id } = await params;
        const res = await fetch(`${ENGINE_URL}/models/public`, { cache: "no-store" });
        if (!res.ok) throw new Error(`Python API returned ${res.status}`);
        const routesData = await res.json();
        const providerData = routesData.providers?.[id];
        if (!providerData) return NextResponse.json({ error: "Provider not found" }, { status: 404 });
        return NextResponse.json(mapEngineProvider(id, providerData));
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        console.error("Failed to fetch provider via Python API:", message);
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    try {
        const { id } = await params;
        const data = await req.json() as Record<string, unknown>;
        const providerPatch: Record<string, unknown> = {};
        if ("name" in data) providerPatch.name = data.name;
        if ("description" in data) providerPatch.description = data.description;
        if ("icon" in data) providerPatch.icon = data.icon;
        if ("baseUrl" in data) providerPatch.base_url = data.baseUrl;
        if ("type" in data) providerPatch.type = data.type;
        if ("apiStandard" in data) providerPatch.api_standard = data.apiStandard;
        if ("isEnabled" in data) providerPatch.is_enabled = data.isEnabled;
        if ("credentialMode" in data) providerPatch.credential_mode = data.credentialMode;
        if ("platformLoginPreset" in data) providerPatch.oauth_preset = data.platformLoginPreset;
        if ("localBackendPreset" in data) providerPatch.local_backend_preset = data.localBackendPreset;
        let currentCredential = buildStoredCredential({
            providerType: String(data.type || ""),
            credentialMode: String(data.credentialMode || ""),
            apiKey: String(data.apiKey || ""),
            oauthPath: String(data.oauthPath || ""),
            existingRawCredential: "",
        });
        if (currentCredential.startsWith("oauth:")) {
            const canonicalized = await canonicalizeOauthCredentialReference({
                providerId: id,
                rawReference: currentCredential,
                platformLoginPreset: (String(data.platformLoginPreset || "") || "codex") as "codex",
            });
            currentCredential = canonicalized.storedCredential;
            providerPatch.oauth_ref = canonicalized.oauthRef;
        }
        if (currentCredential && currentCredential !== "****") providerPatch.api_key = currentCredential;

        const response = await fetch(`${ENGINE_URL}/models/providers/${encodeURIComponent(id)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(providerPatch),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        console.error("Failed to update provider via Python API:", message);
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    try {
        const { id } = await params;
        const response = await fetch(`${ENGINE_URL}/models/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        console.error("Failed to delete provider via Python API:", message);
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
