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
    modelParameters?: {
        supervisor?: {
            temperature?: number | null;
        };
        subagent?: {
            temperature?: number | null;
        };
    };
    promptBudgetDiagnostics?: Array<Record<string, unknown>>;
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
            prompt_budget_diagnostics: supervisorData.promptBudgetDiagnostics ?? [],
            model_id: supervisorData.bindings?.supervisorModel || null,
            binding_source: "config.json#models.roles.supervisor",
            allowed_tools: supervisorData.allowedTools ?? null,
            locked_native_tools: supervisorData.lockedNativeTools ?? [],
            runtime_managed_tools: supervisorData.runtimeManagedTools ?? [],
            supervisor_temperature: supervisorData.modelParameters?.supervisor?.temperature ?? null,
            subagent_temperature: supervisorData.modelParameters?.subagent?.temperature ?? null,
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

        const { systemPrompt, model_id, allowed_tools, name, roleLabel, avatar, supervisor_temperature, subagent_temperature } = await req.json();
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
                    modelParameters: {
                        supervisor: { temperature: supervisor_temperature ?? null },
                        subagent: { temperature: subagent_temperature ?? null },
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
            prompt_budget_diagnostics: (data as { data?: SupervisorRegistryData })?.data?.promptBudgetDiagnostics ?? [],
            model_id: model_id ?? null,
            binding_source: "config.json#models.roles.supervisor",
            supervisor_temperature: supervisor_temperature ?? null,
            subagent_temperature: subagent_temperature ?? null,
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
