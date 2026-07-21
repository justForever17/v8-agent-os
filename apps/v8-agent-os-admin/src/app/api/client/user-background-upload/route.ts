import fs from "fs";
import path from "path";
import { randomUUID } from "crypto";
import { once } from "events";

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

const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const VIDEO_TYPES = new Set(["video/mp4"]);
const MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024;
const MAX_VIDEO_SIZE_BYTES = 500 * 1024 * 1024;

function hasMp4Signature(buffer: Buffer) {
    return buffer.length >= 12 && buffer.subarray(4, 8).toString("ascii") === "ftyp";
}

function uploadTooLargeMessage(isVideo: boolean) {
    return isVideo
        ? "背景视频过大，请选择 500MB 以内的 MP4"
        : "背景图过大，请选择 20MB 以内的图片";
}

async function streamRequestToFile(req: NextRequest, temporaryPath: string, maxSize: number) {
    if (!req.body) throw new Error("UPLOAD_BODY_MISSING");
    const writer = fs.createWriteStream(temporaryPath, { flags: "wx" });
    let size = 0;
    let signature = Buffer.alloc(0);
    const reader = req.body.getReader();
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = Buffer.from(value);
            size += chunk.length;
            if (size > maxSize) throw new Error("UPLOAD_TOO_LARGE");
            if (signature.length < 12) {
                signature = Buffer.concat([signature, chunk.subarray(0, 12 - signature.length)]);
            }
            if (!writer.write(chunk)) await once(writer, "drain");
        }
        writer.end();
        await once(writer, "finish");
        return { size, signature };
    } catch (error) {
        await reader.cancel(error).catch(() => undefined);
        writer.destroy();
        await fs.promises.rm(temporaryPath, { force: true }).catch(() => undefined);
        throw error;
    } finally {
        reader.releaseLock();
    }
}

export async function POST(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) return context;

    try {
        const rawUpload = req.headers.get("x-v8-upload-mode") === "raw";
        const contentType = rawUpload
            ? String(req.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase()
            : "";
        const formData = rawUpload ? null : await req.formData();
        const file = formData?.get("file");
        if (!rawUpload && !(file instanceof File)) {
            return NextResponse.json({ error: "没有找到上传文件" }, { status: 400 });
        }
        const mediaType = rawUpload ? contentType : (file as File).type;
        const isVideo = VIDEO_TYPES.has(mediaType);
        if (!IMAGE_TYPES.has(mediaType) && !isVideo) {
            return NextResponse.json({ error: "自定义背景仅支持 JPG、PNG、WEBP 和 MP4" }, { status: 400 });
        }
        const maxSize = isVideo ? MAX_VIDEO_SIZE_BYTES : MAX_IMAGE_SIZE_BYTES;
        const declaredSize = Number(req.headers.get("content-length") || 0);
        if ((declaredSize > 0 && declaredSize > maxSize) || (!rawUpload && (file as File).size > maxSize)) {
            return NextResponse.json({ error: uploadTooLargeMessage(isVideo) }, { status: 413 });
        }

        const directory = ensureUserMediaDirectory("background");
        const persistedMediaType = isVideo ? "video" : "image";
        const filename = `background-${Date.now()}-${randomUUID().slice(0, 8)}.${isVideo ? "mp4" : "webp"}`;
        const targetPath = path.join(directory, filename);
        const temporaryPath = `${targetPath}.upload`;
        let originalWidth: number | null = null;
        let originalHeight: number | null = null;
        try {
            let signature = Buffer.alloc(0);
            if (rawUpload) {
                ({ signature } = await streamRequestToFile(req, temporaryPath, maxSize));
            } else {
                const source = Buffer.from(await (file as File).arrayBuffer());
                signature = source.subarray(0, 12);
                await fs.promises.writeFile(temporaryPath, source, { flag: "wx" });
            }
            if (isVideo) {
                if (!hasMp4Signature(signature)) {
                    return NextResponse.json({ error: "背景视频不是有效的 MP4 文件" }, { status: 400 });
                }
                await fs.promises.rename(temporaryPath, targetPath);
            } else {
                const image = sharp(temporaryPath, { animated: false }).rotate();
                const metadata = await image.metadata();
                originalWidth = metadata.width || null;
                originalHeight = metadata.height || null;
                await image
                    .resize({ width: 3840, height: 2160, fit: "inside", withoutEnlargement: true })
                    .webp({ quality: 88 })
                    .toFile(targetPath);
            }
        } catch (error) {
            if (error instanceof Error && error.message === "UPLOAD_TOO_LARGE") {
                return NextResponse.json({ error: uploadTooLargeMessage(isVideo) }, { status: 413 });
            }
            throw error;
        } finally {
            await fs.promises.rm(temporaryPath, { force: true }).catch(() => undefined);
        }

        const nextPath = buildUserMediaPublicPath("background", filename);
        const previousMedia = context.user.appearance?.lightBackgroundMedia
            || context.user.appearance?.lightBackgroundImage
            || "";
        const updated = updateUserRecord(context.user.id, {
            appearance: {
                ...(context.user.appearance || {}),
                lightBackgroundMedia: nextPath,
                lightBackgroundMediaType: persistedMediaType,
                lightBackgroundImage: persistedMediaType === "image" ? nextPath : "",
                lightBackgroundEnabled: true,
            },
        });
        if (previousMedia && previousMedia !== nextPath) {
            removeManagedUserMedia(previousMedia, "background");
        }

        return NextResponse.json({
            url: nextPath,
            path: nextPath,
            mediaType: persistedMediaType,
            originalWidth,
            originalHeight,
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
        return NextResponse.json({ error: "背景上传失败" }, { status: 500 });
    }
}
