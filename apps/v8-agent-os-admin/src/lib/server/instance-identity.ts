import crypto from "crypto";
import fs from "fs";
import path from "path";

import { getBaseDir } from "@/lib/storage";

const INSTANCE_FILE = path.join("runtime", "instance.json");

export type V8InstanceIdentity = {
    version: 1;
    instanceId: string;
    createdAt: string;
    product: "v8-agent-os";
};

function instanceFilePath() {
    return path.join(getBaseDir(), INSTANCE_FILE);
}

function isValidIdentity(value: unknown): value is V8InstanceIdentity {
    if (!value || typeof value !== "object") {
        return false;
    }
    const record = value as Record<string, unknown>;
    return record.version === 1
        && record.product === "v8-agent-os"
        && typeof record.instanceId === "string"
        && record.instanceId.length >= 16
        && typeof record.createdAt === "string";
}

function writeIdentity(identity: V8InstanceIdentity) {
    const target = instanceFilePath();
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(identity, null, 2), "utf-8");
    fs.renameSync(temporary, target);
}

export function readOrCreateInstanceIdentity(): V8InstanceIdentity {
    const target = instanceFilePath();
    try {
        if (fs.existsSync(target)) {
            const parsed = JSON.parse(fs.readFileSync(target, "utf-8")) as unknown;
            if (isValidIdentity(parsed)) {
                return parsed;
            }
        }
    } catch {
        // A malformed identity is replaced because it contains no user data or secret material.
    }

    const identity: V8InstanceIdentity = {
        version: 1,
        instanceId: `v8i_${crypto.randomBytes(18).toString("base64url")}`,
        createdAt: new Date().toISOString(),
        product: "v8-agent-os",
    };
    writeIdentity(identity);
    return identity;
}

