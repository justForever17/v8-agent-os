import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const srcRoot = path.join(root, "src");
const zhFile = path.join(srcRoot, "i18n", "locales", "zh-CN.json");
const enFile = path.join(srcRoot, "i18n", "locales", "en.json");

const zhCatalog = JSON.parse(fs.readFileSync(zhFile, "utf8"));
const enCatalog = JSON.parse(fs.readFileSync(enFile, "utf8"));

const PLACEHOLDER_PATTERN = /\{([a-zA-Z0-9_]+)\}/g;
const LEGACY_PATTERN = /\blt\s*\(|\bLocalizedText\b|\bpickLocalizedText\b/;
const DIRECT_CHINESE_T_PATTERN = /\bt\(\s*["'`][^"'`]*[\u4e00-\u9fff][^"'`]*["'`]/;
const DIRECT_CHINESE_RESOLVE_PATTERN = /\bresolveText\(\s*[^,]+,\s*["'`][^"'`]*[\u4e00-\u9fff][^"'`]*["'`]/;
const TRANSLATION_KEY_PATTERN = /["'`](web\.generated\.[0-9a-f]+)["'`]/g;

const SCAN_ROOTS = [
    path.join(srcRoot, "app"),
    path.join(srcRoot, "components"),
    path.join(srcRoot, "lib"),
];

const SCAN_EXCLUDES = [
    path.join(srcRoot, "app", "api"),
    path.join(srcRoot, "i18n"),
];

function placeholdersOf(message) {
    return new Set(Array.from(String(message).matchAll(PLACEHOLDER_PATTERN)).map((match) => match[1]));
}

function sortedKeys(value) {
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

const problems = [];

const zhKeys = sortedKeys(zhCatalog);
const enKeys = sortedKeys(enCatalog);
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
    if (typeof zhCatalog[key] !== "string") {
        problems.push(`zh-CN.json 的值必须是字符串：${key}`);
        continue;
    }
    if (typeof enCatalog[key] !== "string") {
        problems.push(`en.json 的值必须是字符串：${key}`);
        continue;
    }
    const zhPlaceholders = Array.from(placeholdersOf(zhCatalog[key])).sort();
    const enPlaceholders = Array.from(placeholdersOf(enCatalog[key])).sort();
    if (zhPlaceholders.join("|") !== enPlaceholders.join("|")) {
        problems.push(`占位符不一致：${key} -> zh=[${zhPlaceholders.join(", ")}], en=[${enPlaceholders.join(", ")}]`);
    }
}

const scanFiles = SCAN_ROOTS.flatMap((dir) => walk(dir));
for (const file of scanFiles) {
    const raw = fs.readFileSync(file, "utf8");
    const code = stripComments(raw);
    const relativeFile = path.relative(root, file);

    if (LEGACY_PATTERN.test(code)) {
        problems.push(`检测到旧 web i18n 残留：${relativeFile}`);
    }
    if (DIRECT_CHINESE_T_PATTERN.test(code)) {
        problems.push(`检测到 t("中文") 形式的直接中文调用：${relativeFile}`);
    }
    if (DIRECT_CHINESE_RESOLVE_PATTERN.test(code)) {
        problems.push(`检测到 resolveText(locale, "中文") 形式的直接中文调用：${relativeFile}`);
    }

    for (const match of code.matchAll(TRANSLATION_KEY_PATTERN)) {
        const key = match[1];
        if (!(key in zhCatalog) || !(key in enCatalog)) {
            problems.push(`检测到缺失的 web 翻译 key：${relativeFile} -> ${key}`);
        }
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
