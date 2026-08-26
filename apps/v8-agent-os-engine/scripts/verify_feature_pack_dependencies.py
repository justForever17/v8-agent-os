"""Validate a staged feature pack against the embedded Python environment."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


RESULT_MARKER = "__V8_DEPENDENCY_CHECK__"
MAX_REPORTED_CONFLICTS = 50


def _normalized_paths(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in paths:
        raw = os.fspath(value).strip()
        if not raw:
            continue
        resolved = os.path.realpath(raw)
        key = os.path.normcase(resolved)
        if key in seen or not os.path.exists(resolved):
            continue
        seen.add(key)
        normalized.append(resolved)
    return normalized


def _distribution_record(distribution: metadata.Distribution, source: str) -> dict[str, Any] | None:
    raw_name = str(distribution.metadata.get("Name") or "").strip()
    raw_version = str(distribution.version or "").strip()
    if not raw_name or not raw_version:
        return None
    return {
        "name": canonicalize_name(raw_name),
        "displayName": raw_name,
        "version": raw_version,
        "source": source,
        "distribution": distribution,
    }


def _distribution_index(paths: Sequence[str], source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for distribution in metadata.distributions(path=list(paths)):
        record = _distribution_record(distribution, source)
        if record and record["name"] not in indexed:
            indexed[record["name"]] = record
    return indexed


def _active_requirement(requirement: Requirement) -> bool:
    if requirement.marker is None:
        return True
    environment = default_environment()
    environment["extra"] = ""
    return requirement.marker.evaluate(environment)


def verify_dependency_compatibility(
    target_root: str | os.PathLike[str],
    *,
    base_paths: Sequence[str | os.PathLike[str]] | None = None,
    allow_missing: bool = False,
) -> dict[str, Any]:
    target = os.path.realpath(os.fspath(target_root))
    if not os.path.isdir(target):
        raise ValueError("feature_pack_target_missing")

    target_index = _distribution_index([target], "feature_pack")
    if not target_index:
        raise ValueError("feature_pack_target_has_no_distributions")

    if base_paths is None:
        target_key = os.path.normcase(target)
        selected_base_paths = [
            item
            for item in _normalized_paths(sys.path)
            if os.path.normcase(item) != target_key
        ]
    else:
        selected_base_paths = _normalized_paths(base_paths)

    effective = _distribution_index(selected_base_paths, "base_runtime")
    effective.update(target_index)
    conflicts: list[dict[str, str]] = []

    for dependent_name in sorted(effective):
        dependent = effective[dependent_name]
        distribution = dependent["distribution"]
        for raw_requirement in distribution.requires or []:
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement:
                conflicts.append({
                    "dependent": dependent["displayName"],
                    "dependentVersion": dependent["version"],
                    "requirement": raw_requirement,
                    "reason": "invalid_requirement_metadata",
                })
                continue
            if not _active_requirement(requirement):
                continue
            dependency_name = canonicalize_name(requirement.name)
            dependency = effective.get(dependency_name)
            if dependency is None:
                conflicts.append({
                    "dependent": dependent["displayName"],
                    "dependentVersion": dependent["version"],
                    "requirement": str(requirement),
                    "reason": "dependency_missing",
                })
                continue
            try:
                compatible = not requirement.specifier or requirement.specifier.contains(
                    Version(dependency["version"]),
                    prereleases=True,
                )
            except InvalidVersion:
                compatible = False
            if not compatible:
                conflicts.append({
                    "dependent": dependent["displayName"],
                    "dependentVersion": dependent["version"],
                    "requirement": str(requirement),
                    "installed": f"{dependency['displayName']}=={dependency['version']}",
                    "installedSource": dependency["source"],
                    "reason": "version_conflict",
                })

    target_packages = [
        f"{record['displayName']}=={record['version']}"
        for record in sorted(target_index.values(), key=lambda item: item["name"])
    ]
    blocking_conflicts = [
        item
        for item in conflicts
        if not (allow_missing and item.get("reason") == "dependency_missing")
    ]
    advisories = [
        item
        for item in conflicts
        if allow_missing and item.get("reason") == "dependency_missing"
    ]
    return {
        "ok": not blocking_conflicts,
        "kind": "python_dependency_compatibility",
        "checkedPackages": len(effective),
        "targetPackages": target_packages,
        "conflictCount": len(blocking_conflicts),
        "conflicts": blocking_conflicts[:MAX_REPORTED_CONFLICTS],
        "conflictsTruncated": len(blocking_conflicts) > MAX_REPORTED_CONFLICTS,
        "advisoryCount": len(advisories),
        "advisories": advisories[:MAX_REPORTED_CONFLICTS],
        "advisoriesTruncated": len(advisories) > MAX_REPORTED_CONFLICTS,
        "missingDependencyPolicy": "smoke_check" if allow_missing else "blocking",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Report missing metadata dependencies as advisories; the caller must run a feature-specific smoke check.",
    )
    args = parser.parse_args(argv)
    try:
        result = verify_dependency_compatibility(
            Path(args.target),
            allow_missing=bool(args.allow_missing),
        )
    except Exception as error:
        result = {
            "ok": False,
            "kind": "python_dependency_compatibility",
            "conflictCount": 1,
            "conflicts": [{"reason": "verification_failed", "error": type(error).__name__}],
            "conflictsTruncated": False,
        }
    print(f"{RESULT_MARKER}{json.dumps(result, ensure_ascii=True, sort_keys=True)}")
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
