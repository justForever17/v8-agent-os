"""Reduce synthetic browser observations to reviewable, source-controlled evidence."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
args = p.parse_args()
data = json.loads(args.input.read_text(encoding="utf-8"))
rows = []
for row in data["rows"]:
    if "controls" not in row:
        rows.append(row)
        continue
    viewport = row["viewport"]
    actions = []
    for control in row["controls"]:
        if control["name"] not in ["保存", "安装", "取消", "展开详情"]:
            continue
        box = control["rect"]
        actions.append({"name": control["name"], "rect": box, "insideViewportBounds": box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= viewport["width"] and box["y"] + box["height"] <= viewport["height"]})
    rows.append({key: row[key] for key in ["route", "url", "level", "viewport", "rootFont", "viewportOverflow", "spinners", "animationCount", "screenshot", "dialogs", "navigationMs", "performanceQualification"] if key in row} | {"layoutControlCount": len(row["controls"]), "primaryActionBounds": actions, "scrollHosts": row["scrollHosts"], "dimensions": {"function": "NOT_RUN", "convenience": "OBSERVED_CANDIDATE", "visual": "OBSERVED_CANDIDATE", "performance": "NOT_RUN"}})
report = {"sourceHead": data["sourceHead"], "fixture": data["fixture"], "runtime": data["runtime"], "qualification": "Single post-networkidle snapshots, followed by awaited long-detail interaction. Layout controls include offscreen elements. Frame observations do not prove permanent spinner, correct save, native behavior, or pixel equivalence. Initial all-route pass narrow capture may overlap dialog exit animation; focused run waits for hidden dialog.", "browserVersion": data.get("browserVersion", "See focused performance run"), "rowCount": len(rows), "requestCount": len(data["requests"]), "unhandledErrorCount": len(data["errors"]), "rows": rows}
if "performance" in data:
    report["performance"] = data["performance"]
if "harnessError" in data:
    report["harnessError"] = data["harnessError"]
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"rows": len(rows), "requestCount": report["requestCount"], "unhandledErrorCount": report["unhandledErrorCount"]}))
