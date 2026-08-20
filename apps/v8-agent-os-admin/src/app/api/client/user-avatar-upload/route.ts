import path from "path";
import { randomUUID } from "crypto";

import { NextRequest, NextResponse } from "next/server";

import { requireClientContext } from "@/lib/server/client-proxy";
import {
    getNativeImageProcessingAvailability,
    NATIVE_IMAGE_PROCESSING_UNAVAILABLE_MESSAGE,
} from "@/lib/server/native-image-processing";
import { getSessionIdentifier, updateUserRecord } from "@/lib/users";
import {
    buildUserMediaPublicPath,
    ensureUserMediaDirectory,
    removeManagedUserMedia,
} from "@/lib/user-media";

export const runtime = "nodejs";

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
const MAX_SIZE_BYTES = 8 * 1024 * 1024;

export async function POST(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }

    try {
        const formData = await req.formData();
        const file = formData.get("file");
        if (!(file instanceof File)) {
            return NextResponse.json({ error: "没有找到上传文件" }, { status: 400 });
        }

        if (!ALLOWED_TYPES.has(file.type)) {
            return NextResponse.json({ error: "暂不支持这种图片格式" }, { status: 400 });
        }
        if (file.size > MAX_SIZE_BYTES) {
            return NextResponse.json({ error: "图片过大，请换一张更小的图片" }, { status: 400 });
        }
        const availability = getNativeImageProcessingAvailability();
        if (!availability.available) {
            return NextResponse.json({
                error: NATIVE_IMAGE_PROCESSING_UNAVAILABLE_MESSAGE,
                code: availability.reasonCode,
            }, { status: 503 });
        }

        const { default: sharp } = await import("sharp");
        const buffer = Buffer.from(await file.arrayBuffer());
        const userAvatarDir = ensureUserMediaDirectory("avatar");

        const filename = `user-${Date.now()}-${randomUUID().slice(0, 8)}.webp`;
        const targetPath = path.join(userAvatarDir, filename);
        const image = sharp(buffer, { animated: false }).rotate();
        const metadata = await image.metadata();

        await image
            .resize(256, 256, { fit: "cover", position: "centre" })
            .webp({ quality: 90 })
            .toFile(targetPath);

        const nextPath = buildUserMediaPublicPath("avatar", filename);
        const previousImage = context.user.image || "";
        const updated = updateUserRecord(context.user.id, { image: nextPath });
        if (previousImage && previousImage !== nextPath) {
            removeManagedUserMedia(previousImage, "avatar");
        }

        return NextResponse.json({
            url: nextPath,
            path: nextPath,
            width: 256,
            height: 256,
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
        console.error("[client/user-avatar-upload] Upload failed:", error);
        return NextResponse.json({ error: "头像上传失败" }, { status: 500 });
    }
}
