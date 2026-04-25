import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return new NextResponse("Unauthorized", { status: 401 });

    try {
        const { id } = await params;
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
            globalExposure,
            reflection_enabled,
            max_reflections,
        } = data;

        // Python Markdown Agents expect these base fields.
        const payload = {
            id,
            name: name || id, // Fallback to id if name is stripped
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
            globalExposure: Boolean(globalExposure),
            reflection_enabled: reflection_enabled || false,
            max_reflections: max_reflections || 3,
            system_prompt: systemPrompt || ""
        };

        const res = await fetch(`${ENGINE_URL}/agents`, {
            method: "POST", // The python engine uses POST for both insert and update based on filename
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (!res.ok) {
            throw new Error(`Python API returned ${res.status}`);
        }

        await res.json();
        return NextResponse.json({ ...payload });
    } catch (error) {
        console.error("Failed to update agent via Python API:", error);
        return NextResponse.json({ error: "Failed to update agent" }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session) return new NextResponse("Unauthorized", { status: 401 });

    try {
        const { id } = await params;
        const res = await fetch(`${ENGINE_URL}/agents/${id}`, {
            method: "DELETE"
        });

        if (!res.ok) {
            throw new Error(`Python API returned ${res.status}`);
        }

        return NextResponse.json({ success: true });
    } catch (error) {
        console.error("Failed to delete agent via Python API:", error);
        return NextResponse.json({ error: "Failed to delete agent" }, { status: 500 });
    }
}
