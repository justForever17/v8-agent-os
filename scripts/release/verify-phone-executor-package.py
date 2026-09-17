"""Check the built Android archive, not just Expo's JS/native declarations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

REQUIRED_CLASSES = {f"Lexpo/modules/v8executor/{name};" for name in (
    "V8DeviceExecutorModule", "ExecutorAccessibilityService", "ExecutorStopReceiver",
    "ExecutorController", "ExecutorStore", "CommandGuard",
)}
FORBIDDEN_CLASSES = {
    "Lexpo/modules/v8executor/ExecutorTestActivity;",
    "Lexpo/modules/v8executor/ExecutorNativeTest;",
    "Lcom/v8agentos/executorfixture/FixtureActivity;",
}
REQUIRED_MANIFEST = (
    "expo.modules.v8executor.ExecutorAccessibilityService",
    "expo.modules.v8executor.ExecutorStopReceiver",
    "android.permission.BIND_ACCESSIBILITY_SERVICE",
)
FORBIDDEN_MARKERS = ("v8_executor_test_ca", "v8_executor_test_network_security", "ExecutorTestActivity", "com.v8agentos.executorfixture")
REQUIRED_GUARDS = {
    "stopCannotBeRearmedByNetworkRenewalOrOldCommand",
    "anotherAuthorityWithEqualEpochCannotTakeControl",
    "rebootEpochAndGrantChangesFenceOldCommands",
    "deadlineAndOriginalTtlDoNotResetOnReconnect",
}


def class_definitions(data: bytes) -> set[str]:
    if len(data) < 112 or data[:4] != b"dex\n" or data[7] != 0:
        raise ValueError("Android package contains an invalid DEX header")
    if struct.unpack_from("<I", data, 40)[0] != 0x12345678:
        raise ValueError("Unsupported DEX byte order")

    def table(header: int, width: int):
        size, offset = struct.unpack_from("<II", data, header)
        if offset + size * width > len(data):
            raise ValueError("Truncated DEX table")
        return size, offset

    string_count, strings = table(56, 4)
    type_count, types = table(64, 4)
    class_count, classes = table(96, 32)
    result = set()
    for index in range(class_count):
        type_index = struct.unpack_from("<I", data, classes + index * 32)[0]
        if type_index >= type_count:
            raise ValueError("Invalid DEX class index")
        string_index = struct.unpack_from("<I", data, types + type_index * 4)[0]
        if string_index >= string_count:
            raise ValueError("Invalid DEX type index")
        offset = struct.unpack_from("<I", data, strings + string_index * 4)[0]
        # DEX strings start with a ULEB128 UTF-16 length. Required descriptors
        # are ASCII; other valid modified-UTF-8 names are not inspected.
        for _ in range(5):
            if offset >= len(data):
                raise ValueError("Truncated DEX string")
            byte = data[offset]
            offset += 1
            if not byte & 0x80:
                break
        else:
            raise ValueError("Invalid DEX string length")
        end = data.find(b"\0", offset)
        if end < 0:
            raise ValueError("Unterminated DEX string")
        result.add(data[offset:end].decode("utf-8", errors="replace"))
    return result


def contains_marker(data: bytes, marker: str) -> bool:
    # APK binary XML has UTF-8/UTF-16 string pools; AAB manifests use protobuf
    # with UTF-8 strings. This gate checks component presence, not UI behavior.
    return marker.encode() in data or marker.encode("utf-16-le") in data


def verify_archive(archive: Path) -> dict:
    if archive.suffix not in {".apk", ".aab"}:
        raise ValueError("Expected an APK or AAB archive")
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate entries in Android package")
        if any(any(marker in name for marker in FORBIDDEN_MARKERS) or "android-executor-fixture" in name for name in names):
            raise ValueError("Android package contains executor test resources")
        manifest_name = "AndroidManifest.xml" if archive.suffix == ".apk" else "base/manifest/AndroidManifest.xml"
        if manifest_name not in names:
            raise ValueError("Android package has no application manifest")
        manifest = package.read(manifest_name)
        if any(contains_marker(manifest, marker) for marker in FORBIDDEN_MARKERS):
            raise ValueError("Android package registers executor test components or trust configuration")
        if not all(contains_marker(manifest, marker) for marker in REQUIRED_MANIFEST):
            raise ValueError("Android package is missing executor service/Stop registration or accessibility permission")
        dex_files = [name for name in names if re.search(r"(?:^|/)classes(?:\d+)?\.dex$", name)]
        classes = set().union(*(class_definitions(package.read(name)) for name in dex_files))
        if FORBIDDEN_CLASSES & classes:
            raise ValueError("Android package compiles executor test or fixture classes")
        if REQUIRED_CLASSES - classes:
            raise ValueError("Android package did not compile the complete executor native module")
    return {"archive": archive.name, "format": archive.suffix[1:], "nativeExecutorClasses": len(REQUIRED_CLASSES), "testComponentsAbsent": True}


def verify_junit(filename: Path) -> int:
    suite = ET.parse(filename).getroot()
    cases = list(suite.iter("testcase"))
    if not cases or not REQUIRED_GUARDS <= {case.get("name") for case in cases}:
        raise ValueError("The required native executor guard tests did not run")
    if any(True for kind in ("failure", "error", "skipped") for _ in suite.iter(kind)):
        raise ValueError("Native executor guard tests failed or were skipped")
    if any(int(suite.get(key, "0")) for key in ("failures", "errors", "skipped")):
        raise ValueError("Native executor guard suite did not pass")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--junit", type=Path, help="Gradle testReleaseUnitTest report from this build")
    args = parser.parse_args()
    report = verify_archive(args.archive)
    if args.junit:
        report["nativeGuardTests"] = verify_junit(args.junit)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
