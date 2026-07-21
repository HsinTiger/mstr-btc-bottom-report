#!/usr/bin/env python3
"""Audit product pages against explicit purpose, data, freshness, and lifecycle contracts."""

from __future__ import annotations

import json
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "site_registry.json"
OUTPUT_PATH = ROOT / "data" / "daily" / "site_health.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def nested(data: Any, field: str | None) -> Any:
    if not field:
        return None
    current = data
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def local_links(html: str) -> list[str]:
    links = []
    for value in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        target = value.split("#", 1)[0].split("?", 1)[0]
        if not target or "${" in target or target.startswith(("http://", "https://", "mailto:", "data:", "javascript:")):
            continue
        links.append(target)
    return sorted(set(links))


def meta_content(html: str, name: str) -> str | None:
    match = re.search(
        rf'<meta\s+[^>]*name=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def check_dependency(item: dict[str, Any], now: datetime) -> dict[str, Any]:
    path = ROOT / item["path"]
    result = {"path": item["path"], "required": bool(item.get("required", True)), "status": "pass"}
    if not path.exists():
        result.update({"status": "fail" if result["required"] else "degraded", "reason": "檔案不存在"})
        return result
    result["bytes"] = path.stat().st_size
    if not any(item.get(key) for key in ("timestamp_field", "quality_field", "required_fields", "schema_value", "lineage_field", "lineage_source_field")):
        return result
    try:
        data = load_json(path)
    except (json.JSONDecodeError, OSError) as error:
        result.update({"status": "fail" if result["required"] else "degraded", "reason": f"JSON 無法解析：{error}"})
        return result
    if "schema_value" in item and data.get(item.get("schema_field", "schema")) != item["schema_value"]:
        result.update({"status": "fail" if result["required"] else "degraded", "reason": "schema 版本不相容"})
        return result
    missing_fields = [field for field in item.get("required_fields", []) if nested(data, field) is None]
    if missing_fields:
        result.update({"status": "fail" if result["required"] else "degraded", "reason": f"必要欄位缺漏：{', '.join(missing_fields)}"})
        return result
    for result_key, item_key in (("lineage", "lineage_field"), ("lineage_source", "lineage_source_field")):
        if item.get(item_key):
            lineage = parse_time(nested(data, item[item_key]))
            if lineage is None:
                result.update({"status": "fail" if result["required"] else "degraded", "reason": "批次血緣時間戳缺漏或格式錯誤"})
                return result
            result[result_key] = lineage.isoformat()
    if item.get("timestamp_field"):
        value = nested(data, item["timestamp_field"])
        timestamp = parse_time(value)
        if timestamp is None:
            result.update({"status": "fail" if result["required"] else "degraded", "reason": "時間戳缺漏或格式錯誤"})
            return result
        age_hours = (now - timestamp).total_seconds() / 3600
        result.update({"as_of": timestamp.isoformat(), "age_hours": round(age_hours, 2)})
        if age_hours < -1:
            result.update({"status": "fail", "reason": "時間戳位於未來"})
            return result
        if age_hours > float(item.get("max_age_hours", 10**9)):
            result.update({"status": "fail" if result["required"] else "degraded", "reason": "超過新鮮度契約"})
            return result
    if item.get("quality_field"):
        quality_value = nested(data, item["quality_field"])
        result["quality"] = quality_value
        if quality_value in item.get("fail_values", []):
            result.update({"status": item.get("fail_result", "fail"), "reason": f"資料品質狀態為 {quality_value}"})
        elif quality_value in item.get("degraded_values", []):
            result.update({"status": "degraded", "reason": f"資料品質狀態為 {quality_value}"})
    return result


def audit_page(
    page: dict[str, Any],
    now: datetime,
    active_paths: list[str],
    paths_by_id: dict[str, str],
    data_contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    html_path = ROOT / page["path"]
    checks: list[dict[str, Any]] = []
    if not html_path.exists():
        checks.append({"name": "page_exists", "status": "fail", "reason": "頁面不存在"})
        return {**page, "status": "fail", "surface_status": "fail", "data_status": "pass", "checks": checks, "dependencies": []}

    html = html_path.read_text(encoding="utf-8-sig")
    checks.append({"name": "page_exists", "status": "pass"})
    title_match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
    checks.append({
        "name": "title_contract",
        "status": "pass" if page.get("title_contains", "") in title else "fail",
        "reason": None if page.get("title_contains", "") in title else f"title 與責任名稱不符：{title}",
    })
    section_marker = meta_content(html, "app-section")
    checks.append({
        "name": "owner_marker",
        "status": "pass" if section_marker == page["id"] else "fail",
        "reason": None if section_marker == page["id"] else "app-section owner marker 缺漏或錯配",
    })

    if page["lifecycle"] == "active":
        purpose = meta_content(html, "app-purpose")
        cadence = meta_content(html, "app-update")
        checks.append({
            "name": "purpose_contract",
            "status": "pass" if purpose and "本專區回答：" in html else "fail",
            "reason": None if purpose and "本專區回答：" in html else "缺少用途 metadata 或頁面內唯一問題聲明",
        })
        checks.append({
            "name": "cadence_contract",
            "status": "pass" if cadence else "fail",
            "reason": None if cadence else "缺少 app-update 更新節奏",
        })
        links = local_links(html)
        missing_navigation = [path for path in active_paths if path not in links]
        checks.append({
            "name": "primary_navigation",
            "status": "pass" if not missing_navigation else "fail",
            "reason": None if not missing_navigation else "主要導航缺少維護中專區",
            "missing": missing_navigation,
        })

    if page["lifecycle"] == "archive":
        archive_ok = "封存" in html and "不再代表目前" in html
        checks.append({"name": "archive_disclosure", "status": "pass" if archive_ok else "fail", "reason": None if archive_ok else "封存頁未清楚聲明不再代表目前體系"})
        replacement_path = paths_by_id.get(str(page.get("replacement")))
        replacement_ok = not replacement_path or replacement_path in local_links(html)
        checks.append({
            "name": "archive_replacement",
            "status": "pass" if replacement_ok else "fail",
            "reason": None if replacement_ok else "封存頁未連向目前替代專區",
        })
    if page.get("surface_type") == "mixed_live_and_versioned_research":
        research_ok = "研究樣本截至" in html and str(page.get("research_as_of")) in html and "不是今日決策首頁" in html
        checks.append({"name": "mixed_surface_disclosure", "status": "pass" if research_ok else "fail", "reason": None if research_ok else "混合頁未分清每日資料與歷史研究樣本"})

    broken_links = [link for link in local_links(html) if not (ROOT / link).exists()]
    checks.append({"name": "local_links", "status": "pass" if not broken_links else "fail", "broken": broken_links})

    dependency_specs = [{**data_contracts.get(item["path"], {}), **item} for item in page.get("dependencies", [])]
    dependency_results = [check_dependency(item, now) for item in dependency_specs]
    lineage_source = next((item.get("lineage_source") for item in dependency_results if item.get("lineage_source")), None)
    if lineage_source:
        for dependency in dependency_results:
            if dependency.get("lineage") and dependency["lineage"] != lineage_source:
                dependency.update({"status": "fail", "reason": "衍生資料與目前快照批次不一致"})
    for dependency in page.get("dependencies", []):
        if dependency["path"].endswith((".json", ".md")) and dependency["path"] not in html and page["id"] != "site-governance":
            checks.append({"name": f"dependency_wired:{dependency['path']}", "status": "degraded", "reason": "頁面未直接引用已登記依賴；需確認是否經其他產物間接供應"})

    check_statuses = [item["status"] for item in checks]
    dependency_statuses = [item["status"] for item in dependency_results]
    surface_status = "fail" if "fail" in check_statuses else "degraded" if "degraded" in check_statuses else "pass"
    data_status = "fail" if "fail" in dependency_statuses else "degraded" if "degraded" in dependency_statuses else "pass"
    status = "fail" if "fail" in {surface_status, data_status} else "degraded" if "degraded" in {surface_status, data_status} else "pass"
    return {
        **page,
        "status": status,
        "surface_status": surface_status,
        "data_status": data_status,
        "checks": checks,
        "dependencies": dependency_results,
    }


def main(allow_fail_closed_data: bool = False) -> int:
    registry = load_json(REGISTRY_PATH)
    now = datetime.now(timezone.utc)
    registry_pages = registry.get("pages", [])
    active_paths = [page["path"] for page in registry_pages if page.get("lifecycle") == "active"]
    paths_by_id = {page["id"]: page["path"] for page in registry_pages}
    data_contracts = registry.get("data_contracts", {})
    pages = [audit_page(page, now, active_paths, paths_by_id, data_contracts) for page in registry_pages]
    active = [page for page in pages if page.get("lifecycle") == "active"]
    critical_surface_failures = [page["id"] for page in active if page["surface_status"] == "fail" and page.get("criticality") in {"critical", "high"}]
    critical_data_failures = [page["id"] for page in active if page["data_status"] == "fail" and page.get("criticality") in {"critical", "high"}]
    degraded = [page["id"] for page in active if page["status"] == "degraded"]
    overall = "fail" if critical_surface_failures or critical_data_failures else "degraded" if any(page["status"] != "pass" for page in active) else "pass"
    output = {
        "schema": 1,
        "generated_at": now_iso(),
        "status": overall,
        "summary": {
            "active_pages": len(active),
            "archive_pages": len(pages) - len(active),
            "pass": sum(page["status"] == "pass" for page in active),
            "degraded": sum(page["status"] == "degraded" for page in active),
            "fail": sum(page["status"] == "fail" for page in active),
            "critical_failures": critical_surface_failures + critical_data_failures,
            "critical_surface_failures": critical_surface_failures,
            "critical_data_failures": critical_data_failures,
            "degraded_pages": degraded,
        },
        "pages": pages,
        "governance_rules": [
            "每個 active 專區必須有唯一問題、資料依賴與更新頻率。",
            "頁面不得用未驗證即時資料覆蓋已驗證每日資料。",
            "每日資料與歷史研究樣本混放時必須明示兩個日期。",
            "封存頁不得出現在主要導航，且必須指向目前替代專區。",
            "缺值、過期、schema 或日期不一致時 fail closed。"
        ],
        "publication_mode": "fail_closed_diagnostics_only" if critical_data_failures and not critical_surface_failures else "normal",
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"site_health": str(OUTPUT_PATH), "status": overall, **output["summary"]}, ensure_ascii=False))
    if critical_surface_failures:
        return 1
    if critical_data_failures and not allow_fail_closed_data:
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-fail-closed-data",
        action="store_true",
        help="Publish explicit red diagnostic state when data fails, while still blocking any green decision output.",
    )
    args = parser.parse_args()
    raise SystemExit(main(allow_fail_closed_data=args.allow_fail_closed_data))
