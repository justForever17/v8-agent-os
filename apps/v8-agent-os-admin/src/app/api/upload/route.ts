import { NextRequest, NextResponse } from "next/server";
import * as os from "os";
import * as path from "path";
import * as fs from "fs";
import { resolveConfigDomain } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";
import { auth } from "@/lib/auth";

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
