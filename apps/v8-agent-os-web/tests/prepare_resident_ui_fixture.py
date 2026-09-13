"""Copy actual Web components with synthetic auth/API boundaries; never boot user services."""
from pathlib import Path
import shutil
import subprocess

source = Path(__file__).resolve().parents[1]
repo = source.parents[1]
target = repo / "tmp" / "resident-web-ui"
target.mkdir(parents=True, exist_ok=True)


def ignore(directory, names):
    if Path(directory) == source / "src" / "app":
        return ["api"]
    return []


for name in ("src", "public"):
    shutil.copytree(source / name, target / name, dirs_exist_ok=True, ignore=ignore)
for name in ("package.json", "tsconfig.json", "next-env.d.ts", "tailwind.config.ts", "postcss.config.mjs"):
    if (source / name).is_file():
        shutil.copy2(source / name, target / name)
if not (target / "node_modules").exists():
    subprocess.run(["powershell", "-NoProfile", "-Command", f"New-Item -ItemType Junction -Path '{target / 'node_modules'}' -Target '{source / 'node_modules'}' | Out-Null"], check=True)
(target / "next.config.ts").write_text("export default { allowedDevOrigins: ['127.0.0.1'], reactCompiler: true, transpilePackages: ['@v8/session-realtime'], experimental: { externalDir: true } };\n", encoding="utf-8")
layout = target / "src/app/layout.tsx"
text = layout.read_text(encoding="utf-8").replace('import { auth } from "@/lib/auth";', '').replace('import { resolveInitialProductTheme } from "@/lib/server/product-theme";', '')
text = text.replace('const initialSession = await auth();', 'const initialSession = { user: { id: "fixture-owner", email: "fixture@example.invalid", name: "测试用户" }, expires: "2099-01-01T00:00:00Z" };')
text = text.replace('const initialTheme = await resolveInitialProductTheme(normalizeProductTheme(themeCookie));', 'const initialTheme = { theme: "light", syncState: "synced" };')
layout.write_text(text, encoding="utf-8")
(target / "src/lib/actions/user.actions.ts").write_text('''"use server";
import { cookies } from "next/headers";
export type SharedUserProfile = { id?: string; login?: string; email?: string; name?: string; image?: string; role?: string; appearance?: any; mustChangePassword?: boolean };
export async function getUserProfile() { const jar = await cookies(); return { success: true, user: { id: "fixture-owner", name: "测试用户", email: "fixture@example.invalid", appearance: JSON.parse(jar.get("fixture-appearance")?.value || "{}") } }; }
export async function updateUserNickname() { return { success: false, error: "fixture" }; }
export async function updateUserAvatar() { return { success: false, error: "fixture" }; }
export async function updateUserAppearance(appearance: any) { (await cookies()).set("fixture-appearance", JSON.stringify(appearance)); return getUserProfile(); }
''', encoding="utf-8")
print(target)
