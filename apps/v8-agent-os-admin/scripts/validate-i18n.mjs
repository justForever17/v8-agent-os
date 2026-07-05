import fs from "fs";
import path from "path";

const root = process.cwd();
const srcRoot = path.join(root, "src");
const zhFile = path.join(srcRoot, "i18n", "locales", "zh-CN.json");
const enFile = path.join(srcRoot, "i18n", "locales", "en.json");

const zhCatalog = JSON.parse(fs.readFileSync(zhFile, "utf8"));
const enCatalog = JSON.parse(fs.readFileSync(enFile, "utf8"));

const PLACEHOLDER_PATTERN = /\{([a-zA-Z0-9_]+)\}/g;
const BANNED_PATTERN = /\b(?:lt|LocalizedText|pickLocalizedText)\b|admin-copy/;
const LEGACY_ADMIN_PATTERN = /INTERNAL_READABLE|admin\.generated\.|locale === "en"/;
const DIRECT_CHINESE_T_PATTERN = /\bt\(\s*["'`][^"'`]*[\u4e00-\u9fff][^"'`]*["'`]/;
const CHINESE_STRING_PATTERN = /["'`][^"'`]*[\u4e00-\u9fff][^"'`]*["'`]/;
const TG_PATTERN = /\btg\(\s*t\s*,\s*"([0-9a-f]+)"/g;
const EN_CHINESE_PATTERN = /[\u4e00-\u9fff]/;
const GUIDANCE_KEY_PATTERN = /(?:description|tooltip|hint|hover|notice|summary|subtitle|message|detail)/i;
const MAX_GUIDANCE_TEXT_LENGTH = 220;
const EN_CHINESE_ALLOWLIST = new Set([
    "layout.locale.label.zhCN",
]);
const HARD_CODED_CHINESE_SOURCE_FILES = new Set([
    "src/lib/product-vocabulary.ts",
]);

const SCAN_ROOTS = [
    path.join(srcRoot, "app"),
    path.join(srcRoot, "components"),
    path.join(srcRoot, "lib"),
];

const SCAN_EXCLUDES = [
    path.join(srcRoot, "app", "api"),
    path.join(srcRoot, "lib", "server"),
    path.join(srcRoot, "lib", "email.ts"),
    path.join(srcRoot, "i18n"),
    path.join(srcRoot, "lib", "locale.ts"),
    path.join(srcRoot, "lib", "actions"),
    path.join(srcRoot, "lib", "users.ts"),
    path.join(srcRoot, "lib", "service-auth.ts"),
];

function placeholdersOf(message) {
    return new Set(Array.from(String(message).matchAll(PLACEHOLDER_PATTERN)).map((match) => match[1]));
}

function sortObjectKeys(value) {
    return Object.keys(value).sort((left, right) => left.localeCompare(right));
}

function walk(dir, files = []) {
    if (!fs.existsSync(dir)) {
        return files;
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (SCAN_EXCLUDES.some((excluded) => full === excluded || full.startsWith(`${excluded}${path.sep}`))) {
            continue;
        }
        if (entry.isDirectory()) {
            walk(full, files);
            continue;
        }
        if (/\.(ts|tsx)$/.test(entry.name)) {
            files.push(full);
        }
    }
    return files;
}

function stripComments(code) {
    return code
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:\\])\/\/.*$/gm, "$1");
}

function collectMissingLegacyGeneratedKeys(code, file) {
    const missing = [];
    for (const match of code.matchAll(TG_PATTERN)) {
        const key = `admin.generated.${match[1]}`;
        if (!(key in zhCatalog) || !(key in enCatalog)) {
            missing.push(`${path.relative(root, file)} -> ${key}`);
        }
    }
    return missing;
}

const problems = [];

const zhKeys = sortObjectKeys(zhCatalog);
const enKeys = sortObjectKeys(enCatalog);
const missingInEn = zhKeys.filter((key) => !(key in enCatalog));
const missingInZh = enKeys.filter((key) => !(key in zhCatalog));

if (missingInEn.length) {
    problems.push(`en.json 缺少 ${missingInEn.length} 个 key，例如：${missingInEn.slice(0, 5).join(", ")}`);
}
if (missingInZh.length) {
    problems.push(`zh-CN.json 缺少 ${missingInZh.length} 个 key，例如：${missingInZh.slice(0, 5).join(", ")}`);
}

for (const key of zhKeys) {
    if (!(key in enCatalog)) {
        continue;
    }
    const zhPlaceholders = placeholdersOf(zhCatalog[key]);
    const enPlaceholders = placeholdersOf(enCatalog[key]);
    const zhList = Array.from(zhPlaceholders).sort();
    const enList = Array.from(enPlaceholders).sort();
    if (zhList.join("|") !== enList.join("|")) {
        problems.push(`占位符不一致：${key} -> zh=[${zhList.join(", ")}], en=[${enList.join(", ")}]`);
    }
}

for (const [key, message] of Object.entries(enCatalog)) {
    if (typeof message !== "string") {
        continue;
    }
    if (EN_CHINESE_PATTERN.test(message) && !EN_CHINESE_ALLOWLIST.has(key)) {
        problems.push(`en.json 出现中文：${key}`);
    }
    if (GUIDANCE_KEY_PATTERN.test(key) && message.length > MAX_GUIDANCE_TEXT_LENGTH) {
        problems.push(`说明文案过长：${key} -> ${message.length}/${MAX_GUIDANCE_TEXT_LENGTH}`);
    }
}

const scanFiles = SCAN_ROOTS.flatMap((dir) => walk(dir));
for (const file of scanFiles) {
    const raw = fs.readFileSync(file, "utf8");
    const code = stripComments(raw);
    const relativeFile = path.relative(root, file).replace(/\\/g, "/");

    if (BANNED_PATTERN.test(code)) {
        problems.push(`检测到旧国际化残留：${path.relative(root, file)}`);
    }
    if (LEGACY_ADMIN_PATTERN.test(code)) {
        problems.push(`检测到 legacy admin i18n 调用：${path.relative(root, file)}`);
    }
    if (DIRECT_CHINESE_T_PATTERN.test(code)) {
        problems.push(`检测到 t(\"中文\") 形式的直接中文调用：${path.relative(root, file)}`);
    }
    if (!HARD_CODED_CHINESE_SOURCE_FILES.has(relativeFile) && CHINESE_STRING_PATTERN.test(code)) {
        problems.push(`检测到疑似硬编码中文字符串：${path.relative(root, file)}`);
    }
    for (const missingKey of collectMissingLegacyGeneratedKeys(code, file)) {
        problems.push(`检测到缺失的 legacy generated key：${missingKey}`);
    }
}

if (problems.length) {
    console.error("[validate-i18n] 发现以下问题：");
    for (const problem of problems) {
        console.error(`- ${problem}`);
    }
    process.exit(1);
}

console.log(`[validate-i18n] OK. ${zhKeys.length} keys validated.`);
