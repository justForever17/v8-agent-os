import fs from "node:fs";
import path from "node:path";
import { v4 as uuidv4 } from "uuid";
import { AdminStorageUnavailableError, getBaseDir, readJsonStrict, writeJsonStrict } from "@/lib/storage";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
export type AdminUserRole = "ADMIN" | "USER";
export type UserAppearancePreferences = {
  lightBackgroundMedia?: string;
  lightBackgroundMediaType?: "image" | "video";
  /** @deprecated Read compatibility for image-only clients. Use lightBackgroundMedia. */
  lightBackgroundImage?: string;
  lightBackgroundEnabled?: boolean;
};
export type AdminUserRecord = {
  id: string;
  login: string;
  email?: string;
  name?: string | null;
  role: AdminUserRole;
  password?: string;
  image?: string;
  appearance?: UserAppearancePreferences;
  mustChangePassword?: boolean;
  createdAt: string;
  updatedAt?: string;
};
export const PERSONAL_OWNER_MODE = true;
export const MAX_NON_ADMIN_USERS = 0;
type AdminUsersPayload = Record<string, unknown> & {
  users: AdminUserRecord[];
};
const USERS_FILENAME = "users.json";
const USERS_LOCK_FILENAME = `.${USERS_FILENAME}.lock`;
const USERS_LOCK_WAIT_MS = 5_000;
const USERS_LOCK_STALE_MS = 30_000;
const USERS_LOCK_POLL_MS = 20;
const USERS_LOCK_SLEEP = new Int32Array(new SharedArrayBuffer(4));
export class OwnerAlreadyInitializedError extends Error {
  readonly code = "owner_already_initialized";

  constructor() {
    super("V8 Agent OS personal mode already has an Owner.");
    this.name = "OwnerAlreadyInitializedError";
  }
}
export function isOwnerAlreadyInitializedError(error: unknown): error is OwnerAlreadyInitializedError {
  return error instanceof OwnerAlreadyInitializedError;
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function hasOptionalType(record: Record<string, unknown>, key: string, predicate: (value: unknown) => boolean) {
  return !(key in record) || predicate(record[key]);
}
function isValidAppearance(value: unknown) {
  if (!isRecord(value)) return false;
  return hasOptionalType(value, "lightBackgroundMedia", (item) => typeof item === "string")
    && hasOptionalType(value, "lightBackgroundImage", (item) => typeof item === "string")
    && hasOptionalType(value, "lightBackgroundMediaType", (item) => item === "image" || item === "video")
    && hasOptionalType(value, "lightBackgroundEnabled", (item) => typeof item === "boolean");
}
function invalidUsersPayload(): never {
  throw new AdminStorageUnavailableError(USERS_FILENAME, "read");
}
function assertValidUsersPayload(data: unknown): asserts data is Record<string, unknown> & { users: Record<string, unknown>[] } {
  if (!isRecord(data) || !Array.isArray(data.users)) invalidUsersPayload();

  const ids = new Set<string>();
  const logins = new Set<string>();
  let ownerCount = 0;
  for (const raw of data.users) {
    if (!isRecord(raw)) invalidUsersPayload();
    const role = typeof raw.role === "string" ? raw.role.toUpperCase() : "";
    const id = isNonEmptyString(raw.id) ? raw.id.trim() : "";
    const login = isNonEmptyString(raw.login)
      ? raw.login.trim()
      : (!("login" in raw) && isNonEmptyString(raw.email) ? raw.email.trim() : "");
    const createdAt = isNonEmptyString(raw.createdAt) ? raw.createdAt.trim() : "";
    if (("id" in raw && !id)
      || !login
      || (role !== "ADMIN" && role !== "USER")
      || ("createdAt" in raw && (!createdAt || Number.isNaN(Date.parse(createdAt))))) {
      invalidUsersPayload();
    }
    if (!hasOptionalType(raw, "email", (value) => typeof value === "string")
      || !hasOptionalType(raw, "name", (value) => value === null || typeof value === "string")
      || !hasOptionalType(raw, "password", (value) => typeof value === "string")
      || !hasOptionalType(raw, "image", (value) => typeof value === "string")
      || !hasOptionalType(raw, "appearance", isValidAppearance)
      || !hasOptionalType(raw, "mustChangePassword", (value) => typeof value === "boolean")
      || !hasOptionalType(raw, "updatedAt", (value) => (
        isNonEmptyString(value) && !Number.isNaN(Date.parse(value))
      ))) {
      invalidUsersPayload();
    }
    const normalizedLogin = login.toLowerCase();
    if ((id && ids.has(id)) || logins.has(normalizedLogin)) invalidUsersPayload();
    if (id) ids.add(id);
    logins.add(normalizedLogin);
    if (role === "ADMIN") {
      ownerCount += 1;
      if (ownerCount > 1 || !isNonEmptyString(raw.password)) invalidUsersPayload();
    }
  }
}
function normalizeRole(role: unknown): AdminUserRole {
  return String(role || "").toUpperCase() === "ADMIN" ? "ADMIN" : "USER";
}
function normalizeAppearance(value: unknown): UserAppearancePreferences {
  const record = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const rawMedia = String(record.lightBackgroundMedia || record.lightBackgroundImage || "").trim().slice(0, 2048);
  const lightBackgroundMedia = /^\/user-assets\/background\/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(?:webp|mp4)$/i.test(rawMedia)
    ? rawMedia
    : "";
  const inferredType = lightBackgroundMedia.toLowerCase().endsWith(".mp4") ? "video" : "image";
  const requestedType = String(record.lightBackgroundMediaType || "").trim().toLowerCase();
  const lightBackgroundMediaType: "image" | "video" = requestedType === "video" && inferredType === "video"
    ? "video"
    : inferredType;
  return {
    ...record,
    lightBackgroundMedia,
    lightBackgroundMediaType,
    lightBackgroundImage: lightBackgroundMediaType === "image" ? lightBackgroundMedia : "",
    lightBackgroundEnabled: Boolean(record.lightBackgroundEnabled && lightBackgroundMedia),
  };
}
function normalizeUserRecord(raw: Record<string, unknown>, index: number): AdminUserRecord {
  const login = String(raw.login || raw.email || `user-${index + 1}`).trim();
  const email = String(raw.email || "").trim() || undefined;
  return {
    ...raw,
    id: String(raw.id || uuidv4()),
    login,
    email,
    name: typeof raw.name === "string" ? raw.name : "",
    role: normalizeRole(raw.role),
    password: typeof raw.password === "string" ? raw.password : "",
    image: typeof raw.image === "string" ? raw.image : "",
    appearance: normalizeAppearance(raw.appearance),
    mustChangePassword: Boolean(raw.mustChangePassword),
    createdAt: String(raw.createdAt || new Date().toISOString()),
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : undefined
  };
}
function sortUsers(users: AdminUserRecord[]) {
  return [...users].sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime());
}
function sleepForUsersLock() {
  Atomics.wait(USERS_LOCK_SLEEP, 0, 0, USERS_LOCK_POLL_MS);
}
function processIsAlive(pid: number) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  if (pid === process.pid) return true;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException)?.code === "EPERM";
  }
}
function reclaimStaleUsersLock(lockPath: string) {
  try {
    const stat = fs.statSync(lockPath);
    if (Date.now() - stat.mtimeMs <= USERS_LOCK_STALE_MS) return false;
    let ownerPid = 0;
    try {
      ownerPid = Number(JSON.parse(fs.readFileSync(lockPath, "utf8")).pid || 0);
    } catch {}
    if (processIsAlive(ownerPid)) return false;
    const stalePath = `${lockPath}.stale.${process.pid}.${uuidv4()}`;
    fs.renameSync(lockPath, stalePath);
    fs.unlinkSync(stalePath);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code === "ENOENT") return true;
    return false;
  }
}
function withUsersWriteLock<T>(operation: () => T): T {
  const baseDir = getBaseDir();
  const lockPath = path.join(baseDir, USERS_LOCK_FILENAME);
  const deadline = Date.now() + USERS_LOCK_WAIT_MS;
  let descriptor: number | null = null;
  try {
    fs.mkdirSync(baseDir, { recursive: true });
  } catch {
    throw new AdminStorageUnavailableError(USERS_FILENAME, "write");
  }
  while (descriptor === null) {
    try {
      descriptor = fs.openSync(lockPath, "wx", 0o600);
      fs.writeFileSync(descriptor, JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }), "utf8");
      fs.fsyncSync(descriptor);
    } catch (error) {
      if (descriptor !== null) {
        try { fs.closeSync(descriptor); } catch {}
        descriptor = null;
        try { fs.unlinkSync(lockPath); } catch {}
      }
      if ((error as NodeJS.ErrnoException)?.code !== "EEXIST") {
        throw new AdminStorageUnavailableError(USERS_FILENAME, "write");
      }
      if (!reclaimStaleUsersLock(lockPath) && Date.now() >= deadline) {
        throw new AdminStorageUnavailableError(USERS_FILENAME, "write");
      }
      sleepForUsersLock();
    }
  }
  try {
    return operation();
  } finally {
    if (descriptor !== null) {
      try { fs.closeSync(descriptor); } catch {}
    }
    try {
      fs.unlinkSync(lockPath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException)?.code !== "ENOENT") {
        console.error(`Failed to release required admin storage lock ${USERS_FILENAME}:`, error);
      }
    }
  }
}
function readUsersPayloadUnlocked() {
  if (!fs.existsSync(path.join(getBaseDir(), USERS_FILENAME))) {
    return { payload: { users: [] } as AdminUsersPayload, changed: false };
  }
  const data = readJsonStrict<unknown>(USERS_FILENAME);
  assertValidUsersPayload(data);
  const rawUsers = data.users;
  const normalized = rawUsers.map((item, index) => normalizeUserRecord(item, index));
  const payload: AdminUsersPayload = {
    ...data,
    users: sortUsers(normalized)
  };
  const changed = JSON.stringify(rawUsers) !== JSON.stringify(payload.users);
  return { payload, changed };
}
function writeUsersPayloadUnlocked(payload: AdminUsersPayload) {
  writeJsonStrict(USERS_FILENAME, {
    ...payload,
    users: sortUsers(payload.users)
  });
}
export function readUsersPayload(): AdminUsersPayload {
  const snapshot = readUsersPayloadUnlocked();
  if (!snapshot.changed) return snapshot.payload;
  return withUsersWriteLock(() => {
    const latest = readUsersPayloadUnlocked();
    if (latest.changed) writeUsersPayloadUnlocked(latest.payload);
    return latest.payload;
  });
}
export function writeUsersPayload(payload: AdminUsersPayload) {
  withUsersWriteLock(() => writeUsersPayloadUnlocked(payload));
}
export function listUsers() {
  return readUsersPayload().users;
}
export function hasUsers() {
  return listUsers().length > 0;
}
export function hasOwner() {
  return listUsers().some(user => user.role === "ADMIN");
}
export function findUserById(id: string) {
  return listUsers().find(user => user.id === id) || null;
}
export function findUserByIdentifier(identifier: string) {
  const normalized = String(identifier || "").trim().toLowerCase();
  if (!normalized) return null;
  return listUsers().find(user => {
    const login = String(user.login || "").trim().toLowerCase();
    const email = String(user.email || "").trim().toLowerCase();
    return login === normalized || !!email && email === normalized;
  }) || null;
}
export function assertUniqueLogin(login: string, excludeUserId?: string) {
  assertUniqueLoginInPayload(readUsersPayload(), login, excludeUserId);
}
function assertUniqueLoginInPayload(payload: AdminUsersPayload, login: string, excludeUserId?: string) {
  const normalized = String(login || "").trim().toLowerCase();
  const existing = payload.users.find(user => user.id !== excludeUserId && String(user.login || "").trim().toLowerCase() === normalized);
  if (existing) {
    throw new Error(INTERNAL_READABLE.k29cb690784);
  }
}
export function createUserRecord(input: {
  login: string;
  name?: string;
  role?: AdminUserRole;
  password?: string;
  email?: string;
  image?: string;
  appearance?: UserAppearancePreferences;
  mustChangePassword?: boolean;
}) {
  return withUsersWriteLock(() => {
    const payload = readUsersPayloadUnlocked().payload;
    const role = input.role || "USER";
    if (PERSONAL_OWNER_MODE && payload.users.some(user => user.role === "ADMIN")) {
      throw new OwnerAlreadyInitializedError();
    }
    assertUniqueLoginInPayload(payload, input.login);
    if (PERSONAL_OWNER_MODE && role !== "ADMIN") {
      throw new Error("Only the instance Owner can be created. Connect clients through device pairing.");
    }
    if (role === "USER" && payload.users.filter(user => user.role === "USER").length >= MAX_NON_ADMIN_USERS) {
      throw new Error(`Non-owner account creation is disabled: max ${MAX_NON_ADMIN_USERS}.`);
    }
    const now = new Date().toISOString();
    const nextUser: AdminUserRecord = {
      id: uuidv4(),
      login: String(input.login || "").trim(),
      email: String(input.email || "").trim() || undefined,
      name: String(input.name || "").trim(),
      role,
      password: input.password || "",
      image: String(input.image || "").trim(),
      appearance: normalizeAppearance(input.appearance),
      mustChangePassword: Boolean(input.mustChangePassword),
      createdAt: now,
      updatedAt: now
    };
    payload.users.push(nextUser);
    writeUsersPayloadUnlocked(payload);
    return nextUser;
  });
}
export function updateUserRecord(id: string, patch: Partial<Pick<AdminUserRecord, "login" | "name" | "role" | "password" | "image" | "appearance" | "mustChangePassword" | "email">>) {
  return withUsersWriteLock(() => {
    const payload = readUsersPayloadUnlocked().payload;
    const target = payload.users.find(user => user.id === id);
    if (!target) {
      throw new Error(INTERNAL_READABLE.k1b2a3e6bd1);
    }
    if (typeof patch.login === "string" && patch.login.trim()) {
      assertUniqueLoginInPayload(payload, patch.login, id);
      target.login = patch.login.trim();
    }
    if (typeof patch.name === "string") {
      target.name = patch.name.trim();
    }
    if (patch.role) {
      const nextRole = normalizeRole(patch.role);
      if (PERSONAL_OWNER_MODE && nextRole !== "ADMIN") {
        throw new Error("The personal instance Owner cannot be converted to a non-Owner account.");
      }
      if (nextRole === "USER" && target.role !== "USER" && payload.users.filter(user => user.role === "USER").length >= MAX_NON_ADMIN_USERS) {
        throw new Error(`Non-owner account creation is disabled: max ${MAX_NON_ADMIN_USERS}.`);
      }
      target.role = nextRole;
    }
    if (typeof patch.password === "string" && patch.password) {
      target.password = patch.password;
    }
    if (typeof patch.image === "string") {
      target.image = patch.image.trim();
    }
    if (patch.appearance && typeof patch.appearance === "object") {
      target.appearance = normalizeAppearance(patch.appearance);
    }
    if (typeof patch.email === "string") {
      target.email = patch.email.trim() || undefined;
    }
    if (typeof patch.mustChangePassword === "boolean") {
      target.mustChangePassword = patch.mustChangePassword;
    }
    target.updatedAt = new Date().toISOString();
    writeUsersPayloadUnlocked(payload);
    return target;
  });
}
export function deleteUserRecord(id: string) {
  withUsersWriteLock(() => {
    const payload = readUsersPayloadUnlocked().payload;
    const target = payload.users.find(user => user.id === id);
    if (PERSONAL_OWNER_MODE && target?.role === "ADMIN") {
      throw new Error("The instance Owner cannot be deleted from the user list.");
    }
    payload.users = payload.users.filter(user => user.id !== id);
    writeUsersPayloadUnlocked(payload);
  });
}
export function getSessionIdentifier(user: AdminUserRecord) {
  return user.email || user.login;
}
