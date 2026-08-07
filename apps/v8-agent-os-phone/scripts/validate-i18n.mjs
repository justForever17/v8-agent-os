import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const localeDir = path.join(workspaceRoot, "src", "i18n", "locales");
const sourceRoots = [
    path.join(workspaceRoot, "app"),
    path.join(workspaceRoot, "src"),
];

const zhCatalog = JSON.parse(fs.readFileSync(path.join(localeDir, "zh-CN.json"), "utf8"));
const enCatalog = JSON.parse(fs.readFileSync(path.join(localeDir, "en.json"), "utf8"));

const issues = [];

function flattenObject(value, prefix = "", output = {}) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        output[prefix] = value;
        return output;
    }
    for (const [key, nestedValue] of Object.entries(value)) {
        const nextKey = prefix ? `${prefix}.${key}` : key;
        if (typeof nestedValue === "object" && nestedValue !== null && !Array.isArray(nestedValue)) {
            flattenObject(nestedValue, nextKey, output);
        } else {
            output[nextKey] = nestedValue;
        }
    }
    return output;
}

function extractPlaceholders(template) {
    const matches = String(template).matchAll(/\{([a-zA-Z0-9_]+)\}/g);
    return Array.from(new Set(Array.from(matches, (match) => match[1]))).sort();
}

function addIssue(message) {
    issues.push(message);
}

function compareCatalogs() {
    const zhFlat = flattenObject(zhCatalog);
    const enFlat = flattenObject(enCatalog);
    const zhKeys = Object.keys(zhFlat).sort();
    const enKeys = Object.keys(enFlat).sort();

    for (const key of zhKeys) {
        if (!(key in enFlat)) {
            addIssue(`[catalog] Missing key in en.json: ${key}`);
        }
    }
    for (const key of enKeys) {
        if (!(key in zhFlat)) {
            addIssue(`[catalog] Extra key in en.json: ${key}`);
        }
    }

    for (const key of zhKeys) {
        if (!(key in enFlat)) {
            continue;
        }
        if (typeof zhFlat[key] !== "string") {
            addIssue(`[catalog] zh-CN value must be a string: ${key}`);
            continue;
        }
        if (typeof enFlat[key] !== "string") {
            addIssue(`[catalog] en value must be a string: ${key}`);
            continue;
        }
        const zhPlaceholders = extractPlaceholders(zhFlat[key]);
        const enPlaceholders = extractPlaceholders(enFlat[key]);
        if (zhPlaceholders.join("|") !== enPlaceholders.join("|")) {
            addIssue(
                `[catalog] Placeholder mismatch for ${key}: zh-CN=[${zhPlaceholders.join(", ")}] en=[${enPlaceholders.join(", ")}]`,
            );
        }
    }
}

function walkFiles(root, collected = []) {
    if (!fs.existsSync(root)) {
        return collected;
    }
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        const fullPath = path.join(root, entry.name);
        if (entry.isDirectory()) {
            walkFiles(fullPath, collected);
            continue;
        }
        if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) {
            collected.push(fullPath);
        }
    }
    return collected;
}

function scanSourceFiles() {
    const files = sourceRoots.flatMap((root) => walkFiles(root));
    const legacyPatterns = [
        {
            label: "dual-arg t(...)",
            regex: /\bt\(\s*(['"`])[^'"`]*[\u4e00-\u9fff][^'"`]*\1\s*,/g,
        },
        {
            label: "dual-arg tRef.current(...)",
            regex: /tRef\.current\(\s*(['"`])[^'"`]*[\u4e00-\u9fff][^'"`]*\1\s*,/g,
        },
        {
            label: "{ zh, en } localized object",
            regex: /\bzh\s*:\s*(['"`])/g,
        },
        {
            label: "legacy LocalizedText usage",
            regex: /\bLocalizedText\b/g,
        },
        {
            label: "legacy pickLocalizedText usage",
            regex: /\bpickLocalizedText\b/g,
        },
        {
            label: "legacy lt(...) usage",
            regex: /\blt\(/g,
        },
    ];

    for (const file of files) {
        const text = fs.readFileSync(file, "utf8");
        const relativePath = path.relative(workspaceRoot, file).replace(/\\/g, "/");
        const lines = text.split(/\r?\n/);

        for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index];
            if (/[\u4e00-\u9fff]/.test(line)) {
                addIssue(`[source] Hardcoded Chinese text in ${relativePath}:${index + 1}: ${line.trim()}`);
            }
        }

        for (const { label, regex } of legacyPatterns) {
            const matches = text.match(regex);
            if (matches?.length) {
                addIssue(`[source] ${label} found in ${relativePath}`);
            }
        }
    }
}

compareCatalogs();
scanSourceFiles();

if (issues.length > 0) {
    console.error("phone i18n validation failed:");
    for (const issue of issues) {
        console.error(`- ${issue}`);
    }
    process.exit(1);
}

console.log("phone i18n validation passed");
