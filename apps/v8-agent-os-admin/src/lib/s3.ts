import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { Upload } from "@aws-sdk/lib-storage";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const s3Client = new S3Client({
    region: process.env.S3_REGION || "default",
    endpoint: process.env.S3_ENDPOINT || process.env.S3_CUSTOM_DOMAIN, // Use Endpoint for API calls
    credentials: {
        accessKeyId: process.env.S3_ACCESS_KEY!,
        secretAccessKey: process.env.S3_SECRET_KEY!,
    },
    forcePathStyle: true, // Required for custom S3-compatible endpoints
});

export interface UploadFileOptions {
    file: Buffer;
    filename: string;
    contentType: string;
    userId?: string; // Phase 3: Add userId for namespacing
}

function generateKey(filename: string, userId?: string) {
    const ext = filename.includes('.') ? filename.substring(filename.lastIndexOf('.')) : '';
    const timestamp = Date.now();

    if (userId) {
        // Standardized Path: users/{id}/{yyyy}/{mm}/{timestamp}{ext}
        const date = new Date();
        const year = date.getFullYear();
        const month = date.getMonth() + 1;
        return `users/${userId}/${year}/${month}/${timestamp}${ext}`;
    }

    // Legacy Path
    return `${process.env.S3_UPLOAD_PATH}/${timestamp}${ext}`;
}

export async function uploadToS3({ file, filename, contentType, userId }: UploadFileOptions): Promise<string> {
    const key = generateKey(filename, userId);

    const upload = new Upload({
        client: s3Client,
        params: {
            Bucket: process.env.S3_BUCKET!,
            Key: key,
            Body: file,
            ContentType: contentType,
        },
    });

    await upload.done();

    // Return public URL
    return `${process.env.S3_CUSTOM_DOMAIN}/${process.env.S3_BUCKET}/${key}`;
}

export async function getPresignedUploadUrl(filename: string, contentType: string, userId?: string) {
    const key = generateKey(filename, userId);

    const command = new PutObjectCommand({
        Bucket: process.env.S3_BUCKET!,
        Key: key,
        ContentType: contentType,
    });

    const url = await getSignedUrl(s3Client, command, { expiresIn: 3600 });
    return { url, key, publicUrl: `${process.env.S3_CUSTOM_DOMAIN}/${process.env.S3_BUCKET}/${key}` };
}
