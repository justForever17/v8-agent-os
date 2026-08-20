import fs from "fs";
import path from "path";
import { randomUUID } from "crypto";

import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import {
    getNativeImageProcessingAvailability,
    NATIVE_IMAGE_PROCESSING_UNAVAILABLE_MESSAGE,
} from "@/lib/server/native-image-processing";
import { resolveAdminPublicBaseUrl } from "@/lib/server/runtime-config";
import { findUserByIdentifier } from "@/lib/users";

export const runtime = "nodejs";

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
const MAX_SIZE_BYTES = 8 * 1024 * 1024;
const AVATAR_DIR = path.join(process.cwd(), "public", "Avatar");

async function resolveAuthorizedUser(req: NextRequest) {
    const serviceIdentifier = await verifyServiceAuth(req);
    if (serviceIdentifier) {
        const user = findUserByIdentifier(serviceIdentifier);
        return user?.login || null;
    }

    const session = await auth();
    return session?.user?.login || session?.user?.email || null;
}

export async function POST(req: NextRequest) {
    try {
        const userEmail = await resolveAuthorizedUser(req);
        if (!userEmail) {
            return NextResponse.json({ error: "未授权" }, { status: 401 });
        }

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
        const source = Buffer.from(await file.arrayBuffer());
        fs.mkdirSync(AVATAR_DIR, { recursive: true });

        const filename = `avatar-${Date.now()}-${randomUUID().slice(0, 8)}.webp`;
        const targetPath = path.join(AVATAR_DIR, filename);

        const image = sharp(source, { animated: false }).rotate();
        const metadata = await image.metadata();
        await image
            .resize(256, 256, { fit: "cover", position: "centre" })
            .webp({ quality: 90 })
            .toFile(targetPath);

        const publicPath = `/Avatar/${filename}`;
        const url = `${resolveAdminPublicBaseUrl()}${publicPath}`;

        return NextResponse.json({
            url,
            path: publicPath,
            width: 256,
            height: 256,
            originalWidth: metadata.width || null,
            originalHeight: metadata.height || null,
        });
    } catch (error) {
        console.error("[avatar-upload] Upload failed:", error);
        return NextResponse.json({ error: "头像上传失败" }, { status: 500 });
    }
}
