import { NextResponse } from 'next/server';
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type SupervisorRegistryData = {
    systemPrompt?: string;
    allowedTools?: string[] | null;
    lockedNativeTools?: Array<{
        name: string;
        description?: string;
        reason?: string;
    }>;
    runtimeManagedTools?: Array<{
        name: string;
        description?: string;
        reason?: string;
        runtimeKind?: string;
        runtimeLabel?: string;
    }>;
    profile?: {
        name?: string;
        roleLabel?: string;
        avatar?: string;
    };
    bindings?: {
        supervisorModel?: string | null;
        defaultReplyModel?: string | null;
    };
};

export async function GET() {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/config-registry/supervisor");
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const supervisorData = (data as { data?: SupervisorRegistryData }).data || {};

        const profile = supervisorData.profile || {};

        return NextResponse.json({
            systemPrompt: String(supervisorData.systemPrompt || ""),
            model_id: supervisorData.bindings?.supervisorModel || null,
            binding_source: "config.json#models.roles.supervisor",
            allowed_tools: supervisorData.allowedTools ?? null,
            locked_native_tools: supervisorData.lockedNativeTools ?? [],
            runtime_managed_tools: supervisorData.runtimeManagedTools ?? [],
            name: profile.name || "智能主管",
            roleLabel: profile.roleLabel || "主理人",
            avatar: profile.avatar || "",
        });
    } catch (error) {
        console.error("Failed to read supervisor setup:", error);
        return NextResponse.json({ error: "Failed to read data" }, { status: 500 });
    }
}

export async function POST(req: Request) {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { systemPrompt, model_id, allowed_tools, name, roleLabel, avatar } = await req.json();
        const { response, data } = await proxyEngineJson("/config-registry/supervisor", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                data: {
                    systemPrompt,
                    allowedTools: allowed_tools,
                    profile: {
                        name,
                        roleLabel,
                        avatar,
                    },
                    bindings: {
                        supervisorModel: model_id ?? null,
                        defaultReplyModel: undefined,
                    },
                },
            }),
        });
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }

        return NextResponse.json({
            success: true,
            systemPrompt,
            model_id: model_id ?? null,
            binding_source: "config.json#models.roles.supervisor",
            allowed_tools,
            name,
            roleLabel,
            avatar,
        });
    } catch (error) {
        console.error("Failed to save supervisor config:", error);
        return NextResponse.json({ error: "Failed to save data" }, { status: 500 });
    }
}
