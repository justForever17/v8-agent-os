import { NextRequest, NextResponse } from "next/server";
import * as os from "os";
import * as path from "path";
import * as fs from "fs";
import { resolveConfigDomain } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";
import { auth } from "@/lib/auth";

function legacyWorkspaceResidueStatus(workspacePath: string) {
    const normalized = path.resolve(String(workspacePath || "").trim());
    const workspaceRoot = path.resolve(process.cwd(), "..");
    const legacyTargets = [
        { target: path.resolve(workspaceRoot, "apps", "engine", "workspace"), reason: "命中旧 monorepo engine workspace 路径" },
        { target: path.resolve(workspaceRoot, "apps", "admin"), reason: "命中旧 monorepo admin 根目录" },
        { target: path.resolve(workspaceRoot, "apps", "web"), reason: "命中旧 monorepo web 根目录" },
    ];

    for (const item of legacyTargets) {
        if (normalized === item.target || normalized.startsWith(`${item.target}${path.sep}`)) {
            return {
                isLegacyResidue: true,
                legacyReason: item.reason,
            };
        }
    }
    return {
        isLegacyResidue: false,
        legacyReason: "",
    };
}

export async function POST(req: NextRequest) {
    try {
        let userEmail: string | undefined | null;
        userEmail = await verifyServiceAuth(req);
        if (!userEmail) {
            const session = await auth();
            userEmail = session?.user?.email;
        }

        if (!userEmail) {
            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        }
        const formData = await req.formData();
        const file = formData.get("file") as File;

        if (!file) {
            return NextResponse.json({ error: "No file provided" }, { status: 400 });
        }

        const buffer = Buffer.from(await file.arrayBuffer());
        const filename = file.name.replace(/\s+/g, "_"); // Sanitize filename

        let workspacePath = "";
        let url = "";
        try {
            const workspaceConfig = resolveConfigDomain<Record<string, unknown>>("workspace", {});
            workspacePath = typeof workspaceConfig.agent_workspace_path === "string" ? workspaceConfig.agent_workspace_path : "";
            if (!workspacePath) {
                workspacePath = path.join(os.homedir(), ".v8-agent-os", "workspace");
            }
            const residue = legacyWorkspaceResidueStatus(workspacePath);
            if (residue.isLegacyResidue) {
                return NextResponse.json(
                    {
                        error: `当前工作区命中 legacy monorepo residue：${residue.legacyReason}，上传入口已阻止自动建目录。请先改成 canonical workspace。`,
                    },
                    { status: 400 }
                );
            }
            
            const uploadsDir = path.join(workspacePath, "uploads");
            if (!fs.existsSync(uploadsDir)) {
                fs.mkdirSync(uploadsDir, { recursive: true });
            }
            
            // Avoid filename collisions
            const timestamp = Date.now();
            const uniqueFilename = `${timestamp}_${filename}`;
            const targetPath = path.join(uploadsDir, uniqueFilename);
            
            fs.writeFileSync(targetPath, buffer);
            
            // Return an HTTP route URL that the frontend can render
            url = `/api/workspace/files/uploads/${uniqueFilename}`;
            
        } catch (e) {
             console.error("Local Workspace upload error:", e);
             return NextResponse.json({ error: "Failed to write to local workspace" }, { status: 500 });
        }

        const savedFile = {
            id: Math.random().toString(36).substring(7),
            name: filename,
            url,
            type: file.type,
            size: file.size,
            createdAt: new Date().toISOString()
        };

        return NextResponse.json(savedFile);
    } catch (error) {
        console.error("Upload failed:", error);
        return NextResponse.json({ error: "Upload failed" }, { status: 500 });
    }
}
