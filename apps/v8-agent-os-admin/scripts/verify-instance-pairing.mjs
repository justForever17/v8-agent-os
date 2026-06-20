import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const adminDir = process.cwd();
const nextBin = path.join(adminDir, "node_modules", "next", "dist", "bin", "next");
const buildId = path.join(adminDir, ".next", "BUILD_ID");
assert.ok(fs.existsSync(nextBin), "Next.js is not installed");
assert.ok(fs.existsSync(buildId), "Run `npm run build` before this verification");

const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-instance-pairing-"));
fs.writeFileSync(path.join(stateRoot, "users.json"), JSON.stringify({
    users: [{
        id: "legacy-user",
        login: "legacy",
        name: "Legacy user",
        role: "USER",
        password: "",
        createdAt: new Date(0).toISOString(),
    }],
}, null, 2));
const port = 19000 + crypto.randomInt(1000);
const baseUrl = `http://127.0.0.1:${port}`;
const logs = [];
const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
    cwd: adminDir,
    env: {
        ...process.env,
        V8_AGENT_OS_HOME: stateRoot,
        NEXTAUTH_URL: baseUrl,
        AUTH_SECRET: "instance-pairing-test-secret-instance-pairing-test-secret",
        NEXTAUTH_SECRET: "instance-pairing-test-secret-instance-pairing-test-secret",
    },
    stdio: ["ignore", "pipe", "pipe"],
});
server.stdout.on("data", (chunk) => logs.push(String(chunk)));
server.stderr.on("data", (chunk) => logs.push(String(chunk)));

async function request(pathname, init) {
    return fetch(`${baseUrl}${pathname}`, init);
}

async function json(response) {
    return response.json().catch(() => ({}));
}

async function waitUntilReady() {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
        try {
            const response = await request("/api/client/instance");
            if (response.ok) return json(response);
        } catch {}
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(`Admin did not become ready:\n${logs.join("")}`);
}

async function main() {
    const initialManifest = await waitUntilReady();
    assert.equal(initialManifest.ownerMode, "single_owner");
    assert.equal(initialManifest.initialized, false);
    assert.equal(initialManifest.clientGateway, "admin_bff");
    assert.equal(initialManifest.capabilities.publicRegistration, false);
    assert.ok(!("engine" in initialManifest));

    const rootRedirect = await request("/", { redirect: "manual" });
    assert.ok([307, 308].includes(rootRedirect.status), `unexpected root redirect status ${rootRedirect.status}`);
    const rootLocation = rootRedirect.headers.get("location") || "";
    assert.ok(rootLocation.endsWith("/admin") || rootLocation.includes("/admin?"), `root redirect should point at /admin, got ${rootLocation}`);
    assert.ok(!rootLocation.includes("/admin/login"), `root redirect must not point at removed /admin/login: ${rootLocation}`);
    const adminRedirect = await request("/admin", { redirect: "manual" });
    assert.ok([307, 308].includes(adminRedirect.status), `unexpected /admin redirect status ${adminRedirect.status}`);
    const adminLocation = adminRedirect.headers.get("location") || "";
    assert.ok(adminLocation.includes("/login"), `/admin redirect should point at /login, got ${adminLocation}`);
    assert.ok(!adminLocation.includes("/admin/login"), `/admin redirect must not point at removed /admin/login: ${adminLocation}`);

    const bootstrap = await request("/api/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "owner", name: "Owner", password: "owner-test-password" }),
    });
    assert.equal(bootstrap.status, 200, JSON.stringify(await json(bootstrap)));

    const adminRegistration = await request("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "extra", password: "password", name: "Extra" }),
    });
    assert.equal(adminRegistration.status, 404);

    const clientRegistration = await request("/api/client/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "extra", password: "password", name: "Extra" }),
    });
    assert.equal(clientRegistration.status, 404);

    const login = await request("/api/client/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "owner", password: "owner-test-password", deviceName: "test-owner" }),
    });
    const loginPayload = await json(login);
    assert.equal(login.status, 200, JSON.stringify(loginPayload));
    assert.ok(loginPayload.accessToken);

    const createTicket = async (surface) => {
        const response = await request("/api/client/pairing/tickets", {
            method: "POST",
            headers: {
                Authorization: `Bearer ${loginPayload.accessToken}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ surface, deviceName: `test-${surface}` }),
        });
        const payload = await json(response);
        assert.equal(response.status, 200, JSON.stringify(payload));
        assert.equal(payload.instanceId, initialManifest.instanceId);
        const pairedAdminUrl = new URL(payload.adminBaseUrl);
        assert.equal(pairedAdminUrl.protocol, "http:");
        assert.equal(pairedAdminUrl.port, String(port));
        assert.ok(["localhost", "127.0.0.1", "::1", "[::1]"].includes(pairedAdminUrl.hostname));
        assert.ok(payload.pairingCode);
        assert.ok(!payload.pairingUri.includes(loginPayload.accessToken));
        assert.ok(!("refreshToken" in payload));
        return payload;
    };

    const phoneTicket = await createTicket("phone");
    const phoneConsume = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: phoneTicket.pairingCode,
            instanceId: phoneTicket.instanceId,
            deviceName: "test-phone",
        }),
    });
    const phonePayload = await json(phoneConsume);
    assert.equal(phoneConsume.status, 200, JSON.stringify(phonePayload));
    assert.ok(phonePayload.accessToken);
    assert.ok(phonePayload.refreshToken);
    assert.equal(phonePayload.user.role, "ADMIN");
    assert.ok(!("engine" in phonePayload.linkManifest));

    const connectionResponse = await request("/api/client/connection", {
        headers: { Authorization: `Bearer ${phonePayload.accessToken}` },
    });
    const connectionPayload = await json(connectionResponse);
    assert.equal(connectionResponse.status, 200, JSON.stringify(connectionPayload));
    assert.ok(!("engineBaseUrl" in connectionPayload.connection));
    assert.ok(!("desktopLiveBridgeBaseUrl" in connectionPayload.connection));

    const devicesResponse = await request("/api/client/devices", {
        headers: { Authorization: `Bearer ${loginPayload.accessToken}` },
    });
    const devicesPayload = await json(devicesResponse);
    assert.equal(devicesResponse.status, 200, JSON.stringify(devicesPayload));
    const phoneDevice = devicesPayload.devices.find((device) => device.deviceName === "test-phone");
    assert.ok(phoneDevice?.id);

    const revokeResponse = await request("/api/client/devices", {
        method: "DELETE",
        headers: {
            Authorization: `Bearer ${loginPayload.accessToken}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ deviceSessionId: phoneDevice.id }),
    });
    assert.equal(revokeResponse.status, 200, JSON.stringify(await json(revokeResponse)));
    const revokedMeResponse = await request("/api/client/auth/me", {
        headers: { Authorization: `Bearer ${phonePayload.accessToken}` },
    });
    assert.equal(revokedMeResponse.status, 401);

    const replay = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: phoneTicket.pairingCode }),
    });
    assert.equal(replay.status, 410);
    assert.equal((await json(replay)).error, "pairing_ticket_consumed");

    const expiredTicket = await createTicket("phone");
    const pairingStoreFile = path.join(stateRoot, "device_pairing_tickets.json");
    const pairingStore = JSON.parse(fs.readFileSync(pairingStoreFile, "utf8"));
    const expiredRecord = pairingStore.tickets.find((ticket) => ticket.id === expiredTicket.pairingId);
    assert.ok(expiredRecord, "created pairing ticket must be persisted before consume");
    expiredRecord.expiresAt = new Date(Date.now() - 1_000).toISOString();
    fs.writeFileSync(pairingStoreFile, JSON.stringify(pairingStore, null, 2), "utf8");
    const expiredConsume = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: expiredTicket.pairingCode,
            instanceId: expiredTicket.instanceId,
        }),
    });
    assert.equal(expiredConsume.status, 410);
    assert.equal((await json(expiredConsume)).error, "pairing_ticket_expired");

    const webTicket = await createTicket("web");
    const webConsume = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: webTicket.pairingCode,
            instanceId: webTicket.instanceId,
            sessionKind: "web_session",
        }),
    });
    const webPayload = await json(webConsume);
    assert.equal(webConsume.status, 200, JSON.stringify(webPayload));
    assert.equal(webPayload.user.role, "ADMIN");
    assert.ok(!("accessToken" in webPayload));
    assert.ok(!("refreshToken" in webPayload));

    const cyberTicket = await createTicket("cyber");
    assert.ok(cyberTicket.pairingUri.startsWith("v8agentoscyber://pair?"));
    const cyberConsume = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: cyberTicket.pairingCode,
            instanceId: cyberTicket.instanceId,
            deviceName: "test-cyber",
        }),
    });
    const cyberPayload = await json(cyberConsume);
    assert.equal(cyberConsume.status, 200, JSON.stringify(cyberPayload));
    assert.ok(cyberPayload.accessToken);

    const identityFile = path.join(stateRoot, "runtime", "instance.json");
    const identityText = fs.readFileSync(identityFile, "utf8");
    assert.ok(identityText.includes(initialManifest.instanceId));
    assert.ok(!identityText.includes("secret"));

    console.log(JSON.stringify({
        ok: true,
        instanceId: initialManifest.instanceId,
        checks: [
            "single_owner_manifest",
            "legacy_user_does_not_block_owner_bootstrap",
            "client_manifest_exposes_admin_bff_only",
            "root_redirects_to_login",
            "registration_routes_removed",
            "owner_bootstrap_and_login",
            "phone_pairing_single_use",
            "expired_pairing_ticket_rejected",
            "device_revocation_invalidates_new_access_tokens",
            "web_identity_pairing_without_device_token",
            "cybercore_pairing_uses_admin_bff_device_token",
            "instance_manifest_contains_no_secret",
        ],
    }, null, 2));
}

try {
    await main();
} finally {
    server.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
