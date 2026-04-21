import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
    const session = await auth();
    if (!session) return new NextResponse("Unauthorized", { status: 401 });

    try {
        const res = await fetch(`${ENGINE_URL}/agents`);
        if (!res.ok) {
            throw new Error(`Python API returned ${res.status}`);
        }
        const data = await res.json();
        // Python returns { agents: [...] }. Map it to match what the frontend expects.
        const mappedAgents = (data.agents || []).map((agent: Record<string, unknown>) => ({
            ...agent,
            systemPrompt: agent.system_prompt,
            modelId: agent.model
        }));
        return NextResponse.json(mappedAgents);
    } catch (error) {
        console.error("Failed to fetch agents via Python API:", error);
        return NextResponse.json({ error: "Failed to fetch agents" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session) return new NextResponse("Unauthorized", { status: 401 });

    try {
        const data = await req.json();
        const {
            name,
            description,
            modelId,
            systemPrompt,
            tools,
            tool_mode,
            avatar,
            icon,
            roleLabel,
            capabilitySnapshot,
            reflection_enabled,
            max_reflections,
        } = data;

        if (!name) {
            return NextResponse.json({ error: "Name is required" }, { status: 400 });
        }

        // Prepare the payload for the Python Markdown Agent API
        const payload = {
            id: name.toLowerCase().replace(/[^a-z0-9]/g, '-'), // Generate a safe filename ID
            name,
            description: description || "",
            avatar: avatar || "",
            icon: icon || "",
            roleLabel: roleLabel || "",
            model: modelId || "",
            tools: tools || [],
            tool_mode: String(tool_mode || "").trim() || undefined,
            capabilitySnapshot: capabilitySnapshot && typeof capabilitySnapshot === "object" && !Array.isArray(capabilitySnapshot)
                ? capabilitySnapshot
                : {},
            reflection_enabled: reflection_enabled || false,
            max_reflections: max_reflections || 3,
            system_prompt: systemPrompt || "",
            createdBy: data.createdBy || "human"
        };

        const res = await fetch(`${ENGINE_URL}/agents`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (!res.ok) {
             throw new Error(`Python API returned ${res.status}`);
        }

        await res.json();
        return NextResponse.json({ ...payload });
    } catch (error) {
        console.error("Failed to save agent via Python API:", error);
        return NextResponse.json({ error: "Failed to save agent" }, { status: 500 });
    }
}
