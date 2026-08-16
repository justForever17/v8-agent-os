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
const usersFile = path.join(stateRoot, "users.json");
const strictWriteFailureMarker = path.join(stateRoot, "strict-write-failure.marker");
const strictWriteFailureHook = path.join(stateRoot, "strict-write-failure-hook.cjs");
fs.writeFileSync(strictWriteFailureHook, String.raw`
const fs = require("node:fs");
const path = require("node:path");

const marker = process.env.V8_TEST_STRICT_WRITE_FAILURE_MARKER;
const target = path.resolve(process.env.V8_TEST_STRICT_WRITE_TARGET || "");
const targetDirectory = path.dirname(target);
const temporaryPrefix = "." + path.basename(target) + ".";
const temporaryDescriptors = new Set();
const originalOpenSync = fs.openSync;
const originalWriteFileSync = fs.writeFileSync;
const originalCloseSync = fs.closeSync;
const originalRenameSync = fs.renameSync;

function failureMode() {
    try {
        return fs.readFileSync(marker, "utf8").trim();
    } catch {
        return "";
    }
}

function isStrictTemporaryFile(candidate) {
    if (typeof candidate !== "string") return false;
    const resolved = path.resolve(candidate);
    const basename = path.basename(resolved);
    return path.dirname(resolved) === targetDirectory
        && basename.startsWith(temporaryPrefix)
        && basename.endsWith(".tmp");
}

fs.openSync = function patchedOpenSync(candidate, ...args) {
    const descriptor = originalOpenSync.call(fs, candidate, ...args);
    if (isStrictTemporaryFile(candidate)) temporaryDescriptors.add(descriptor);
    return descriptor;
};

fs.writeFileSync = function patchedWriteFileSync(candidate, ...args) {
    if (typeof candidate === "number"
        && temporaryDescriptors.has(candidate)
        && failureMode() === "temp_write") {
        throw Object.assign(new Error("Injected strict temporary write failure"), { code: "ENOSPC" });
    }
    return originalWriteFileSync.call(fs, candidate, ...args);
};

fs.closeSync = function patchedCloseSync(descriptor) {
    try {
        return originalCloseSync.call(fs, descriptor);
    } finally {
        temporaryDescriptors.delete(descriptor);
    }
};

fs.renameSync = function patchedRenameSync(oldPath, newPath) {
    if (typeof newPath === "string" && path.resolve(newPath) === target && failureMode() === "replace") {
        throw Object.assign(new Error("Injected strict replace failure"), { code: "EPERM" });
    }
    return originalRenameSync.call(fs, oldPath, newPath);
};
`, "utf8");
fs.writeFileSync(usersFile, JSON.stringify({
    migrationMetadata: { source: "legacy-admin-store" },
    users: [{
        login: "legacy",
        name: "Legacy user",
        role: "USER",
        password: "",
        legacyRecordMetadata: { preserve: true },
        appearance: { legacyAppearanceMetadata: "preserve" },
    }],
}, null, 2));
const port = 19000 + crypto.randomInt(1000);
const baseUrl = `http://127.0.0.1:${port}`;
const logs = [];
const nodeOptions = [
    String(process.env.NODE_OPTIONS || "").trim(),
    `--require ${JSON.stringify(strictWriteFailureHook)}`,
].filter(Boolean).join(" ");
function spawnAdminServer(serverPort, serverBaseUrl, serverLogs) {
    const child = spawn(process.execPath, [nextBin, "start", "-p", String(serverPort)], {
        cwd: adminDir,
        env: {
            ...process.env,
            V8_AGENT_OS_HOME: stateRoot,
            NEXTAUTH_URL: serverBaseUrl,
            AUTH_SECRET: "instance-pairing-test-secret-instance-pairing-test-secret",
            NEXTAUTH_SECRET: "instance-pairing-test-secret-instance-pairing-test-secret",
            NODE_OPTIONS: nodeOptions,
            V8_TEST_STRICT_WRITE_FAILURE_MARKER: strictWriteFailureMarker,
            V8_TEST_STRICT_WRITE_TARGET: usersFile,
        },
        stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.on("data", (chunk) => serverLogs.push(String(chunk)));
    child.stderr.on("data", (chunk) => serverLogs.push(String(chunk)));
    return child;
}
const server = spawnAdminServer(port, baseUrl, logs);
let secondaryServer = null;

async function request(pathname, init) {
    return fetch(`${baseUrl}${pathname}`, init);
}

async function requestAt(targetBaseUrl, pathname, init) {
    return fetch(`${targetBaseUrl}${pathname}`, init);
}

async function json(response) {
    return response.json().catch(() => ({}));
}

async function waitUntilReady(targetBaseUrl = baseUrl, targetLogs = logs) {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
        try {
            const response = await requestAt(targetBaseUrl, "/api/client/instance");
            if (response.ok) return json(response);
        } catch {}
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(`Admin did not become ready:\n${targetLogs.join("")}`);
}

function sha256(file) {
    return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function storedUser(id, login, role = "USER", overrides = {}) {
    return {
        id,
        login,
        name: login,
        role,
        password: role === "ADMIN" ? "stored-owner-password-hash" : "",
        createdAt: new Date(0).toISOString(),
        ...overrides,
    };
}

function strictTemporaryFiles() {
    const prefix = `.${path.basename(usersFile)}.`;
    return fs.readdirSync(path.dirname(usersFile)).filter((filename) => (
        filename.startsWith(prefix) && filename.endsWith(".tmp")
    ));
}

async function assertStrictWriteFailsSafely(mode) {
    fs.writeFileSync(usersFile, JSON.stringify({ users: [] }, null, 2), "utf8");
    fs.writeFileSync(strictWriteFailureMarker, mode, "utf8");
    const expectedDigest = sha256(usersFile);
    try {
        const bootstrap = await request("/api/auth/bootstrap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ login: "failure-test", name: "Failure Test", password: "failure-test-password" }),
        });
        const payload = await json(bootstrap);
        assert.equal(bootstrap.status, 503, `${mode}: ${JSON.stringify(payload)}`);
        assert.equal(payload.code, "owner_state_unavailable", mode);
        assert.equal(sha256(usersFile), expectedDigest, `${mode}: failed write must preserve users.json`);
        assert.deepEqual(strictTemporaryFiles(), [], `${mode}: failed write must clean its temporary file`);
    } finally {
        fs.rmSync(strictWriteFailureMarker, { force: true });
    }
}

async function assertOwnerStateFailsClosed(usersFile, label, content) {
    fs.writeFileSync(usersFile, content, "utf8");
    const expectedDigest = sha256(usersFile);
    const manifest = await request("/api/client/instance");
    const manifestPayload = await json(manifest);
    assert.equal(manifest.status, 503, `${label}: ${JSON.stringify(manifestPayload)}`);
    assert.equal(manifestPayload.error, "owner_state_unavailable", label);
    assert.equal(sha256(usersFile), expectedDigest, `${label}: manifest must not mutate users.json`);

    const loginPage = await request("/login");
    const loginHtml = await loginPage.text();
    assert.equal(loginPage.status, 200, `${label}: login page must render a recovery surface`);
    assert.match(loginHtml, /data-v8os-owner-state-unavailable="true"/, label);
    assert.equal(sha256(usersFile), expectedDigest, `${label}: login page must not mutate users.json`);

    const bootstrap = await request("/api/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "replacement", name: "Replacement", password: "replacement-password" }),
    });
    const bootstrapPayload = await json(bootstrap);
    assert.equal(bootstrap.status, 503, `${label}: ${JSON.stringify(bootstrapPayload)}`);
    assert.equal(bootstrapPayload.code, "owner_state_unavailable", label);
    assert.equal(sha256(usersFile), expectedDigest, `${label}: bootstrap must not mutate users.json`);
}

async function main() {
    const initialManifest = await waitUntilReady();
    assert.equal(initialManifest.ownerMode, "single_owner");
    assert.equal(initialManifest.initialized, false);
    assert.equal(initialManifest.clientGateway, "admin_bff");
    assert.equal(initialManifest.capabilities.publicRegistration, false);
    assert.equal(initialManifest.capabilities.localTrustedSession, true);
    assert.ok(initialManifest.capabilities.localTrustedSurfaces.includes("web"));
    assert.ok(initialManifest.capabilities.localTrustedSurfaces.includes("cyber"));
    assert.ok(!("engine" in initialManifest));
    const migratedLegacyState = JSON.parse(fs.readFileSync(path.join(stateRoot, "users.json"), "utf8"));
    const migratedLegacyUser = migratedLegacyState.users[0];
    const migratedLegacyUserId = migratedLegacyUser.id;
    assert.match(migratedLegacyUserId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    assert.ok(!Number.isNaN(Date.parse(migratedLegacyUser.createdAt)));
    assert.deepEqual(migratedLegacyState.migrationMetadata, { source: "legacy-admin-store" });
    assert.deepEqual(migratedLegacyUser.legacyRecordMetadata, { preserve: true });
    assert.equal(migratedLegacyUser.appearance.legacyAppearanceMetadata, "preserve");
    const migratedLegacyStateText = fs.readFileSync(usersFile, "utf8");

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

    await assertStrictWriteFailsSafely("temp_write");
    await assertStrictWriteFailsSafely("replace");
    fs.writeFileSync(usersFile, migratedLegacyStateText, "utf8");

    const secondaryPort = port + 1_500;
    const secondaryBaseUrl = `http://127.0.0.1:${secondaryPort}`;
    const secondaryLogs = [];
    secondaryServer = spawnAdminServer(secondaryPort, secondaryBaseUrl, secondaryLogs);
    await waitUntilReady(secondaryBaseUrl, secondaryLogs);
    const concurrentOwners = [
        { baseUrl, login: "owner-a", name: "Owner A", password: "owner-a-test-password" },
        { baseUrl: secondaryBaseUrl, login: "owner-b", name: "Owner B", password: "owner-b-test-password" },
    ];
    const concurrentResponses = await Promise.all(concurrentOwners.map((candidate) => requestAt(
        candidate.baseUrl,
        "/api/auth/bootstrap",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(candidate),
        },
    )));
    const concurrentPayloads = await Promise.all(concurrentResponses.map(json));
    assert.deepEqual(
        concurrentResponses.map((response) => response.status).sort((left, right) => left - right),
        [200, 409],
        JSON.stringify(concurrentPayloads),
    );
    const winningIndex = concurrentResponses.findIndex((response) => response.status === 200);
    const winner = concurrentOwners[winningIndex];
    const concurrentState = JSON.parse(fs.readFileSync(usersFile, "utf8"));
    const persistedOwners = concurrentState.users.filter((user) => user.role === "ADMIN");
    assert.equal(persistedOwners.length, 1);
    assert.equal(persistedOwners[0].login, winner.login);
    const concurrentLogin = await request("/api/client/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: winner.login, password: winner.password, deviceName: "concurrent-owner" }),
    });
    assert.equal(concurrentLogin.status, 200, JSON.stringify(await json(concurrentLogin)));
    assert.equal(fs.existsSync(path.join(stateRoot, ".users.json.lock")), false);
    secondaryServer.kill("SIGTERM");
    secondaryServer = null;
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.writeFileSync(usersFile, migratedLegacyStateText, "utf8");

    const bootstrap = await request("/api/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "owner", name: "Owner", password: "owner-test-password" }),
    });
    assert.equal(bootstrap.status, 200, JSON.stringify(await json(bootstrap)));

    const staleBootstrap = await request("/api/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "owner", name: "Owner", password: "owner-test-password" }),
    });
    const staleBootstrapPayload = await json(staleBootstrap);
    assert.equal(staleBootstrap.status, 409, JSON.stringify(staleBootstrapPayload));
    assert.equal(staleBootstrapPayload.code, "owner_already_initialized");

    const localWebSession = await request("/api/client/auth/local-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface: "web", deviceName: "test-web-local" }),
    });
    const localWebPayload = await json(localWebSession);
    assert.equal(localWebSession.status, 200, JSON.stringify(localWebPayload));
    assert.equal(localWebPayload.surface, "web");
    assert.equal(localWebPayload.trustedLocal, true);
    assert.ok(localWebPayload.accessToken);
    assert.ok(localWebPayload.refreshToken);
    assert.ok(!("engine" in localWebPayload.linkManifest));
    const localWebMe = await request("/api/client/auth/me", {
        headers: { Authorization: `Bearer ${localWebPayload.accessToken}` },
    });
    const localWebMePayload = await json(localWebMe);
    assert.equal(localWebMe.status, 200, JSON.stringify(localWebMePayload));
    assert.equal(localWebMePayload.user.role, "ADMIN");

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

    const reachablePhoneOrigin = `http://192.168.50.7:${port}`;
    const createTicket = async (surface, init = {}) => {
        const response = await request("/api/client/pairing/tickets", {
            method: "POST",
            headers: {
                Authorization: `Bearer ${loginPayload.accessToken}`,
                "Content-Type": "application/json",
                ...(init.headers || {}),
            },
            body: JSON.stringify({ surface, deviceName: `test-${surface}` }),
        });
        const payload = await json(response);
        assert.equal(response.status, 200, JSON.stringify(payload));
        assert.equal(payload.instanceId, initialManifest.instanceId);
        assert.ok(payload.pairingCode);
        assert.ok(!payload.pairingUri.includes(loginPayload.accessToken));
        assert.ok(!("refreshToken" in payload));
        return payload;
    };

    const rejectNonPhoneTicket = async (surface) => {
        const response = await request("/api/client/pairing/tickets", {
            method: "POST",
            headers: {
                Authorization: `Bearer ${loginPayload.accessToken}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ surface, deviceName: `test-${surface}` }),
        });
        const payload = await json(response);
        assert.equal(response.status, 400, JSON.stringify(payload));
        assert.equal(payload.error, "phone_pairing_only");
    };

    await rejectNonPhoneTicket("web");
    await rejectNonPhoneTicket("cyber");

    const phoneTicket = await createTicket("phone", {
        headers: { "x-v8-client-surface-origin": reachablePhoneOrigin },
    });
    assert.equal(phoneTicket.adminBaseUrl, reachablePhoneOrigin);
    assert.ok(phoneTicket.pairingUri.includes(encodeURIComponent(reachablePhoneOrigin)));
    const staleWebSessionConsume = await request("/api/client/pairing/consume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: phoneTicket.pairingCode,
            instanceId: phoneTicket.instanceId,
            sessionKind: "web_session",
        }),
    });
    assert.equal(staleWebSessionConsume.status, 400);
    assert.equal((await json(staleWebSessionConsume)).error, "web_local_session_required");

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

    const identityFile = path.join(stateRoot, "runtime", "instance.json");
    const identityText = fs.readFileSync(identityFile, "utf8");
    assert.ok(identityText.includes(initialManifest.instanceId));
    assert.ok(!identityText.includes("secret"));

    const legacyOwnerState = JSON.parse(fs.readFileSync(usersFile, "utf8"));
    const legacyOwner = legacyOwnerState.users.find((user) => user.role === "ADMIN");
    assert.ok(legacyOwner, "bootstrapped Owner must be persisted");
    legacyOwner.role = "admin";
    delete legacyOwner.updatedAt;
    fs.writeFileSync(usersFile, JSON.stringify(legacyOwnerState, null, 2), "utf8");
    const legacyOwnerManifest = await request("/api/client/instance");
    const legacyOwnerManifestPayload = await json(legacyOwnerManifest);
    assert.equal(legacyOwnerManifest.status, 200, JSON.stringify(legacyOwnerManifestPayload));
    assert.equal(legacyOwnerManifestPayload.initialized, true);
    const migratedOwnerState = JSON.parse(fs.readFileSync(usersFile, "utf8"));
    assert.equal(migratedOwnerState.users.find((user) => user.id === legacyOwner.id).role, "ADMIN");
    assert.deepEqual(migratedOwnerState.migrationMetadata, { source: "legacy-admin-store" });
    assert.deepEqual(migratedOwnerState.users.find((user) => user.id === migratedLegacyUserId).legacyRecordMetadata, { preserve: true });
    const legacyOwnerLogin = await request("/api/client/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "owner", password: "owner-test-password", deviceName: "legacy-owner-login" }),
    });
    assert.equal(legacyOwnerLogin.status, 200, JSON.stringify(await json(legacyOwnerLogin)));

    const ownerWithoutPassword = storedUser("owner-without-password", "owner-without-password", "ADMIN");
    delete ownerWithoutPassword.password;
    const invalidOwnerStates = [
        ["missing users property", "{}"],
        ["null users", '{"users":null}'],
        ["null user record", '{"users":[null]}'],
        ["blank user id", JSON.stringify({
            users: [{
                id: " ",
                login: "broken-owner",
                name: "Broken Owner",
                role: "ADMIN",
                password: "not-a-real-password-hash",
                createdAt: new Date(0).toISOString(),
            }],
        })],
        ["invalid createdAt", JSON.stringify({ users: [
            storedUser("invalid-created-at", "invalid-created-at", "USER", { createdAt: "not-a-date" }),
        ] })],
        ["Owner without password", JSON.stringify({ users: [ownerWithoutPassword] })],
        ["duplicate user id", JSON.stringify({ users: [
            storedUser("duplicate-id", "first-login"),
            storedUser("duplicate-id", "second-login"),
        ] })],
        ["duplicate login", JSON.stringify({ users: [
            storedUser("first-id", "Duplicate-Login"),
            storedUser("second-id", "duplicate-login"),
        ] })],
        ["multiple Owners", JSON.stringify({ users: [
            storedUser("first-owner", "first-owner", "ADMIN"),
            storedUser("second-owner", "second-owner", "ADMIN"),
        ] })],
        ["invalid updatedAt", JSON.stringify({ users: [
            storedUser("invalid-updated-at", "invalid-updated-at", "USER", { updatedAt: "not-a-date" }),
        ] })],
        ["malformed JSON", '{"users":['],
    ];
    for (const [label, content] of invalidOwnerStates) {
        await assertOwnerStateFailsClosed(usersFile, label, content);
    }
    assert.deepEqual(strictTemporaryFiles(), [], "strict writes must leave no temporary files");

    console.log(JSON.stringify({
        ok: true,
        instanceId: initialManifest.instanceId,
        checks: [
            "single_owner_manifest",
            "legacy_user_does_not_block_owner_bootstrap",
            "client_manifest_exposes_admin_bff_only",
            "web_local_trusted_session_uses_admin_bff_token",
            "root_redirects_to_login",
            "registration_routes_removed",
            "owner_bootstrap_and_login",
            "strict_atomic_write_failures_preserve_owner_state",
            "cross_process_owner_bootstrap_is_atomic",
            "legacy_owner_migration_and_login",
            "legacy_missing_identity_fields_migrate_atomically",
            "unknown_owner_metadata_preserved",
            "stale_owner_bootstrap_returns_stable_conflict_code",
            "non_phone_pairing_surface_rejected",
            "phone_pairing_single_use",
            "web_pairing_session_kind_rejected",
            "expired_pairing_ticket_rejected",
            "device_revocation_invalidates_new_access_tokens",
            "instance_manifest_contains_no_secret",
            "malformed_and_invalid_owner_state_fail_closed_without_overwrite",
            "login_page_surfaces_owner_state_recovery_without_overwrite",
        ],
    }, null, 2));
}

try {
    await main();
} finally {
    secondaryServer?.kill("SIGTERM");
    server.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
