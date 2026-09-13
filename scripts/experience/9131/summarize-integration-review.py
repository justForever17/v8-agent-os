"""Reduce synthetic browser observations without converting unrun work to PASS."""
import hashlib
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
prefix = repo / "tmp/experience-9131"
candidate = "c03b7ebb72dfa0a80bb072ab56e5f638669e963d"
admin_dir = prefix / "admin-candidate-c03b7ebb"
admin = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(admin_dir.glob("focused-*.json"))]
extensions = [json.loads((prefix / directory / "evidence.json").read_text(encoding="utf-8")) for directory in [
    "extensions-candidate-c03b7ebb", "extensions-candidate-c03b7ebb-footer", "extensions-candidate-c03b7ebb-header"]]


def surface(value):
    return {key: value[key] for key in ["theme", "viewport", "topbar", "headings", "tokens", "horizontalOverflow", "stylesheets"]}


rows = []
for run in admin:
    for row in run["evidence"]:
        if row["id"].startswith("A01"):
            for measurement in row["details"]:
                measurement["surface"] = surface(measurement["surface"])
        elif row["id"].startswith("V01"):
            for observation in row["details"]["observations"]:
                observation["surface"] = surface(observation["surface"])
        rows.append(row)
corrected = []
for run in extensions:
    for row in run["evidence"]:
        if row["status"] == "HARNESS_ERROR":
            corrected.append(row)
            continue
        if row["id"].startswith("X14"):
            for variant in row["details"]["variants"]:
                variant["store"] = surface(variant["store"])
                variant["manager"] = surface(variant["manager"])
        rows.append(row)
all_runs = admin + extensions
assert all(run["candidateCommit"] == candidate for run in all_runs)
assert all(run["targetUrl"] == "http://127.0.0.1:22938" for run in all_runs)
css_sets = []
for run in all_runs:
    assets = run["assetHashes"]
    if isinstance(assets, dict):
        assets = [{"path": path, **values} for path, values in assets.items()]
    css_sets.append({asset["path"]: asset["sha256"] for asset in assets if asset["path"].endswith(".css")})
assert all(value == css_sets[0] for value in css_sets)
assert len(css_sets[0]) == 2
screenshots = [admin_dir / "save-390-light.png", admin_dir / "visual-dark-390-1.png",
    prefix / "extensions-candidate-c03b7ebb/shell-store-390-dark.png",
    prefix / "extensions-candidate-c03b7ebb/shell-manager-390-light.png",
    prefix / "extensions-candidate-c03b7ebb-footer/footer-390-dark.png",
    prefix / "extensions-candidate-c03b7ebb-header/header-actions-390-light.png",
    prefix / "extensions-candidate-c03b7ebb-header/header-actions-390-dark.png"]
report = {
    "candidate": candidate, "buildId": "LwPhR5Q4--pMNfvE5JoWr", "targetUrl": "http://127.0.0.1:22938",
    "runtime": "owner-frozen managed production standalone", "browser": admin[0]["browserVersion"],
    "sourcePackageVersions": {"product-ui": "0.0.16", "session-realtime": "0.0.46"},
    "qualification": "Source package versions independently read from frozen Git. Build/commit association and archive integrity supplied by coordinator. Identical delivered CSS bytes independently compared across Admin and Extensions runs.",
    "commonCssHashes": css_sets[0], "rows": rows,
    "headerActionMeasurements": extensions[-1]["headerActionMeasurements"],
    "correctedHarnessRuns": corrected,
    "harnessCorrection": "New X05 setup saw both summary and footer receipts. Readiness now waits the first receipt; the original post-README viewport checks remain unchanged and pass in a separate run.",
    "runs": [{"requests": len(run["requests"]), "syntheticWrites": len(run.get("writes", [])),
              "syntheticOperations": len(run.get("submitted", [])), "syntheticMcpSaves": len(run.get("configSaves", [])),
              "uncaughtErrors": run.get("pageErrors", run.get("errors", [])), "summary": run["summary"]} for run in all_runs],
    "screenshotHashes": {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in screenshots},
    "remainingFailure": "At 390px in both themes, Refresh Extensions x371+105 reaches476 behind clip right390. Root overflow is hidden, so scrollWidth alone missed the inaccessible action.",
    "limitations": ["Business APIs intercepted; only public synthetic owner setup/auth reached the isolated application state.",
        "No Engine save, real install, runtime permission or actual process effect claimed.",
        "Canvas callback timing is one instrumented segment, not comparative FPS/INP. Background uses explicit visibility signal injection.",
        "Root-font enlargement does not constitute real browser 200% zoom.",
        "No full 40-route save sweep, Phone physical device, Web/Shell/package or final release approval."],
    "browsersClosedAndFreezeReleased": True,
}
destination = repo / "scripts/experience/9131/reports/integration-c03b7ebb-review.json"
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"destination": str(destination), "checks": len(rows), "cssIdentical": True,
                  "status": {status: sum(row["status"] == status for row in rows) for status in ["PASS", "FAIL"]}}))
