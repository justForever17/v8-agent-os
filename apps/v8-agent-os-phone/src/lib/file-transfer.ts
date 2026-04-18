import * as Linking from "expo-linking";
import * as FileSystem from "expo-file-system/legacy";
import { Platform, Share } from "react-native";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

function sanitizeName(value: string) {
    return value.replace(/[<>:"/\\|?*\u0000-\u001F]+/g, "_").replace(/\s+/g, "_").slice(0, 120);
}

function parseDispositionFilename(value: string | null) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const utf8Match = raw.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match?.[1]) {
        try {
            return decodeURIComponent(utf8Match[1]);
        } catch {
            return utf8Match[1];
        }
    }
    const basicMatch = raw.match(/filename="?([^";]+)"?/i);
    return basicMatch?.[1] || "";
}

function extensionFromContentType(contentType: string) {
    const normalized = String(contentType || "").toLowerCase();
    if (normalized.includes("audio/mpeg")) return "mp3";
    if (normalized.includes("audio/mp4") || normalized.includes("audio/m4a")) return "m4a";
    if (normalized.includes("audio/wav")) return "wav";
    if (normalized.includes("video/mp4")) return "mp4";
    if (normalized.includes("video/webm")) return "webm";
    if (normalized.includes("video/quicktime")) return "mov";
    if (normalized.includes("video/x-matroska")) return "mkv";
    if (normalized.includes("image/png")) return "png";
    if (normalized.includes("image/jpeg")) return "jpg";
    if (normalized.includes("image/webp")) return "webp";
    if (normalized.includes("application/pdf")) return "pdf";
    if (normalized.includes("application/json")) return "json";
    if (normalized.includes("text/plain")) return "txt";
    if (normalized.includes("text/html")) return "html";
    return "bin";
}

function mimeTypeFromFilename(filename: string, fallback = "application/octet-stream") {
    const normalized = String(filename || "").toLowerCase();
    if (normalized.endsWith(".html") || normalized.endsWith(".htm")) return "text/html";
    if (normalized.endsWith(".pdf")) return "application/pdf";
    if (normalized.endsWith(".ppt")) return "application/vnd.ms-powerpoint";
    if (normalized.endsWith(".pptx")) return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    if (normalized.endsWith(".glb")) return "model/gltf-binary";
    if (normalized.endsWith(".gltf")) return "model/gltf+json";
    if (normalized.endsWith(".json")) return "application/json";
    if (normalized.endsWith(".txt")) return "text/plain";
    if (normalized.endsWith(".mp4")) return "video/mp4";
    if (normalized.endsWith(".jpg") || normalized.endsWith(".jpeg")) return "image/jpeg";
    if (normalized.endsWith(".png")) return "image/png";
    return fallback;
}

function normalizeAdminBaseUrl(value?: string | null) {
    return String(value || "").trim().replace(/\/+$/, "");
}

function resolveAbsoluteDownloadUrl(rawUrl: string, adminBaseUrl?: string | null) {
    const raw = String(rawUrl || "").trim();
    if (!raw) {
        return "";
    }
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        return raw;
    }
    const base = normalizeAdminBaseUrl(adminBaseUrl);
    if (base && raw.startsWith("/")) {
        return `${base}${raw}`;
    }
    if (base && !raw.startsWith("/")) {
        return `${base}/${raw.replace(/^\/+/, "")}`;
    }
    return raw;
}

function extractAuthorizedAdminPath(rawUrl: string, adminBaseUrl?: string | null) {
    const raw = String(rawUrl || "").trim();
    if (!raw) {
        return "";
    }
    if (raw.startsWith("/api/")) {
        return raw;
    }
    const base = normalizeAdminBaseUrl(adminBaseUrl);
    if (!base || !/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        return "";
    }
    try {
        const parsed = new URL(raw);
        const admin = new URL(base);
        if (parsed.origin !== admin.origin || !parsed.pathname.startsWith("/api/")) {
            return "";
        }
        return `${parsed.pathname}${parsed.search}`;
    } catch {
        return "";
    }
}

function arrayBufferToBase64(buffer: ArrayBuffer) {
    const bytes = new Uint8Array(buffer);
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let output = "";
    let index = 0;

    while (index < bytes.length) {
        const byte1 = bytes[index++] ?? 0;
        const byte2 = index < bytes.length ? bytes[index++] : undefined;
        const byte3 = index < bytes.length ? bytes[index++] : undefined;

        const chunk = (byte1 << 16) | ((byte2 ?? 0) << 8) | (byte3 ?? 0);
        output += chars[(chunk >> 18) & 63];
        output += chars[(chunk >> 12) & 63];
        output += typeof byte2 === "number" ? chars[(chunk >> 6) & 63] : "=";
        output += typeof byte3 === "number" ? chars[chunk & 63] : "=";
    }

    return output;
}

async function shareLocalFile(uri: string, filename: string) {
    await Share.share({
        title: filename,
        url: uri,
        message: filename,
    });
}

export async function saveResponseToCache(
    response: Response,
    options?: { prefix?: string; filename?: string; fallbackExtension?: string },
) {
    const contentType = response.headers.get("Content-Type") || "application/octet-stream";
    const dispositionName = parseDispositionFilename(response.headers.get("Content-Disposition"));
    const guessedName = options?.filename || dispositionName || `${options?.prefix || "asset"}-${Date.now()}.${options?.fallbackExtension || extensionFromContentType(contentType)}`;
    const safeName = sanitizeName(guessedName);
    const root = FileSystem.cacheDirectory || FileSystem.documentDirectory;
    if (!root) {
        throw new Error("当前设备没有可写缓存目录");
    }
    const folder = `${root}v8-agent-os/`;
    await FileSystem.makeDirectoryAsync(folder, { intermediates: true }).catch(() => undefined);
    const uri = `${folder}${safeName}`;
    const base64 = arrayBufferToBase64(await response.arrayBuffer());
    await FileSystem.writeAsStringAsync(uri, base64, {
        encoding: FileSystem.EncodingType.Base64,
    });
    return {
        uri,
        filename: safeName,
        contentType,
    };
}

async function writeBase64ToUserSelectedFile(base64: string, filename: string, contentType: string) {
    if (Platform.OS === "android" && FileSystem.StorageAccessFramework) {
        const permissions = await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync();
        if (!permissions.granted) {
            throw new Error("未选择保存文件夹");
        }
        const uri = await FileSystem.StorageAccessFramework.createFileAsync(
            permissions.directoryUri,
            filename,
            contentType || mimeTypeFromFilename(filename),
        );
        await FileSystem.StorageAccessFramework.writeAsStringAsync(uri, base64, {
            encoding: FileSystem.EncodingType.Base64,
        });
        return { uri, filename, contentType, userVisible: true, shared: false };
    }

    const root = FileSystem.documentDirectory || FileSystem.cacheDirectory;
    if (!root) {
        throw new Error("当前设备没有可写目录");
    }
    const folder = `${root}v8-agent-os/downloads/`;
    await FileSystem.makeDirectoryAsync(folder, { intermediates: true }).catch(() => undefined);
    const uri = `${folder}${filename}`;
    await FileSystem.writeAsStringAsync(uri, base64, {
        encoding: FileSystem.EncodingType.Base64,
    });
    await shareLocalFile(uri, filename);
    return { uri, filename, contentType, userVisible: true, shared: true };
}

export async function downloadUrlToUserSelectedFile(
    url: string,
    options?: {
        prefix?: string;
        filename?: string;
        fallbackExtension?: string;
        mimeType?: string;
        adminBaseUrl?: string;
        authorizedFetch?: AuthorizedFetch;
    },
) {
    const authorizedPath = extractAuthorizedAdminPath(url, options?.adminBaseUrl);
    const response = authorizedPath && options?.authorizedFetch
        ? await options.authorizedFetch(authorizedPath)
        : await fetch(resolveAbsoluteDownloadUrl(url, options?.adminBaseUrl));
    if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
    }
    const contentType = options?.mimeType || response.headers.get("Content-Type") || "application/octet-stream";
    const dispositionName = parseDispositionFilename(response.headers.get("Content-Disposition"));
    const guessedName = options?.filename
        || dispositionName
        || `${options?.prefix || "asset"}-${Date.now()}.${options?.fallbackExtension || extensionFromContentType(contentType)}`;
    const safeName = sanitizeName(guessedName);
    const base64 = arrayBufferToBase64(await response.arrayBuffer());
    return writeBase64ToUserSelectedFile(base64, safeName, contentType || mimeTypeFromFilename(safeName));
}

export async function saveTextToUserSelectedFile(
    contents: string,
    options?: { filename?: string; mimeType?: string },
) {
    const filename = sanitizeName(options?.filename || `snippet-${Date.now()}.txt`);
    const contentType = options?.mimeType || mimeTypeFromFilename(filename, "text/plain");
    if (Platform.OS === "android" && FileSystem.StorageAccessFramework) {
        const permissions = await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync();
        if (!permissions.granted) {
            throw new Error("未选择保存文件夹");
        }
        const uri = await FileSystem.StorageAccessFramework.createFileAsync(permissions.directoryUri, filename, contentType);
        await FileSystem.StorageAccessFramework.writeAsStringAsync(uri, contents);
        return { uri, filename, contentType, userVisible: true, shared: false };
    }

    const root = FileSystem.documentDirectory || FileSystem.cacheDirectory;
    if (!root) {
        throw new Error("当前设备没有可写目录");
    }
    const folder = `${root}v8-agent-os/downloads/`;
    await FileSystem.makeDirectoryAsync(folder, { intermediates: true }).catch(() => undefined);
    const uri = `${folder}${filename}`;
    await FileSystem.writeAsStringAsync(uri, contents);
    await shareLocalFile(uri, filename);
    return { uri, filename, contentType, userVisible: true, shared: true };
}

export async function openCachedFile(uri: string) {
    try {
        const canOpen = await Linking.canOpenURL(uri);
        if (!canOpen) {
            throw new Error("当前设备无法直接打开这个文件");
        }
        await Linking.openURL(uri);
        return true;
    } catch {
        return false;
    }
}
