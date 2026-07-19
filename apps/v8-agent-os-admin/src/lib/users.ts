import { v4 as uuidv4 } from "uuid";
import { readJson, writeJson } from "@/lib/storage";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
export type AdminUserRole = "ADMIN" | "USER";
export type UserAppearancePreferences = {
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
type AdminUsersPayload = {
  users: AdminUserRecord[];
};
function normalizeRole(role: unknown): AdminUserRole {
  return String(role || "").toUpperCase() === "ADMIN" ? "ADMIN" : "USER";
}
function normalizeAppearance(value: unknown): UserAppearancePreferences {
  const record = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const rawImage = String(record.lightBackgroundImage || "").trim().slice(0, 2048);
  let lightBackgroundImage = "";
  if (rawImage.startsWith("/user-assets/background/")) {
    lightBackgroundImage = rawImage;
  }
  return {
    lightBackgroundImage,
    lightBackgroundEnabled: Boolean(record.lightBackgroundEnabled && lightBackgroundImage),
  };
}
function normalizeUserRecord(raw: Record<string, unknown>, index: number): AdminUserRecord {
  const login = String(raw.login || raw.email || `user-${index + 1}`).trim();
  const email = String(raw.email || "").trim() || undefined;
  return {
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
export function readUsersPayload(): AdminUsersPayload {
  const data = readJson<{
    users?: Record<string, unknown>[];
  }>("users.json", {
    users: []
  });
  const rawUsers = Array.isArray(data.users) ? data.users : [];
  const normalized = rawUsers.map((item, index) => normalizeUserRecord(item, index));
  const payload = {
    users: sortUsers(normalized)
  };
  const changed = JSON.stringify(rawUsers) !== JSON.stringify(payload.users);
  if (changed) {
    writeJson("users.json", payload);
  }
  return payload;
}
export function writeUsersPayload(payload: AdminUsersPayload) {
  writeJson("users.json", {
    users: sortUsers(payload.users)
  });
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
  const normalized = String(login || "").trim().toLowerCase();
  const existing = listUsers().find(user => user.id !== excludeUserId && String(user.login || "").trim().toLowerCase() === normalized);
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
  const payload = readUsersPayload();
  assertUniqueLogin(input.login);
  const role = input.role || "USER";
  if (PERSONAL_OWNER_MODE && payload.users.some(user => user.role === "ADMIN")) {
    throw new Error("V8 Agent OS personal mode supports one Owner. Connect additional devices through pairing.");
  }
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
  writeUsersPayload(payload);
  return nextUser;
}
export function updateUserRecord(id: string, patch: Partial<Pick<AdminUserRecord, "login" | "name" | "role" | "password" | "image" | "appearance" | "mustChangePassword" | "email">>) {
  const payload = readUsersPayload();
  const target = payload.users.find(user => user.id === id);
  if (!target) {
    throw new Error(INTERNAL_READABLE.k1b2a3e6bd1);
  }
  if (typeof patch.login === "string" && patch.login.trim()) {
    assertUniqueLogin(patch.login, id);
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
  writeUsersPayload(payload);
  return target;
}
export function deleteUserRecord(id: string) {
  const payload = readUsersPayload();
  const target = payload.users.find(user => user.id === id);
  if (PERSONAL_OWNER_MODE && target?.role === "ADMIN") {
    throw new Error("The instance Owner cannot be deleted from the user list.");
  }
  payload.users = payload.users.filter(user => user.id !== id);
  writeUsersPayload(payload);
}
export function getSessionIdentifier(user: AdminUserRecord) {
  return user.email || user.login;
}
