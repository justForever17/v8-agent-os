"use server";

import { revalidatePath } from "next/cache";

import { hashPassword } from "@/lib/password";
import { createUserRecord, deleteUserRecord, listUsers, updateUserRecord } from "@/lib/users";

export async function getUsers() {
    return listUsers();
}

export async function createUser(formData: FormData) {
    const login = String(formData.get("login") || "").trim();
    const name = String(formData.get("name") || "").trim();
    const role = (String(formData.get("role") || "USER").trim().toUpperCase() === "ADMIN" ? "ADMIN" : "USER") as "ADMIN" | "USER";
    const password = String(formData.get("password") || "").trim();
    const mustChangePassword = String(formData.get("mustChangePassword") || "") === "on";

    if (!login || !password) {
        throw new Error("登录名和初始密码不能为空");
    }

    const passwordHash = await hashPassword(password);
    createUserRecord({
        login,
        name,
        role,
        password: passwordHash,
        mustChangePassword,
    });

    revalidatePath("/admin/users");
}

export async function updateUser(formData: FormData) {
    const id = String(formData.get("id") || "").trim();
    const login = String(formData.get("login") || "").trim();
    const name = String(formData.get("name") || "").trim();
    const role = (String(formData.get("role") || "USER").trim().toUpperCase() === "ADMIN" ? "ADMIN" : "USER") as "ADMIN" | "USER";
    const resetPassword = String(formData.get("resetPassword") || "").trim();
    const mustChangePassword = String(formData.get("mustChangePassword") || "") === "on";

    if (!id || !login) {
        throw new Error("缺少用户标识或登录名");
    }

    const patch: Parameters<typeof updateUserRecord>[1] = {
        login,
        name,
        role,
        mustChangePassword,
    };

    if (resetPassword) {
        patch.password = await hashPassword(resetPassword);
        patch.mustChangePassword = true;
    }

    updateUserRecord(id, patch);
    revalidatePath("/admin/users");
}

export async function deleteUser(id: string) {
    deleteUserRecord(id);
    revalidatePath("/admin/users");
}
