#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const TAG_RE = /^v8-os-(phone|desktop)-v(\d{4}\.\d{2}\.\d{2}\.\d+)$/;

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function inferRelease(args) {
  const tag = args.tag || process.env.GITHUB_REF_NAME;
  let product = args.product;
  let version = args.version;

  if (tag) {
    const match = TAG_RE.exec(tag);
    if (!match) {
      throw new Error(`Invalid V8OS release tag: ${tag}`);
    }
    const tagProduct = match[1];
    const tagChannel = "";
    product = product || tagProduct;
    version = version || match[2];
    if (product !== tagProduct) {
      throw new Error(`Tag product (${tagProduct}) does not match --product (${product}).`);
    }
    if (tagChannel && args.channel && args.channel !== tagChannel) {
      throw new Error(`Tag channel (${tagChannel}) does not match --channel (${args.channel}).`);
    }
    args.channel = args.channel || tagChannel;
  }

  if (!product || !["phone", "desktop"].includes(product)) {
    throw new Error("Missing or invalid --product. Use phone or desktop.");
  }
  if (!version || !/^\d{4}\.\d{2}\.\d{2}\.\d+$/.test(version)) {
    throw new Error("Missing or invalid --version. Expected YYYY.MM.DD.N.");
  }

  return {
    product,
    version,
    tag: tag || `v8-os-${product}-v${version}`,
    channel: args.channel || "preview",
  };
}

function git(args) {
  try {
    return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

function repoUrl() {
  if (process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY) {
    return `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}`;
  }
  const remote = git(["config", "--get", "remote.origin.url"]);
  if (remote.startsWith("git@github.com:")) {
    return `https://github.com/${remote.slice("git@github.com:".length).replace(/\.git$/, "")}`;
  }
  if (remote.startsWith("https://")) {
    return remote.replace(/\.git$/, "");
  }
  return "https://github.com/justForever17/v8-agent-os";
}

function previousTag(currentTag, product) {
  const prefix = `v8-os-${product}-v`;
  const tags = git(["tag", "--list", `${prefix}*`, "--sort=-creatordate"])
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean)
    .filter((value) => value !== currentTag);
  return tags[0] || "";
}

function releaseTitle(product, version, channel) {
  const name = product === "phone" ? "V8OS Phone" : "V8OS Desktop";
  const suffix = channel === "stable" ? "Stable" : "Preview";
  return `${name} ${suffix} v${version}`;
}

function assetSection(product, version, channel) {
  if (product === "phone") {
    return [
      "- `V8OS-Phone-" + version + "-android-preview.apk`：Android 11+ 预览安装包。",
      "- `SHA256SUMS.txt`：下载文件的 SHA256 校验信息。",
      "",
      "iOS 包仍在后续发布链路中推进，本次以 Android APK 为主。",
    ].join("\n");
  }

  const desktopLabel = channel === "stable" ? "Windows 安装包" : "Windows unsigned preview 安装包";
  const desktopVersion = channel === "stable" ? version : `preview-${version}`;
  const desktopZipLabel = channel === "stable" ? "免安装包" : "免安装预览包";
  const desktopChannelNote = channel === "stable"
    ? "请确认本次桌面包已完成 stable 门禁；签名、更新和校验信息应与发布资产同批提供。"
    : "当前桌面包未签名。Windows 可能显示安全确认；代码签名、SmartScreen 信誉和自动更新属于后续阶段。";
  return [
    "- `V8-Agent-OS-" + desktopVersion + "-win-x64-setup.exe`：" + desktopLabel + "。",
    "- `V8-Agent-OS-" + desktopVersion + "-win-x64.zip`：" + desktopZipLabel + "。",
    "- `SHA256SUMS.txt`：下载文件的 SHA256 校验信息。",
    "- `RUNTIME_PROBE.json`：本次桌面包内置运行时与功能依赖探针结果；若 Git/ffmpeg 等能力显示 degraded，请按探针结果理解实际可用范围。",
    "",
    desktopChannelNote,
  ].join("\n");
}

function knownLimits(product, channel) {
  if (product === "phone") {
    return [
      "- Phone 是远程交互端，需要通过 V8OS 桌面/控制台配对。",
      "- Android 支持目标为 11 及以上；iOS 发布仍在后续完善。",
      "- 若你从旧 `phone-v*` release 升级，请优先使用新的 `v8-os-phone-v*` 版本线。",
    ].join("\n");
  }
  if (channel === "stable") {
    return [
      "- 本版本属于 desktop-stable 通道，请仅在 stable 门禁完成后发布。",
      "- Shell 会托管 Engine/Admin/Web/桌宠；退出 V8OS 时会清理受管子进程。",
      "- macOS/Linux 安装包仍在后续版本中推进。",
    ].join("\n");
  }
  return [
    "- 本版本是 Windows 预览包，不代表 stable 版本。",
    "- Shell 会托管 Engine/Admin/Web/桌宠；退出 V8OS 时会清理受管子进程。",
    "- 自动更新、代码签名、macOS/Linux 安装包仍在后续版本中推进。",
  ].join("\n");
}

function buildNotes(release) {
  const base = repoUrl();
  const prev = previousTag(release.tag, release.product);
  const changelog = prev ? `${base}/compare/${prev}...${release.tag}` : `${base}/commits/${release.tag}`;

  return `# ${releaseTitle(release.product, release.version, release.channel)}

## 下载

${assetSection(release.product, release.version, release.channel)}

## 本次版本

- 发布对象：${release.product === "phone" ? "Phone 远程端" : "桌面版"}
- 发布通道：${release.channel}
- 标签：\`${release.tag}\`

## 安装 / 更新

${release.product === "phone"
  ? "下载 APK 后安装到 Android 设备；打开 Phone 后扫码配对到你的 V8OS 桌面/控制台。"
  : "下载安装包或 zip 后启动 V8 Agent OS。首次运行会启动本机服务并打开桌面 Shell。"}

## 已知限制

${knownLimits(release.product, release.channel)}

## 校验

下载后可使用 \`SHA256SUMS.txt\` 校验文件完整性。发布页中的校验文件与资产同批生成。

## 完整更新日志

${prev ? `[${prev}...${release.tag}](${changelog})` : `[${release.tag}](${changelog})`}
`;
}

try {
  const args = parseArgs(process.argv.slice(2));
  const release = inferRelease(args);
  const notes = buildNotes(release);
  if (args.out) {
    const target = resolve(args.out);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, notes, "utf8");
  } else {
    process.stdout.write(notes);
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
