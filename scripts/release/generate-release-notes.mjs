#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import {
  isValidReleaseVersion,
  LEGACY_PRODUCT_TAG_RE as LEGACY_TAG_RE,
  UNIFIED_TAG_RE,
  loadReleaseManifest,
} from "./release-manifest.mjs";

function isReleaseTag(tag) {
  return UNIFIED_TAG_RE.test(tag) || LEGACY_TAG_RE.test(tag);
}

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
  const environmentTag = String(process.env.GITHUB_REF_NAME || "").trim();
  // An explicit product or version is a self-contained release-note request.
  // Do not let a surrounding, unrelated tag (for example a desktop tag while
  // CI checks the Phone notes fixture) silently change its product.
  const hasExplicitReleaseIdentity = Boolean(args.tag || args.product || args.version);
  const tag = args.tag || (!hasExplicitReleaseIdentity && isReleaseTag(environmentTag) ? environmentTag : "");
  let product = args.product;
  let version = args.version;

  if (tag) {
    const unifiedMatch = UNIFIED_TAG_RE.exec(tag);
    const legacyMatch = LEGACY_TAG_RE.exec(tag);
    if (!unifiedMatch && !legacyMatch) {
      throw new Error(`Invalid V8OS release tag: ${tag}`);
    }
    const tagProduct = unifiedMatch ? "all" : legacyMatch[1];
    product = product || tagProduct;
    version = version || (unifiedMatch ? unifiedMatch[1] : legacyMatch[2]);
    if (unifiedMatch && !["all", "phone", "desktop"].includes(product)) {
      throw new Error(`Unified tag cannot generate notes for product ${product}.`);
    }
    if (legacyMatch && product !== tagProduct) {
      throw new Error(`Tag product (${tagProduct}) does not match --product (${product}).`);
    }
  }

  if (!product || !["phone", "desktop", "all"].includes(product)) {
    throw new Error("Missing or invalid --product. Use phone, desktop, or all.");
  }
  if (!isValidReleaseVersion(version)) {
    throw new Error("Missing or invalid --version. Expected a real UTC date in YYYY.MM.DD.N form, year 2000-2099, and N 1-99 without a leading zero.");
  }

  const manifest = args.manifest ? loadReleaseManifest(args.manifest).manifest : null;
  if (manifest && (manifest.release.version !== version || manifest.release.channel !== (args.channel || "preview"))) {
    throw new Error("Release notes manifest identity differs from the requested release");
  }
  return {
    product,
    version,
    tag: tag || (product === "all" ? `v8-os-v${version}` : `v8-os-${product}-v${version}`),
    channel: args.channel || "preview",
    products: manifest?.products,
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
  const prefix = product === "all" ? "v8-os-v" : `v8-os-${product}-v`;
  const tags = git(["tag", "--list", `${prefix}*`, "--sort=-creatordate"])
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean)
    .filter((value) => value !== currentTag);
  return tags[0] || "";
}

function releaseTitle(product, version, channel) {
  if (product === "all") {
    return `V8 Agent OS ${channel === "stable" ? "Stable" : "Preview"} v${version}`;
  }
  const name = product === "phone" ? "V8OS Phone" : "V8OS Desktop";
  const suffix = channel === "stable" ? "Stable" : "Preview";
  return `${name} ${suffix} v${version}`;
}

const RELEASE_HIGHLIGHTS = Object.freeze({
  "2026.09.17.1": Object.freeze({
    all: Object.freeze([
      "修复首条回复的正文漏显、等待气泡跳变和刷新后失败原因丢失；等待显示轻量三点，失败后可恢复原输入继续编辑，不会自动重发。",
      "新增完整对话分支和消息修订；修改后的上下文在 Web 与 Phone 同步，旧运行的迟到结果不会混入新分支。",
      "新增独立 Linux x64 Server 和可通过 npm 安装的 TUI：保留无头浏览器、Phone 与受信设备组网，重型能力按需安装；终端支持命令候选、配置管理、审批、草稿恢复和长消息增量显示。",
      "控制台 Phone 连接入口更易点击，二维码使用 Engine 提供的可配对地址；未配置远程连接时直接引导设置，避免把回环地址交给手机。",
      "Phone 可管理主设备向受信从设备分发模型策略与角色配置；各设备独立映射模型，离线或失败时明确展示部分结果并支持恢复，不分发密钥和授权。修复连续凭据续期失效及失败请求占用连接名额，保持长时间使用和出错后的操作连续性。",
      "Canvas 支持多实体参考图集、镜头和视频控制素材预览；RPA 补齐操作录制、步骤编辑、执行与取消反馈。参考素材用于视觉引导，不承诺生成模型严格复现几何位置。",
      "内置角色提示词按专业职责强化，用户可编辑内容与内部运行指导分离；多媒体角色补充人物、场景、动作、镜头、时序和参考素材的细致要求。",
      "Android 远端执行器提供手动启用的截图、画面操作和本机停止；已在 Android 14 实机验证原生截图、点击、滑动与撤销后重连，其他系统版本和厂商仍需验证，保持实验性入口。",
    ]),
  }),
  "2026.09.16.4": Object.freeze({
    all: Object.freeze([
      "并行模型调用按预计用量预留预算、按实际用量结算；用量未知时保留占额，避免多个任务同时透支剩余额度。自动输出模式不因此增加单次输出上限。",
      "供应商熔断统一约束首选和备选模型，恢复窗口只放行一个探测；聊天、嵌入与重排调用共用计量和恢复规则，本地缓存不会消耗远端调用额度。",
      "工程与委派任务支持传递明确的截止时间，过期排队任务不再启动，运行中到期按取消流程停止并拒绝迟到结果。未设置期限的任务保持原有行为。",
      "同一状态目录拒绝重复启动 Engine，并保留多 Agent 在项目内的并行执行；异常退出后可重新启动并恢复未结算预算记录。",
      "改进供应商结构化拒答、部分流及 OAuth 调用取消处理，保留可用的部分结果，避免将取消误报成供应商故障。",
      "修复 Linux 长状态目录启动及旧 CPU 安装验收中的进程残留问题，防止后续启动因端口被占用而失败。",
    ]),
  }),
  "2026.09.16.3": Object.freeze({
    all: Object.freeze([
      "修复 Linux 在较长状态目录或多字节目录名下无法启动的问题；本机管理通道保持私有权限，不改变 Phone 配对和设备信任。",
      "登录鉴权与 Phone 会话连接统一由 Engine 处理；Admin 按需打开，本机桌面与 Windows/macOS 桌宠无需手机式配对。",
      "命令行对话等待任务最终结果，新建和切换工作区时同步登记目标路径；失败、取消和超时会明确显示。",
      "修复首次账户设置冲突及快速输入被迟到工作区信息覆盖的问题；加载失败可重新同步并保留草稿。",
      "修复魔搭 Skills/MCP 商店分页和异常条目处理，来源暂时异常时保留上次可用列表。",
    ]),
  }),
  "2026.09.16.2": Object.freeze({
    all: Object.freeze([
      "登录鉴权与 Phone 会话连接统一由 Engine 处理；Admin 控制台按需打开，远程对话不再依赖控制台常驻。",
      "本机桌面 Web 与 Windows/macOS 桌宠启动后即可使用，无需手机式配对或重复登录；Phone 可粘贴终端生成的配对链接，或扫描控制台二维码连接设备。",
      "命令行对话会等待任务最终结果，区分进行中、完成、失败与取消；新建和切换工作区时同步登记目标路径，避免任务落入错误目录。",
      "修复首次设置页面在其他窗口已完成账户创建后无法继续的问题：保留登录输入并切回登录，无需刷新重填。",
      "修复打开会话后快速输入的草稿被迟到工作区信息覆盖的问题；加载失败可就地重新同步，恢复后输入与刷新均保留草稿。",
      "修复魔搭 Skills/MCP 商店分页边界和异常条目处理；来源暂时异常时保留上次可用列表并提示状态，避免把加载失败显示为空商店。",
    ]),
  }),
  "2026.09.15.1": Object.freeze({
    all: Object.freeze([
      "控制台采用紧凑的配置面板与就近保存操作，精简说明并统一帮助、错误恢复和明暗主题；记忆星系支持同屏浏览全局与工作区，保留节点编辑和作用域隔离。",
      "桌面 Web 与控制台往返保留原页面、输入草稿和滚动位置；Phone 新增已配对设备快速切换与授权邻居管理，按设备隔离会话、草稿和缓存。",
      "扩展商店默认国际源，可手动切换魔搭国内源；支持完整 Skill ZIP 安装、安装进度与失败后继续，本机 MCP 可按任务使用国内依赖源。",
      "后台委派继续执行时，Supervisor 可处理独立工作、查看进度、补充指导或取消；重要事件可恢复投递，中间成果可按版本验收，进度更新不会逐条唤醒模型。",
      "Web 支持自定义图片与视频背景轮播：图片使用设定间隔，视频播完切换；明暗模式保留原图色彩，左侧栏透明。终端使用独立输出游标，改善长输出、断线恢复和工作区切换体验。",
    ]),
  }),
  "2026.09.12.1": Object.freeze({
    all: Object.freeze([
      "邻居设备新增可达地址检测与一份邀请完成连接；支持为每台设备选择自己的任务目录。",
      "修复邻居签名校验、任务入队、重复投递和审批后结果回传故障，失败消息可沿原记录重试。",
      "修复 OpenAI / Anthropic 兼容接入的工具回传和问答、审批恢复，补齐本机 v8os acp 接入。",
      "普通跨工作区文件操作改为按具体动作请求审批，批准后继续原任务；免审遵循已有授权，保留核心资源与委派权限边界。",
      "修复样式工作台旧文件缓存导致的写入冲突，完善浏览器画面、字幕与音轨的时间对应。",
    ]),
  }),
  "2026.09.07.1": Object.freeze({
    all: Object.freeze([
      "修复文档读取能力包在 Windows 桌面安装包冒烟测试中因 typing-extensions 版本滞后导致的依赖冲突。",
      "模型输出新增自动预算选项；保留人工设置的输出与上下文预算，预置估算值不再覆盖用户选择。",
      "深度调研改进答案审核、局部修正和已存答案复用；区分有据可用的部分答案与失败草稿，减少重复写作和重复调研。",
      "统一浏览器登录授权入口及 Web、Phone 运行详情计数。",
    ]),
  }),
  "2026.09.06.1": Object.freeze({
    all: Object.freeze([
      "模型输出新增自动预算选项；保留人工设置的输出与上下文预算，预置估算值不再覆盖用户选择。",
      "深度调研改进答案审核、局部修正和已存答案复用；区分有据可用的部分答案与失败草稿，减少重复写作和重复调研。",
      "修复委派前读取调研证据被误拦截的问题，并统一浏览器登录授权入口及 Web、Phone 运行详情计数。",
    ]),
  }),
  "2026.08.18.1": Object.freeze({
    all: Object.freeze([
      "文档读取依赖已迁入显著的“文档读取能力包”；安装会按界面语言选择官方 PyPI 或可信中文镜像，未安装时明确提示，不再让 `read_native_file` 静默失败。",
      "命令工具现在严格区分普通管道与真实交互终端；非零退出、超时、进程树清理和 WinPTY 输入往返都会产生可终止、可诊断的结果。",
      "Web 与 Phone 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
    desktop: Object.freeze([
      "文档读取依赖已迁入显著的“文档读取能力包”；安装会按界面语言选择官方 PyPI 或可信中文镜像，未安装时明确提示，不再让 `read_native_file` 静默失败。",
      "命令工具现在严格区分普通管道与真实交互终端；非零退出、超时、进程树清理和 WinPTY 输入往返都会产生可终止、可诊断的结果。",
      "Web 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
    phone: Object.freeze([
      "Phone 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
  }),
  "2026.08.18.2": Object.freeze({
    all: Object.freeze([
      "文档读取依赖已迁入显著的“文档读取能力包”；安装会按界面语言选择官方 PyPI 或可信中文镜像，未安装时明确提示，不再让 `read_native_file` 静默失败。",
      "命令工具严格区分普通管道与真实交互终端；诊断探测移出启动关键路径，POSIX PTY 的读取、退出码和终止清理有界且可诊断。",
      "Web 与 Phone 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
    desktop: Object.freeze([
      "文档读取依赖已迁入显著的“文档读取能力包”；安装会按界面语言选择官方 PyPI 或可信中文镜像，未安装时明确提示，不再让 `read_native_file` 静默失败。",
      "命令工具严格区分普通管道与真实交互终端；诊断探测移出启动关键路径，POSIX PTY 的读取、退出码和终止清理有界且可诊断。",
      "Web 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
    phone: Object.freeze([
      "Phone 的发送/停止按钮改由共享的权威运行状态驱动；成功、失败或中断后会结束转圈，旧运行的迟到终态不会误清理新运行。",
    ]),
  }),
});

function releaseHighlights(release) {
  const items = RELEASE_HIGHLIGHTS[release.version]?.[release.product] || [];
  if (!items.length) return "";
  return `\n## 本次修复\n\n${items.map((item) => `- ${item}`).join("\n")}`;
}

function assetSection(product, version, channel) {
  if (product === "all") {
    const desktopVersion = channel === "stable" ? version : `preview-${version}`;
    const desktopLabel = channel === "stable" ? "桌面安装包" : "未签名桌面预览安装包";
    const androidAsset = channel === "stable"
      ? `V8OS-Phone-${version}-android.aab`
      : `V8OS-Phone-${version}-android-preview.apk`;
    return [
      "- `V8-Agent-OS-" + desktopVersion + "-win-x64-setup.exe` 与 `V8-Agent-OS-" + desktopVersion + "-win-arm64-setup.exe`：Windows x64/ARM64 " + desktopLabel + "；ARM64 包仅适用于原生 Windows on ARM，Intel/AMD 电脑请选择 x64 包。",
      "- `V8-Agent-OS-" + desktopVersion + "-macos-x64.dmg` 与 `V8-Agent-OS-" + desktopVersion + "-macos-arm64.dmg`：macOS 12.3 及以上 Intel/Apple Silicon " + desktopLabel + "。",
      "- `V8-Agent-OS-" + desktopVersion + "-linux-x64.AppImage`、`.deb` 及对应 arm64 版本：Linux " + desktopLabel + "。",
      channel === "stable"
        ? "- `" + androidAsset + "`：Android Phone production 应用包。"
        : "- `" + androidAsset + "`：Android 11+ Phone 预览安装包。",
      "- `SHA256SUMS.txt`：本次统一发布全部下载文件的 SHA256 校验信息。",
      "- 每个平台的运行时探针与包布局证据保留在对应 GitHub Actions artifact 中。",
    ].join("\n");
  }
  if (product === "phone") {
    const androidAsset = channel === "stable"
      ? `V8OS-Phone-${version}-android.aab`
      : `V8OS-Phone-${version}-android-preview.apk`;
    return [
      channel === "stable"
        ? "- `" + androidAsset + "`：Android production 应用包。"
        : "- `" + androidAsset + "`：Android 11+ 预览安装包。",
      "- `SHA256SUMS.txt`：下载文件的 SHA256 校验信息。",
    ].join("\n");
  }

  const desktopLabel = channel === "stable" ? "桌面安装包" : "未签名桌面预览安装包";
  const desktopVersion = channel === "stable" ? version : `preview-${version}`;
  const desktopChannelNote = channel === "stable"
    ? "请确认本次桌面包已完成 stable 门禁；签名、更新和校验信息应与发布资产同批提供。"
    : "当前桌面包未签名。Windows 可能显示 SmartScreen 确认，macOS 可能要求在系统设置中确认打开；代码签名、信誉和自动更新属于后续阶段。";
  const assets = [
    "- `V8-Agent-OS-" + desktopVersion + "-win-x64-setup.exe`：Windows x64 " + desktopLabel + "。",
    "- `V8-Agent-OS-" + desktopVersion + "-win-arm64-setup.exe`：Windows ARM64 " + desktopLabel + "，仅适用于原生 Windows on ARM；Intel/AMD 电脑请选择 x64 包。",
    "- `V8-Agent-OS-" + desktopVersion + "-macos-x64.dmg`：macOS 12.3 及以上 Intel " + desktopLabel + "。",
    "- `V8-Agent-OS-" + desktopVersion + "-macos-arm64.dmg`：macOS 12.3 及以上 Apple Silicon " + desktopLabel + "。",
    "- `V8-Agent-OS-" + desktopVersion + "-linux-x64.AppImage` 与 `V8-Agent-OS-" + desktopVersion + "-linux-x64.deb`：Linux x64 " + desktopLabel + "。",
    "- `V8-Agent-OS-" + desktopVersion + "-linux-arm64.AppImage` 与 `V8-Agent-OS-" + desktopVersion + "-linux-arm64.deb`：Linux arm64 " + desktopLabel + "。",
  ];
  if (channel === "stable") {
    assets.push("- `V8-Agent-OS-" + desktopVersion + "-win-x64.zip`：Windows 免安装包。");
  }
  assets.push(
    "- `SHA256SUMS.txt`：下载文件的 SHA256 校验信息。",
    "- 每个平台的运行时探针与包布局证据保留在对应 GitHub Actions artifact 中，避免把普通下载页塞满诊断 JSON。",
    "",
    desktopChannelNote,
  );
  return assets.join("\n");
}

function knownLimits(product, channel) {
  if (product === "all") {
    return [
      "- Desktop 与 Android Phone 共用本次统一版本；Phone 是远程入口，可粘贴终端生成的配对链接或扫描控制台二维码连接运行 V8OS 的设备。",
      "- iOS 因缺少非交互签名凭据被明确禁用，不会阻断 Desktop 与 Android 发布。",
      channel === "stable"
        ? "- stable 发布必须通过签名与 stable 门禁。"
        : "- 本版本是未签名 preview；SmartScreen、macOS 打开确认、签名和自动更新限制仍然存在。",
      "- 桌面 Shell 默认启动 Engine 与 Web，Admin 配置页按需启动；Windows/macOS 的桌宠由 Shell 管理，退出 V8OS 时会清理受管子进程。",
      "- Linux 的 Engine/Admin/Web/Shell 可用；当前桌宠的全屏透明交互窗口在 Electron 43 Linux 上没有经验证的安全输入区域合同，因此标记为 blocked 且不会启动。",
      "- Linux 当前声明与 clean CI 验证范围为 Ubuntu 22.04/24.04 GNU x64/arm64；其他 glibc 发行版仅 best-effort，Alpine/musl 不受支持。",
      "- Linux x64 核心服务会在不具备 SSE4.2 的旧 CPU 合同下做安装包启动验证；这类 CPU 的头像与图片背景转码会明确返回不可用，MP4 背景不受影响。",
      "- Ubuntu 24.04 在 AppArmor 限制 unprivileged user namespace 时请优先使用 DEB；DEB 随包安装 Electron 启动兼容 profile，但该 profile 不是安全隔离边界。AppImage 不会静默回退到 `--no-sandbox`；宿主必须提供可用 user namespace，否则启动与烟测会明确失败。",
      "- macOS/Linux 的 GUI 权限、窗口管理器与桌面自动化仍需在对应实体主机验收；Linux Wayland 的输入限制会被显式投影为 blocked。",
    ].join("\n");
  }
  if (product === "phone") {
    return [
      "- Phone 是远程交互端；在运行 V8OS 的设备上用终端生成配对链接，或在控制台打开二维码，即可授权连接。",
      "- Android 支持目标为 11 及以上；iOS 支持目标为 16.4 及以上，当前只提供受控手动构建，不随 Phone tag 发布。",
      "- 若你从旧 `phone-v*` release 升级，请优先使用统一的 `v8-os-v*` 版本线；`v8-os-phone-v*` 仅保留两个成功统一发布周期。",
    ].join("\n");
  }
  if (channel === "stable") {
    return [
      "- 本版本属于 desktop-stable 通道，请仅在 stable 门禁完成后发布。",
      "- 桌面 Shell 默认启动 Engine 与 Web，Admin 配置页按需启动；Windows/macOS 的桌宠由 Shell 管理，退出 V8OS 时会清理受管子进程。",
      "- Linux 的 Engine/Admin/Web/Shell 可用；当前桌宠的全屏透明交互窗口在 Electron 43 Linux 上没有经验证的安全输入区域合同，因此标记为 blocked 且不会启动。",
      "- Linux 当前声明与 clean CI 验证范围为 Ubuntu 22.04/24.04 GNU x64/arm64；其他 glibc 发行版仅 best-effort，Alpine/musl 不受支持。",
      "- Linux x64 核心服务会在不具备 SSE4.2 的旧 CPU 合同下做安装包启动验证；这类 CPU 的头像与图片背景转码会明确返回不可用，MP4 背景不受影响。",
      "- Ubuntu 24.04 在 AppArmor 限制 unprivileged user namespace 时请优先使用 DEB；DEB 随包安装 Electron 启动兼容 profile，但该 profile 不是安全隔离边界。AppImage 不会静默回退到 `--no-sandbox`；宿主必须提供可用 user namespace，否则启动与烟测会明确失败。",
      "- macOS/Linux 的 GUI 权限、窗口管理器与桌面自动化需要在对应实体主机验收；Linux Wayland 的输入限制会被显式投影为 blocked。",
    ].join("\n");
  }
  return [
    "- 本版本是未签名的多平台桌面预览包，不代表 stable 版本。",
    "- 桌面 Shell 默认启动 Engine 与 Web，Admin 配置页按需启动；Windows/macOS 的桌宠由 Shell 管理，退出 V8OS 时会清理受管子进程。",
    "- Linux 的 Engine/Admin/Web/Shell 可用；当前桌宠的全屏透明交互窗口在 Electron 43 Linux 上没有经验证的安全输入区域合同，因此标记为 blocked 且不会启动。",
    "- Linux 当前声明与 clean CI 验证范围为 Ubuntu 22.04/24.04 GNU x64/arm64；其他 glibc 发行版仅 best-effort，Alpine/musl 不受支持。",
    "- Linux x64 核心服务会在不具备 SSE4.2 的旧 CPU 合同下做安装包启动验证；这类 CPU 的头像与图片背景转码会明确返回不可用，MP4 背景不受影响。",
    "- Ubuntu 24.04 在 AppArmor 限制 unprivileged user namespace 时请优先使用 DEB；DEB 随包安装 Electron 启动兼容 profile，但该 profile 不是安全隔离边界。AppImage 不会静默回退到 `--no-sandbox`；宿主必须提供可用 user namespace，否则启动与烟测会明确失败。",
    "- 自动更新与代码签名仍在后续阶段；macOS/Linux 的 GUI 权限、窗口管理器与桌面自动化需要在对应实体主机验收。Linux DEB 声明 X11 辅助工具；AppImage 仍要求宿主安装 xdotool、wmctrl 与 xclip/xsel 之一，Wayland 限制会显式显示为 blocked。",
  ].join("\n");
}

function optionalProductNotes(release) {
  if (release.product !== "all") return { names: [], assets: [], installation: [] };
  const result = { names: [], assets: [], installation: [] };
  if (release.products?.server?.enabled) {
    result.names.push("Server");
    for (const [target, value] of Object.entries(release.products.server.targets)) {
      if (value.enabled) {
        result.assets.push(`- \`V8OS-Server-${release.version}-${target}.tar.gz\`：无图形 Linux Server 源分发包。`);
        if (value.standalone?.enabled) result.assets.push(`- \`V8OS-Engine-${release.version}-${target}.tar.gz\` 与对应 \`.json\`：npm CLI 首次启动按 SHA-256 校验下载的便携 Engine 运行时。`);
      }
    }
    for (const [target, value] of Object.entries(release.products.server.standaloneTargets || {})) {
      if (value.enabled) result.assets.push(`- \`V8OS-Engine-${release.version}-${target}.tar.gz\` 与对应 \`.json\`：npm CLI 首次启动按 SHA-256 校验下载的便携 Engine 运行时。`);
    }
    result.installation.push("Server：在无图形 Ubuntu 22.04/24.04 glibc x64 上准备 Python 3.11、Node.js 20+ 与 Chromium 系统库，以普通用户解压到独立版本目录并运行 ./install.sh；安装过程联网下载依赖与 Chromium，不是离线包。配置凭据后使用 ./v8os service install，退出终端后 Engine、Phone 与受信组网服务继续运行。",
      "Server 升级请使用新包 CLI。会话修订使用数据库 schema 4，旧 schema 3 Engine 不能直接回读；不兼容回滚会阻断并保留新数据，故障时可重试当前版本或升级兼容修复包。服务回滚不会覆盖数据库或撤销数据迁移。");
  }
  if (release.products?.tui?.enabled && release.products.tui.targets.npm.enabled) {
    result.names.push("TUI");
    result.assets.push(`- \`V8OS-TUI-${release.version}.tgz\`：可使用 npm 安装的独立终端客户端。`);
    result.installation.push(`终端版：使用 Node.js 22+ 执行 \`npm install -g @v8-agent-os/v8-agent-os\`（或安装 \`V8OS-TUI-${release.version}.tgz\`），然后运行唯一入口 \`v8os\`；首次启动会从同一 Release 下载并校验便携 Engine，\`v8os-tui\` 仅作为兼容的纯界面入口保留。离线部署可设置 \`V8OS_ENGINE_ARCHIVE\` 或 \`V8OS_ENGINE_RUNTIME_DIR\`。`);
  }
  return result;
}

function buildNotes(release) {
  const base = repoUrl();
  const prev = previousTag(release.tag, release.product);
  const changelog = prev ? `${base}/compare/${prev}...${release.tag}` : `${base}/commits/${release.tag}`;
  const optional = optionalProductNotes(release);

  return `# ${releaseTitle(release.product, release.version, release.channel)}

## 下载

${assetSection(release.product, release.version, release.channel)}
${optional.assets.join("\n")}

## 本次版本

- 发布对象：${release.product === "all" ? ["Desktop", "Phone", ...optional.names].join(" 与 ") : release.product === "phone" ? "Phone 远程端" : "桌面版"}
- 发布通道：${release.channel}
- 标签：\`${release.tag}\`
${releaseHighlights(release)}

## 安装 / 更新

${release.product === "all"
  ? "桌面端按目标平台下载安装，本机 Web 无需另行登录；Android 安装 APK 后，可粘贴 V8OS 终端生成的配对链接，或扫描控制台二维码完成连接。"
  : release.product === "phone"
  ? "下载 APK 后安装到 Android 设备；打开 Phone 后粘贴 V8OS 终端生成的配对链接，或扫描控制台二维码完成连接。"
  : release.channel === "stable"
    ? "下载安装包或免安装包后启动 V8 Agent OS。首次运行会启动本机服务并打开桌面 Shell。"
    : "下载安装包后启动 V8 Agent OS。首次运行会启动本机服务并打开桌面 Shell。"}
${optional.installation.join("\n\n")}

## 已知限制

${knownLimits(release.product, release.channel)}
${release.version === "2026.09.06.1" ? "\n- 深度调研的一手资料获取仍在优化；来源不足时会交付有明确限制的部分答案，不代表已满足全部调研要求。重要结论仍需核对原始来源。" : ""}

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
