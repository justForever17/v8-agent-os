import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

// Identity moved to Engine. The retired verifier injected failures into Admin
// JSON writes and could no longer observe the actual identity owner.
const admin = path.resolve(import.meta.dirname, "../..");
const engine = path.resolve(admin, "../v8-agent-os-engine");
const candidates = process.platform === "win32"
    ? [path.join(engine, ".venv/Scripts/python.exe"), path.join(engine, ".python/python.exe")]
    : [path.join(engine, ".venv/bin/python")];
const python = process.env.V8_ENGINE_TEST_PYTHON || candidates.find(candidate => fs.existsSync(candidate)) || "python";

function run(command, args, cwd) {
    return new Promise((resolve, reject) => {
        const process = spawn(command, args, { cwd, stdio: "inherit", windowsHide: true });
        process.once("error", reject);
        process.once("exit", code => code === 0 ? resolve() : reject(new Error(`identity verification exited ${code}`)));
    });
}

await run(python, ["-m", "pytest", "-q", "tests/runtime_core/test_client_identity.py", "tests/runtime_core/test_engine_auth_context.py", "tests/client_surface/test_engine_client_assets.py"], engine);
await run(process.execPath, ["--test", "tests/engine-identity-proxy.test.cjs", "tests/background-playlist-behavior.test.cjs", "tests/user-personalization-contract.test.cjs"], admin);
console.log("Engine identity/asset ASGI and Admin HTTP proxy contracts passed. Browser and production acceptance remain separate checks.");
