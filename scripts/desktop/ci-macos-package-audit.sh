#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?mounted .app path is required}"
expected_arch="${2:?x64 or arm64 is required}"
minimum_system_version="${3:-12.0}"

app_path="$(cd "$(dirname "$app_path")" && pwd)/$(basename "$app_path")"
test -d "$app_path"
info_plist="$app_path/Contents/Info.plist"
test -f "$info_plist"
for required_tool in file find grep lipo otool plutil; do
  command -v "$required_tool" >/dev/null 2>&1 || {
    echo "Required macOS audit tool is missing: $required_tool" >&2
    exit 1
  }
done

case "$expected_arch" in
  x64) expected_macho_arch="x86_64" ;;
  arm64) expected_macho_arch="arm64" ;;
  *) echo "Unsupported macOS architecture: $expected_arch" >&2; exit 2 ;;
esac

plist_value() {
  plutil -extract "$1" raw -o - "$info_plist"
}

test "$(plist_value CFBundleIdentifier)" = 'com.v8agentos.desktop.preview'
test "$(plist_value LSMinimumSystemVersion)" = "$minimum_system_version"
test -n "$(plist_value NSMicrophoneUsageDescription)"
test -n "$(plist_value NSCameraUsageDescription)"
test -n "$(plist_value NSScreenCaptureUsageDescription)"
if plutil -extract NSAppleEventsUsageDescription raw -o - "$info_plist" >/dev/null 2>&1; then
  echo 'Unsigned preview must not claim unused Apple Events access.' >&2
  exit 1
fi

version_is_greater() {
  local candidate="$1"
  local ceiling="$2"
  awk -v candidate="$candidate" -v ceiling="$ceiling" 'BEGIN {
    candidate_count = split(candidate, candidate_parts, ".")
    ceiling_count = split(ceiling, ceiling_parts, ".")
    count = candidate_count > ceiling_count ? candidate_count : ceiling_count
    for (part_index = 1; part_index <= count; part_index += 1) {
      left = candidate_parts[part_index] + 0
      right = ceiling_parts[part_index] + 0
      if (left > right) exit 0
      if (left < right) exit 1
    }
    exit 1
  }'
}

macho_count=0
ax_helper_seen=false
sandbox_host_seen=false
while IFS= read -r -d '' candidate; do
  file_description="$(file -b "$candidate")"
  [[ "$file_description" == *Mach-O* ]] || continue
  macho_count=$((macho_count + 1))

  architectures="$(lipo -archs "$candidate")"
  if ! tr ' ' '\n' <<<"$architectures" | grep -Fxq "$expected_macho_arch"; then
    echo "Mach-O architecture mismatch ($architectures): $candidate" >&2
    exit 1
  fi

  min_versions=""
  if command -v vtool >/dev/null 2>&1; then
    min_versions="$(vtool -show-build "$candidate" 2>/dev/null | awk '$1 == "minos" { print $2 }' || true)"
  fi
  if [[ -z "$min_versions" ]]; then
    min_versions="$(otool -l "$candidate" | awk '
      $1 == "minos" { print $2 }
      $1 == "cmd" { legacy = ($2 == "LC_VERSION_MIN_MACOSX"); next }
      legacy && $1 == "version" { print $2; legacy = 0 }
    ')"
  fi
  if [[ -z "$min_versions" ]]; then
    echo "Mach-O deployment target is missing: $candidate" >&2
    exit 1
  fi
  while IFS= read -r min_version; do
    if version_is_greater "$min_version" "$minimum_system_version"; then
      echo "Mach-O requires macOS $min_version above declared floor $minimum_system_version: $candidate" >&2
      exit 1
    fi
  done <<<"$min_versions"

  [[ "$candidate" != *'/mac_ax_helper' ]] || ax_helper_seen=true
  [[ "$candidate" != *'/v8-sandbox-host' ]] || sandbox_host_seen=true
done < <(find "$app_path" -type f -print0)

test "$macho_count" -gt 10
test "$ax_helper_seen" = true
test "$sandbox_host_seen" = true

if codesign -dv --verbose=4 "$app_path" >/dev/null 2>&1; then
  echo 'V8OS_MACOS_PREVIEW_SIGNATURE_PRESENT_NOT_GATEKEEPER_PROOF'
else
  echo 'V8OS_MACOS_PREVIEW_UNSIGNED'
fi
echo "V8OS_MACOS_MACHO_AUDIT_OK count=$macho_count arch=$expected_macho_arch floor=$minimum_system_version"
