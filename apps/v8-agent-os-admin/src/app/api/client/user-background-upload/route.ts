import path from "path";
import { randomUUID } from "crypto";

import sharp from "sharp";
import { NextRequest, NextResponse } from "next/server";

import { requireClientContext } from "@/lib/server/client-proxy";
import { getSessionIdentifier, updateUserRecord } from "@/lib/users";
import {
    buildUserMediaPublicPath,
    ensureUserMediaDirectory,
    removeManagedUserMedia,
} from "@/lib/user-media";

export const runtime = "nodejs";

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_SIZE_BYTES = 20 * 1024 * 1024;

export async function POST(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) return context;

    try {
        const formData = await req.formData();
        const file = formData.get("file");
        if (!(file instanceof File)) {
            return NextResponse.json({ error: "没有找到上传文件" }, { status: 400 });
        }
        if (!ALLOWED_TYPES.has(file.type)) {
            return NextResponse.json({ error: "背景图仅支持 JPG、PNG 和 WEBP" }, { status: 400 });
        }
        if (file.size > MAX_SIZE_BYTES) {
            return NextResponse.json({ error: "背景图过大，请选择 20MB 以内的图片" }, { status: 400 });
        }

        const source = Buffer.from(await file.arrayBuffer());
        const directory = ensureUserMediaDirectory("background");
        const filename = `background-${Date.now()}-${randomUUID().slice(0, 8)}.webp`;
        const targetPath = path.join(directory, filename);
        const image = sharp(source, { animated: false }).rotate();
        const metadata = await image.metadata();
        await image
            .resize({ width: 3840, height: 2160, fit: "inside", withoutEnlargement: true })
            .webp({ quality: 88 })
            .toFile(targetPath);

        const nextPath = buildUserMediaPublicPath("background", filename);
        const previousImage = context.user.appearance?.lightBackgroundImage || "";
        const updated = updateUserRecord(context.user.id, {
            appearance: {
                ...(context.user.appearance || {}),
                lightBackgroundImage: nextPath,
                lightBackgroundEnabled: true,
            },
        });
        if (previousImage && previousImage !== nextPath) {
            removeManagedUserMedia(previousImage, "background");
        }

        return NextResponse.json({
            url: nextPath,
            path: nextPath,
            originalWidth: metadata.width || null,
            originalHeight: metadata.height || null,
            user: {
                id: updated.id,
                login: updated.login,
                email: getSessionIdentifier(updated),
                name: updated.name || "",
                image: updated.image || "",
                appearance: updated.appearance || {},
                role: updated.role,
            },
        });
    } catch (error) {
        console.error("[client/user-background-upload] Upload failed:", error);
        return NextResponse.json({ error: "背景图上传失败" }, { status: 500 });
    }
}
