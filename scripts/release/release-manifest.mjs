import fs from "node:fs";
import path from "node:path";

export const VERSION_RE = /^20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d)$/;
export const UNIFIED_TAG_RE = /^v8-os-v(20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d))$/;
export const LEGACY_PRODUCT_TAG_RE = /^v8-os-(phone|desktop)-v(20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d))$/;

export const PRODUCT_TARGETS = Object.freeze({
  desktop: Object.freeze([
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
    "linux-x64",
    "linux-arm64",
  ]),
  phone: Object.freeze(["android", "ios"]),
});

export function toUnifiedTag(version) {
  return `v8-os-v${version}`;
}

export function toLegacyProductTag(product, version) {
  if (!Object.hasOwn(PRODUCT_TARGETS, product)) {
    throw new Error(`Unknown release product: ${product}`);
  }
  return `v8-os-${product}-v${version}`;
}

export function isValidReleaseVersion(version) {
  if (!VERSION_RE.test(version || "")) return false;
  const [year, month, day] = version.split(".").map((value) => Number(value));
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day;
}

export function compareReleaseVersions(left, right) {
  if (!isValidReleaseVersion(left) || !isValidReleaseVersion(right)) {
    throw new Error("Release version comparison requires valid YYYY.MM.DD.N values");
  }
  const leftParts = left.split(".").map(Number);
  const rightParts = right.split(".").map(Number);
  for (let index = 0; index < leftParts.length; index += 1) {
    if (leftParts[index] !== rightParts[index]) {
      return leftParts[index] < rightParts[index] ? -1 : 1;
    }
  }
  return 0;
}

export function toSemver(version) {
  const [year, month, day, build] = version.split(".").map((value) => Number(value));
  return `${year}.${month}.${day}-${build}`;
}

export function toAppVersion(version) {
  const [year, month, day] = version.split(".").map((value) => Number(value));
  return `${year}.${month}.${day}`;
}

export function toAndroidVersionCode(version) {
  const [year, month, day, build] = version.split(".");
  return Number(`${year.slice(2)}${month}${day}${build.padStart(2, "0")}`);
}

export function toAppleBuildNumber(version) {
  const [year, month, day, build] = version.split(".");
  return `${year}${month}${day}${build.padStart(2, "0")}`;
}

function readProjectionJson(filePath, label, problems) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    problems.push(`${label} cannot be read: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

export function validateReleaseProjections(manifest, repoRoot) {
  validateReleaseManifest(manifest);
  const root = path.resolve(repoRoot);
  const version = manifest.release.version;
  const semver = toSemver(version);
  const problems = [];
  const versionPath = path.join(root, "VERSION");
  let versionProjection = null;
  try {
    versionProjection = fs.readFileSync(versionPath, "utf8").trim();
  } catch (error) {
    problems.push(`VERSION cannot be read: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (versionProjection !== null && versionProjection !== semver) {
    problems.push(`VERSION is ${versionProjection}, expected ${semver}`);
  }

  if (manifest.products.desktop.enabled) {
    const desktopPackage = readProjectionJson(
      path.join(root, "apps", "v8-agent-os-shell", "package.json"),
      "Desktop package.json",
      problems,
    );
    const desktopLock = readProjectionJson(
      path.join(root, "apps", "v8-agent-os-shell", "package-lock.json"),
      "Desktop package-lock.json",
      problems,
    );
    if (desktopPackage && desktopPackage.version !== semver) {
      problems.push(`Desktop package.json version is ${desktopPackage.version}, expected ${semver}`);
    }
    if (desktopLock && desktopLock.version !== semver) {
      problems.push(`Desktop package-lock.json version is ${desktopLock.version}, expected ${semver}`);
    }
    if (desktopLock?.packages?.[""]?.version !== semver) {
      problems.push(`Desktop package-lock root version is ${desktopLock?.packages?.[""]?.version}, expected ${semver}`);
    }
  }

  if (manifest.products.phone.enabled) {
    const phonePackage = readProjectionJson(
      path.join(root, "apps", "v8-agent-os-phone", "package.json"),
      "Phone package.json",
      problems,
    );
    const phoneLock = readProjectionJson(
      path.join(root, "apps", "v8-agent-os-phone", "package-lock.json"),
      "Phone package-lock.json",
      problems,
    );
    const phoneApp = readProjectionJson(
      path.join(root, "apps", "v8-agent-os-phone", "app.json"),
      "Phone app.json",
      problems,
    );
    if (phonePackage && phonePackage.version !== semver) {
      problems.push(`Phone package.json version is ${phonePackage.version}, expected ${semver}`);
    }
    if (phoneLock && phoneLock.version !== semver) {
      problems.push(`Phone package-lock.json version is ${phoneLock.version}, expected ${semver}`);
    }
    if (phoneLock?.packages?.[""]?.version !== semver) {
      problems.push(`Phone package-lock root version is ${phoneLock?.packages?.[""]?.version}, expected ${semver}`);
    }
    const expectedAppVersion = toAppVersion(version);
    const expectedAndroidVersionCode = toAndroidVersionCode(version);
    const expectedAppleBuildNumber = toAppleBuildNumber(version);
    if (phoneApp?.expo?.version !== expectedAppVersion) {
      problems.push(`Phone Expo version is ${phoneApp?.expo?.version}, expected ${expectedAppVersion}`);
    }
    if (phoneApp?.expo?.android?.versionCode !== expectedAndroidVersionCode) {
      problems.push(`Phone Android versionCode is ${phoneApp?.expo?.android?.versionCode}, expected ${expectedAndroidVersionCode}`);
    }
    if (phoneApp?.expo?.ios?.buildNumber !== expectedAppleBuildNumber) {
      problems.push(`Phone iOS buildNumber is ${phoneApp?.expo?.ios?.buildNumber}, expected ${expectedAppleBuildNumber}`);
    }
  }

  if (problems.length > 0) {
    throw new Error(`Invalid release version projections:\n- ${problems.join("\n- ")}`);
  }
  return { version, semver };
}

function validateBoolean(value, field, problems) {
  if (typeof value !== "boolean") {
    problems.push(`${field} must be a boolean`);
  }
}

function validateTargets(product, entry, problems) {
  const expectedTargets = PRODUCT_TARGETS[product];
  const targets = entry?.targets;
  if (!targets || typeof targets !== "object" || Array.isArray(targets)) {
    problems.push(`products.${product}.targets must be an object`);
    return;
  }

  const actualTargets = Object.keys(targets);
  for (const target of expectedTargets) {
    if (!Object.hasOwn(targets, target)) {
      problems.push(`products.${product}.targets.${target} is required`);
    }
  }
  for (const target of actualTargets) {
    if (!expectedTargets.includes(target)) {
      problems.push(`products.${product}.targets.${target} is not supported by schema 2`);
    }
  }

  for (const target of expectedTargets) {
    const targetEntry = targets[target];
    const prefix = `products.${product}.targets.${target}`;
    if (!targetEntry || typeof targetEntry !== "object" || Array.isArray(targetEntry)) {
      problems.push(`${prefix} must be an object`);
      continue;
    }
    validateBoolean(targetEntry.enabled, `${prefix}.enabled`, problems);
    validateBoolean(targetEntry.required, `${prefix}.required`, problems);
    if (targetEntry.required === true && targetEntry.enabled !== true) {
      problems.push(`${prefix} cannot be required when it is disabled`);
    }
    if (targetEntry.enabled === false && !String(targetEntry.reason || "").trim()) {
      problems.push(`${prefix}.reason is required when the target is disabled`);
    }
  }
}

export function validateReleaseManifest(manifest) {
  const problems = [];
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("release-manifest.json must contain a JSON object");
  }
  if (manifest.schema !== 2) {
    problems.push(`schema is ${JSON.stringify(manifest.schema)}, expected 2`);
  }

  const release = manifest.release;
  if (!release || typeof release !== "object" || Array.isArray(release)) {
    problems.push("release must be an object");
  } else {
    if (!isValidReleaseVersion(release.version)) {
      problems.push("release.version must be a real UTC date in YYYY.MM.DD.N form, year 2000-2099, and N 1-99 without a leading zero");
    }
    if (release.channel !== "preview") {
      problems.push("release.channel must remain preview until the stable signing and installation gates are implemented");
    }
    if (release.tag !== toUnifiedTag(release.version)) {
      problems.push(`release.tag is ${release.tag}, expected ${toUnifiedTag(release.version)}`);
    }
  }

  const products = manifest.products;
  if (!products || typeof products !== "object" || Array.isArray(products)) {
    problems.push("products must be an object");
  } else {
    for (const product of Object.keys(products)) {
      if (!Object.hasOwn(PRODUCT_TARGETS, product)) {
        problems.push(`products.${product} is not supported by schema 2`);
      }
    }
    for (const product of Object.keys(PRODUCT_TARGETS)) {
      const entry = products[product];
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
        problems.push(`products.${product} must be an object`);
        continue;
      }
      validateBoolean(entry.enabled, `products.${product}.enabled`, problems);
      validateBoolean(entry.required, `products.${product}.required`, problems);
      if (entry.enabled !== true || entry.required !== true) {
        problems.push(`products.${product} must be enabled and required`);
      }
      if (entry.required === true && entry.enabled !== true) {
        problems.push(`products.${product} cannot be required when it is disabled`);
      }
      for (const duplicateField of ["version", "channel", "tag"]) {
        if (Object.hasOwn(entry, duplicateField)) {
          problems.push(`products.${product}.${duplicateField} is forbidden; release.${duplicateField} is the only release identity truth`);
        }
      }
      validateTargets(product, entry, problems);
    }
  }

  const desktop = products?.desktop;
  for (const target of PRODUCT_TARGETS.desktop) {
    const entry = desktop?.targets?.[target];
    if (entry && (entry.enabled !== true || entry.required !== true)) {
      problems.push(`products.desktop.targets.${target} must be enabled and required`);
    }
  }
  const android = products?.phone?.targets?.android;
  if (android && (android.enabled !== true || android.required !== true)) {
    problems.push("products.phone.targets.android must be enabled and required");
  }
  const ios = products?.phone?.targets?.ios;
  if (ios && (ios.enabled !== false || ios.required !== false)) {
    problems.push("products.phone.targets.ios must be disabled and not required until signing is configured");
  }

  const legacy = manifest.compatibility?.legacyProductTags;
  if (!legacy || typeof legacy !== "object" || Array.isArray(legacy)) {
    problems.push("compatibility.legacyProductTags must describe the two-cycle transition");
  } else {
    if (legacy.status !== "deprecated") {
      problems.push("compatibility.legacyProductTags.status must be deprecated");
    }
    if (legacy.supportedUnifiedReleaseCycles !== 2) {
      problems.push("compatibility.legacyProductTags.supportedUnifiedReleaseCycles must be 2");
    }
    if (legacy.deriveVersionFrom !== "release.version") {
      problems.push("compatibility.legacyProductTags.deriveVersionFrom must be release.version");
    }
  }

  if (problems.length > 0) {
    throw new Error(`Invalid release manifest schema 2:\n- ${problems.join("\n- ")}`);
  }
  return manifest;
}

export function loadReleaseManifest(manifestPath = "release-manifest.json") {
  const resolvedManifest = path.resolve(manifestPath);
  const manifest = JSON.parse(fs.readFileSync(resolvedManifest, "utf8"));
  validateReleaseManifest(manifest);
  return { manifest, manifestPath: resolvedManifest };
}

export function resolveReleaseTag({ manifest, tag, product }) {
  if (product && !Object.hasOwn(PRODUCT_TARGETS, product)) {
    throw new Error("--product must be phone or desktop when provided");
  }
  const requestedTag = tag || manifest.release.tag;
  const unified = UNIFIED_TAG_RE.exec(requestedTag);
  if (unified) {
    if (unified[1] !== manifest.release.version || requestedTag !== manifest.release.tag) {
      throw new Error(
        `Unified release tag ${requestedTag} does not match manifest release ${manifest.release.tag}`,
      );
    }
    return {
      tag: requestedTag,
      tagKind: "unified",
      deprecated: false,
      product: product || null,
      version: manifest.release.version,
      channel: manifest.release.channel,
    };
  }

  const legacy = LEGACY_PRODUCT_TAG_RE.exec(requestedTag);
  if (legacy) {
    const tagProduct = legacy[1];
    const version = legacy[2];
    if (product && product !== tagProduct) {
      throw new Error(`Legacy tag product ${tagProduct} does not match --product ${product}`);
    }
    const expected = toLegacyProductTag(tagProduct, manifest.release.version);
    if (version !== manifest.release.version || requestedTag !== expected) {
      throw new Error(`Legacy release tag ${requestedTag} does not match derived tag ${expected}`);
    }
    return {
      tag: requestedTag,
      tagKind: "legacy-product",
      deprecated: true,
      product: tagProduct,
      version: manifest.release.version,
      channel: manifest.release.channel,
      warning: `${requestedTag} is a deprecated compatibility trigger; use ${manifest.release.tag}. It remains supported for two successfully published unified release cycles and is derived from release.version.`,
    };
  }

  throw new Error(
    `Tag ${requestedTag} must match v8-os-vYYYY.MM.DD.N or deprecated v8-os-<product>-vYYYY.MM.DD.N`,
  );
}

function targetList(product) {
  return Object.entries(product.targets).map(([name, target]) => ({ name, ...target }));
}

export function resolveReleasePlan(manifest) {
  validateReleaseManifest(manifest);
  const desktop = manifest.products.desktop;
  const phone = manifest.products.phone;
  const android = phone.targets.android;
  const ios = phone.targets.ios;
  const phonePlatform = android.enabled && ios.enabled
    ? "all"
    : android.enabled
      ? "android"
      : ios.enabled
        ? "ios"
        : "none";

  return {
    schema: manifest.schema,
    version: manifest.release.version,
    channel: manifest.release.channel,
    tag: manifest.release.tag,
    prerelease: manifest.release.channel !== "stable",
    desktop: {
      enabled: desktop.enabled,
      required: desktop.required,
      targets: targetList(desktop),
    },
    phone: {
      enabled: phone.enabled,
      required: phone.required,
      platform: phonePlatform,
      targets: targetList(phone),
    },
    compatibility: manifest.compatibility,
  };
}
