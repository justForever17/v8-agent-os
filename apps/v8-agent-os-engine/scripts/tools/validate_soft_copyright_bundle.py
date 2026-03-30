from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document

from soft_copyright_common import A4_HEIGHT_CM, A4_WIDTH_CM, META_PATH, PAGE_MARGIN_CM, load_bundle_meta

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PAGE_TOLERANCE_CM = 0.2
PATH_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+:\d{4}\s+\|\s")

ACCEPTABLE_SONG_FONTS = {"宋体", "SimSun"}
ACCEPTABLE_HEI_FONTS = {"黑体", "SimHei"}


def _cm(value) -> float:
    return round(value.cm, 2)


def _check_dimensions(document: Document) -> dict:
    section = document.sections[0]
    checks = {
        "pageWidthCm": _cm(section.page_width),
        "pageHeightCm": _cm(section.page_height),
        "topMarginCm": _cm(section.top_margin),
        "bottomMarginCm": _cm(section.bottom_margin),
        "leftMarginCm": _cm(section.left_margin),
        "rightMarginCm": _cm(section.right_margin),
    }
    checks["isA4Portrait"] = (
        abs(checks["pageWidthCm"] - A4_WIDTH_CM) <= PAGE_TOLERANCE_CM
        and abs(checks["pageHeightCm"] - A4_HEIGHT_CM) <= PAGE_TOLERANCE_CM
    )
    checks["marginsAre25mm"] = all(
        abs(checks[key] - PAGE_MARGIN_CM) <= PAGE_TOLERANCE_CM
        for key in ("topMarginCm", "bottomMarginCm", "leftMarginCm", "rightMarginCm")
    )
    return checks


def _read_zip_xml(docx_path: Path, member_name: str) -> str:
    with zipfile.ZipFile(docx_path) as archive:
        return archive.read(member_name).decode("utf-8", errors="ignore")


def _load_header_xml(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path) as archive:
        header_files = [name for name in archive.namelist() if name.startswith("word/header")]
        if not header_files:
            return ""
        return archive.read(header_files[0]).decode("utf-8", errors="ignore")


def _extract_header_text(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path) as archive:
        header_files = [name for name in archive.namelist() if name.startswith("word/header")]
        if not header_files:
            return ""
        root = ET.fromstring(archive.read(header_files[0]))
    texts = [elem.text or "" for elem in root.iter(f"{W_NS}t")]
    return "".join(texts)


def _extract_header_table_cells(docx_path: Path) -> list[str]:
    with zipfile.ZipFile(docx_path) as archive:
        header_files = [name for name in archive.namelist() if name.startswith("word/header")]
        if not header_files:
            return []
        root = ET.fromstring(archive.read(header_files[0]))
    cells: list[str] = []
    for cell in root.iter(f"{W_NS}tc"):
        texts = [elem.text or "" for elem in cell.iter(f"{W_NS}t")]
        text = "".join(texts).strip()
        if text:
            cells.append(text)
    return cells


def _extract_style_font_maps(docx_path: Path) -> dict[str, dict[str, str | None]]:
    root = ET.fromstring(_read_zip_xml(docx_path, "word/styles.xml"))
    result: dict[str, dict[str, str | None]] = {}
    for style in root.iter(f"{W_NS}style"):
        style_id = style.attrib.get(f"{W_NS}styleId")
        if not style_id:
            continue
        r_pr = style.find(f"{W_NS}rPr")
        if r_pr is None:
            continue
        r_fonts = r_pr.find(f"{W_NS}rFonts")
        if r_fonts is None:
            continue
        result[style_id] = {
            "ascii": r_fonts.attrib.get(f"{W_NS}ascii"),
            "hAnsi": r_fonts.attrib.get(f"{W_NS}hAnsi"),
            "eastAsia": r_fonts.attrib.get(f"{W_NS}eastAsia"),
            "cs": r_fonts.attrib.get(f"{W_NS}cs"),
        }
    return result


def _count_page_breaks(docx_path: Path) -> int:
    root = ET.fromstring(_read_zip_xml(docx_path, "word/document.xml"))
    return sum(1 for elem in root.iter(f"{W_NS}br") if elem.attrib.get(f"{W_NS}type") == "page")


def _has_media(docx_path: Path) -> bool:
    with zipfile.ZipFile(docx_path) as archive:
        return any(name.startswith("word/media/") for name in archive.namelist())


def _line_is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith(("#", "//", "/*", "*/", '"""', "'''")):
        return True
    return stripped == "*" or stripped.startswith("* ")


def _line_has_path_prefix(line: str) -> bool:
    return bool(PATH_PREFIX_RE.match(line))


def _font_map_matches(font_map: dict[str, str | None], acceptable: set[str]) -> bool:
    return all((font_map.get(key) or "") in acceptable for key in ("ascii", "hAnsi", "eastAsia", "cs"))


def _run_word_render_probe(docx_path: Path) -> dict:
    ps_script = rf"""
$ErrorActionPreference = "Stop"
    $docPath = {json.dumps(str(docx_path), ensure_ascii=False)}
$word = $null
try {{
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($docPath, $false, $true)
    try {{
        $doc.Repaginate()
        $wdStatisticLines = 1
        $wdStatisticPages = 2
        $wdGoToPage = 1
        $wdGoToAbsolute = 1
        $pageCount = $doc.ComputeStatistics($wdStatisticPages)
        $linesByPage = @()
        for ($page = 1; $page -le $pageCount; $page++) {{
            $startRange = $doc.GoTo($wdGoToPage, $wdGoToAbsolute, $page)
            if ($page -lt $pageCount) {{
                $nextRange = $doc.GoTo($wdGoToPage, $wdGoToAbsolute, $page + 1)
                $endPos = $nextRange.Start - 1
            }} else {{
                $endPos = $doc.Content.End
            }}
            $pageRange = $doc.Range($startRange.Start, $endPos)
            $linesByPage += [pscustomobject]@{{
                page = $page
                lineCount = $pageRange.ComputeStatistics($wdStatisticLines)
            }}
        }}

        $sampleRange = $null
        for ($index = 1; $index -le $doc.Paragraphs.Count; $index++) {{
            $candidate = $doc.Paragraphs.Item($index).Range
            if ($candidate.Text.Trim()) {{
                $sampleRange = $candidate
                break
            }}
        }}
        if (-not $sampleRange) {{
            $sampleRange = $doc.Content
        }}

        $headerRange = $doc.Sections.Item(1).Headers.Item(1).Range
        $headerCells = @()
        if ($headerRange.Tables.Count -gt 0) {{
            $table = $headerRange.Tables.Item(1)
            for ($cell = 1; $cell -le $table.Columns.Count; $cell++) {{
                $text = $table.Cell(1, $cell).Range.Text
                $text = $text -replace "`r", ""
                $text = $text -replace [char]7, ""
                $headerCells += $text.Trim()
            }}
        }}

        [ordered]@{{
            available = $true
            pageCount = $pageCount
            linesByPage = $linesByPage
            headerTableCount = $headerRange.Tables.Count
            headerCells = $headerCells
            sampleFont = [ordered]@{{
                name = $sampleRange.Font.Name
                ascii = $sampleRange.Font.NameAscii
                farEast = $sampleRange.Font.NameFarEast
                other = $sampleRange.Font.NameOther
            }}
        }} | ConvertTo-Json -Depth 6 -Compress
    }} finally {{
        $doc.Close()
    }}
}} catch {{
    [ordered]@{{
        available = $false
        error = $_.Exception.Message
    }} | ConvertTo-Json -Compress
}} finally {{
    if ($word -ne $null) {{
        $word.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    }}
}}
"""

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    raw_output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        return {"available": False, "error": raw_output or f"powershell exit {completed.returncode}"}
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        return {"available": False, "error": raw_output or "Word 渲染探测输出无法解析。"}


def validate(bundle_dir: Path, repo_root: Path) -> tuple[dict, str]:
    meta = load_bundle_meta(repo_root)
    manual_docx = bundle_dir / "04_用户手册.docx"
    manual_stats = bundle_dir / "04_用户手册_stats.json"
    source_dir = bundle_dir / "05_源程序材料"
    source_docx = source_dir / "source_program_excerpt.docx"
    source_txt = source_dir / "source_program_excerpt.txt"
    source_stats = source_dir / "source_program_stats.json"
    apply_form = bundle_dir / "01_申请主表信息.md"

    manual_document = Document(manual_docx)
    source_document = Document(source_docx)

    manual_header_xml = _load_header_xml(manual_docx)
    source_header_xml = _load_header_xml(source_docx)
    manual_header_text = _extract_header_text(manual_docx)
    source_header_text = _extract_header_text(source_docx)
    manual_header_cells = _extract_header_table_cells(manual_docx)
    source_header_cells = _extract_header_table_cells(source_docx)
    manual_style_fonts = _extract_style_font_maps(manual_docx)
    source_style_fonts = _extract_style_font_maps(source_docx)

    source_lines = source_txt.read_text(encoding="utf-8").splitlines()
    source_stats_data = json.loads(source_stats.read_text(encoding="utf-8"))
    manual_stats_data = json.loads(manual_stats.read_text(encoding="utf-8"))
    apply_form_text = apply_form.read_text(encoding="utf-8")

    source_blank_or_comment = sum(1 for line in source_lines if _line_is_comment_or_blank(line))
    source_path_prefix = sum(1 for line in source_lines if _line_has_path_prefix(line))
    source_page_breaks = _count_page_breaks(source_docx)

    manual_dimensions = _check_dimensions(manual_document)
    source_dimensions = _check_dimensions(source_document)
    manual_render = _run_word_render_probe(manual_docx)
    source_render = _run_word_render_probe(source_docx)

    result = {
        "meta": meta,
        "manual": {
            "dimensions": manual_dimensions,
            "styleFonts": manual_style_fonts,
            "hasExpectedHeaderText": f"{meta['softwareName']} {meta['version']} {meta['manualHeaderLabel']}" in manual_header_text,
            "headerCells": manual_header_cells,
            "hasPageField": "PAGE" in manual_header_xml,
            "hasNumPagesField": "NUMPAGES" in manual_header_xml,
            "hasMedia": _has_media(manual_docx),
            "estimatedPageCount": manual_stats_data.get("estimatedPageCount"),
            "estimatedPageCountWithinLimit": int(manual_stats_data.get("estimatedPageCount", 0)) <= 60,
            "render": manual_render,
        },
        "source": {
            "dimensions": source_dimensions,
            "styleFonts": source_style_fonts,
            "hasExpectedHeaderText": f"{meta['softwareName']} {meta['version']} {meta['sourceHeaderLabel']}" in source_header_text,
            "headerCells": source_header_cells,
            "hasPageField": "PAGE" in source_header_xml,
            "hasTotalPagesLiteral": "共60页" in source_header_text,
            "hasMedia": _has_media(source_docx),
            "lineCount": len(source_lines),
            "blankOrCommentLines": source_blank_or_comment,
            "pathPrefixedLines": source_path_prefix,
            "pageBreakCount": source_page_breaks,
            "pageCount": source_page_breaks + 1,
            "stats": source_stats_data,
            "render": source_render,
        },
        "consistency": {
            "applyFormMatchesSoftwareName": meta["softwareName"] in apply_form_text,
            "applyFormMatchesVersion": meta["version"] in apply_form_text,
            "metaPath": str((repo_root / META_PATH).resolve()),
        },
    }

    failed_checks: list[str] = []
    if not manual_dimensions["isA4Portrait"] or not manual_dimensions["marginsAre25mm"]:
        failed_checks.append("用户手册版式不符合 A4 纵向/2.5cm 页边距要求。")
    if not result["manual"]["hasExpectedHeaderText"] or not result["manual"]["hasPageField"] or not result["manual"]["hasNumPagesField"]:
        failed_checks.append("用户手册页眉未正确写入软件名称、版本号或 PAGE/NUMPAGES 域。")
    if len(manual_header_cells) < 2:
        failed_checks.append("用户手册页眉未生成左右分栏表格。")
    if not _font_map_matches(manual_style_fonts.get("Normal", {}), ACCEPTABLE_SONG_FONTS):
        failed_checks.append("用户手册正文样式没有稳定锁定宋体。")
    if not _font_map_matches(manual_style_fonts.get("Heading1", {}), ACCEPTABLE_HEI_FONTS):
        failed_checks.append("用户手册一级标题样式没有稳定锁定黑体。")
    if not _font_map_matches(manual_style_fonts.get("Heading2", {}), ACCEPTABLE_HEI_FONTS):
        failed_checks.append("用户手册二级标题样式没有稳定锁定黑体。")
    if result["manual"]["hasMedia"]:
        failed_checks.append("用户手册中存在图片或媒体资源。")
    if not result["manual"]["estimatedPageCountWithinLimit"]:
        failed_checks.append("用户手册预计页数超过 60 页。")
    if not manual_render.get("available"):
        failed_checks.append("用户手册没有通过 Word 渲染校验。")
    elif int(manual_render.get("pageCount", 0)) > 60:
        failed_checks.append("用户手册在 Word 中实际页数超过 60 页。")

    if not source_dimensions["isA4Portrait"] or not source_dimensions["marginsAre25mm"]:
        failed_checks.append("源程序材料版式不符合 A4 纵向/2.5cm 页边距要求。")
    if not result["source"]["hasExpectedHeaderText"] or not result["source"]["hasPageField"] or not result["source"]["hasTotalPagesLiteral"]:
        failed_checks.append("源程序页眉未正确写入软件名称、版本号或总页数。")
    if len(source_header_cells) < 2:
        failed_checks.append("源程序页眉未生成左右分栏表格。")
    if not _font_map_matches(source_style_fonts.get("Normal", {}), ACCEPTABLE_SONG_FONTS):
        failed_checks.append("源程序正文样式没有稳定锁定宋体。")
    if result["source"]["hasMedia"]:
        failed_checks.append("源程序材料中存在图片或媒体资源。")
    if result["source"]["lineCount"] != 3000:
        failed_checks.append("源程序节选不是 3000 行。")
    if result["source"]["blankOrCommentLines"] != 0:
        failed_checks.append("源程序节选仍包含空行或注释行。")
    if result["source"]["pathPrefixedLines"] != 0:
        failed_checks.append("源程序节选仍包含路径前缀。")
    if result["source"]["pageCount"] != 60:
        failed_checks.append("源程序 docx 结构页数不是 60 页。")
    if not source_render.get("available"):
        failed_checks.append("源程序材料没有通过 Word 渲染校验。")
    else:
        actual_page_count = int(source_render.get("pageCount", 0))
        if actual_page_count != 60:
            failed_checks.append(f"源程序材料在 Word 中实际页数不是 60 页，而是 {actual_page_count} 页。")
        source_page_lines = source_render.get("linesByPage", [])
        if len(source_page_lines) != 60 or any(int(item.get("lineCount", 0)) < 50 for item in source_page_lines):
            failed_checks.append("源程序材料在 Word 中未达到每页至少 50 行的目标。")

    if not result["consistency"]["applyFormMatchesSoftwareName"] or not result["consistency"]["applyFormMatchesVersion"]:
        failed_checks.append("申请主表信息与元数据中的软件名称或版本号不一致。")

    result["status"] = "pass" if not failed_checks else "fail"
    result["failedChecks"] = failed_checks
    result["warnings"] = [
        "Word 渲染结果已纳入正式验收；WPS 仍建议人工抽检，但不再作为唯一判断依据。",
    ]

    md_lines = [
        "# V8 Agent OS 软著材料校验报告",
        "",
        f"- 校验结果：`{result['status']}`",
        f"- 软件名称：`{meta['softwareName']}`",
        f"- 版本号：`{meta['version']}`",
        "",
        "## 1. 用户手册检查",
        "",
        f"- A4 纵向：`{result['manual']['dimensions']['isA4Portrait']}`",
        f"- 四边 2.5cm 页边距：`{result['manual']['dimensions']['marginsAre25mm']}`",
        f"- 页眉软件名与版本号：`{result['manual']['hasExpectedHeaderText']}`",
        f"- 页眉表格单元格：`{result['manual']['headerCells']}`",
        f"- PAGE 域：`{result['manual']['hasPageField']}`",
        f"- NUMPAGES 域：`{result['manual']['hasNumPagesField']}`",
        f"- 预计页数：`{result['manual']['estimatedPageCount']}`",
        f"- Word 实际页数：`{result['manual']['render'].get('pageCount')}`",
        f"- 正文样式宋体：`{_font_map_matches(manual_style_fonts.get('Normal', {}), ACCEPTABLE_SONG_FONTS)}`",
        f"- 标题样式黑体：`{_font_map_matches(manual_style_fonts.get('Heading1', {}), ACCEPTABLE_HEI_FONTS) and _font_map_matches(manual_style_fonts.get('Heading2', {}), ACCEPTABLE_HEI_FONTS)}`",
        f"- 无图片/无水印资源：`{not result['manual']['hasMedia']}`",
        "",
        "## 2. 源程序材料检查",
        "",
        f"- A4 纵向：`{result['source']['dimensions']['isA4Portrait']}`",
        f"- 四边 2.5cm 页边距：`{result['source']['dimensions']['marginsAre25mm']}`",
        f"- 页眉软件名与版本号：`{result['source']['hasExpectedHeaderText']}`",
        f"- 页眉表格单元格：`{result['source']['headerCells']}`",
        f"- PAGE 域：`{result['source']['hasPageField']}`",
        f"- 总页数文字：`{result['source']['hasTotalPagesLiteral']}`",
        f"- 行数：`{result['source']['lineCount']}`",
        f"- 空行/注释行：`{result['source']['blankOrCommentLines']}`",
        f"- 路径前缀行：`{result['source']['pathPrefixedLines']}`",
        f"- docx 结构页数：`{result['source']['pageCount']}`",
        f"- Word 实际页数：`{result['source']['render'].get('pageCount')}`",
        f"- Word 每页行数：`{[item.get('lineCount') for item in result['source']['render'].get('linesByPage', [])]}`",
        f"- 正文字体宋体：`{_font_map_matches(source_style_fonts.get('Normal', {}), ACCEPTABLE_SONG_FONTS)}`",
        f"- 无图片/无水印资源：`{not result['source']['hasMedia']}`",
        "",
        "## 3. 一致性检查",
        "",
        f"- 申请主表软件名称一致：`{result['consistency']['applyFormMatchesSoftwareName']}`",
        f"- 申请主表版本号一致：`{result['consistency']['applyFormMatchesVersion']}`",
        "",
        "## 4. 失败项",
        "",
    ]
    if failed_checks:
        md_lines.extend(f"- {item}" for item in failed_checks)
    else:
        md_lines.append("- 无")
    md_lines.extend(["", "## 5. 提醒", ""])
    md_lines.extend(f"- {item}" for item in result["warnings"])

    return result, "\n".join(md_lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate software copyright bundle outputs.")
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    args = parser.parse_args()

    result, markdown = validate(args.bundle_dir, args.repo_root)
    (args.bundle_dir / "validation_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.bundle_dir / "validation_report.md").write_text(markdown, encoding="utf-8")
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
