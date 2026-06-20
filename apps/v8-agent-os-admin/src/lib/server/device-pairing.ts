import crypto from "crypto";
import fs from "fs";
import path from "path";

import { getBaseDir } from "@/lib/storage";
import { findUserById, type AdminUserRecord } from "@/lib/users";
import { readOrCreateInstanceIdentity } from "@/lib/server/instance-identity";

const PAIRING_STORE_FILE = "device_pairing_tickets.json";
const DEFAULT_PAIRING_TTL_MS = 5 * 60 * 1000;
const MAX_PAIRING_TTL_MS = 10 * 60 * 1000;
const MAX_STORED_TICKETS = 100;

export type DeviceSurface = "phone" | "cyber" | "web" | "custom";

type PairingTicketRecord = {
    id: string;
    instanceId: string;
    ownerUserId: string;
    codeHash: string;
    surface: DeviceSurface;
    adminBaseUrl: string;
    deviceName?: string;
    createdAt: string;
    expiresAt: string;
    consumedAt?: string;
};

type PairingTicketStore = {
    version: 1;
    tickets: PairingTicketRecord[];
};

export type DevicePairingTicket = {
    pairingId: string;
    instanceId: string;
    surface: DeviceSurface;
    adminBaseUrl: string;
    pairingCode: string;
    pairingUri: string;
    expiresAt: string;
};

function normalizeSurface(value: unknown): DeviceSurface {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "cyber" || normalized === "web" || normalized === "custom") {
        return normalized;
    }
    return "phone";
}

function normalizeAdminBaseUrl(value: unknown) {
    return String(value || "").trim().replace(/\/+$/, "").replace(/\/api$/, "");
}

function readStore(): PairingTicketStore {
    let existing: Partial<PairingTicketStore> = { version: 1, tickets: [] };
    try {
        const target = path.join(getBaseDir(), PAIRING_STORE_FILE);
        if (fs.existsSync(target)) {
            existing = JSON.parse(fs.readFileSync(target, "utf-8")) as Partial<PairingTicketStore>;
        }
    } catch {
        existing = { version: 1, tickets: [] };
    }
    return {
        version: 1,
        tickets: Array.isArray(existing.tickets) ? existing.tickets : [],
    };
}

function writeStore(store: PairingTicketStore) {
    const payload = {
        version: 1,
        tickets: store.tickets.slice(-MAX_STORED_TICKETS),
    } satisfies PairingTicketStore;
    const target = path.join(getBaseDir(), PAIRING_STORE_FILE);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(payload, null, 2), "utf-8");
    fs.renameSync(temporary, target);
}

function hashPairingCode(code: string) {
    return crypto.createHash("sha256").update(code).digest("hex");
}

function safeEqual(left: string, right: string) {
    const leftBuffer = Buffer.from(left);
    const rightBuffer = Buffer.from(right);
    return leftBuffer.length === rightBuffer.length && crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function pairingScheme(surface: DeviceSurface) {
    if (surface === "phone") return "v8agentosphone";
    if (surface === "cyber") return "v8agentoscyber";
    if (surface === "web") return "v8agentosweb";
    return "v8agentos";
}

function cleanupTickets(tickets: PairingTicketRecord[], nowMs: number) {
    return tickets.filter((ticket) => {
        const expiresAt = new Date(ticket.expiresAt).getTime();
        const consumedAt = ticket.consumedAt ? new Date(ticket.consumedAt).getTime() : 0;
        if (consumedAt) {
            return nowMs - consumedAt < 24 * 60 * 60 * 1000;
        }
        return Number.isFinite(expiresAt) && expiresAt > nowMs - 60 * 60 * 1000;
    });
}

export function createDevicePairingTicket(input: {
    owner: AdminUserRecord;
    surface?: unknown;
    adminBaseUrl: string;
    deviceName?: string;
    ttlMs?: number;
}): DevicePairingTicket {
    if (input.owner.role !== "ADMIN") {
        throw new Error("owner_admin_required");
    }
    const adminBaseUrl = normalizeAdminBaseUrl(input.adminBaseUrl);
    if (!adminBaseUrl) {
        throw new Error("admin_base_url_required");
    }

    const now = Date.now();
    const requestedTtl = Number(input.ttlMs || DEFAULT_PAIRING_TTL_MS);
    const ttlMs = Math.max(60_000, Math.min(MAX_PAIRING_TTL_MS, requestedTtl));
    const surface = normalizeSurface(input.surface);
    const identity = readOrCreateInstanceIdentity();
    const code = crypto.randomBytes(24).toString("base64url");
    const record: PairingTicketRecord = {
        id: crypto.randomUUID(),
        instanceId: identity.instanceId,
        ownerUserId: input.owner.id,
        codeHash: hashPairingCode(code),
        surface,
        adminBaseUrl,
        deviceName: String(input.deviceName || "").trim() || undefined,
        createdAt: new Date(now).toISOString(),
        expiresAt: new Date(now + ttlMs).toISOString(),
    };
    const store = readStore();
    store.tickets = cleanupTickets(store.tickets, now);
    store.tickets.push(record);
    writeStore(store);

    const query = new URLSearchParams({
        admin: adminBaseUrl,
        code,
        instance: identity.instanceId,
        surface,
    });
    return {
        pairingId: record.id,
        instanceId: identity.instanceId,
        surface,
        adminBaseUrl,
        pairingCode: code,
        pairingUri: `${pairingScheme(surface)}://pair?${query.toString()}`,
        expiresAt: record.expiresAt,
    };
}

export function consumeDevicePairingTicket(input: {
    code: string;
    instanceId?: string;
    deviceName?: string;
}) {
    const code = String(input.code || "").trim();
    if (!code) {
        return { ok: false as const, reason: "pairing_code_required" };
    }

    const now = Date.now();
    const codeHash = hashPairingCode(code);
    const store = readStore();
    store.tickets = cleanupTickets(store.tickets, now);
    const ticket = store.tickets.find((candidate) => safeEqual(candidate.codeHash, codeHash));
    if (!ticket) {
        writeStore(store);
        return { ok: false as const, reason: "pairing_ticket_invalid" };
    }
    if (ticket.consumedAt) {
        return { ok: false as const, reason: "pairing_ticket_consumed" };
    }
    if (new Date(ticket.expiresAt).getTime() <= now) {
        return { ok: false as const, reason: "pairing_ticket_expired" };
    }
    if (input.instanceId && input.instanceId !== ticket.instanceId) {
        return { ok: false as const, reason: "pairing_instance_mismatch" };
    }

    const owner = findUserById(ticket.ownerUserId);
    if (!owner || owner.role !== "ADMIN") {
        return { ok: false as const, reason: "pairing_owner_unavailable" };
    }

    ticket.consumedAt = new Date(now).toISOString();
    writeStore(store);
    return {
        ok: true as const,
        instanceId: ticket.instanceId,
        surface: ticket.surface,
        adminBaseUrl: ticket.adminBaseUrl,
        deviceName: String(input.deviceName || ticket.deviceName || `v8-${ticket.surface}`).trim(),
        owner,
    };
}
