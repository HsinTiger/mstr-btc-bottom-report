#!/usr/bin/env python3
"""Independently verify multi-source AI intelligence and ranking invariants."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from collect_ai_intelligence import (
    CATEGORIES,
    HISTORY_PATH,
    MAX_ITEMS_PER_CATEGORY,
    OUTPUT_PATH,
    SOURCES,
    WINDOW_HOURS,
    parse_time,
    relevance_score,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "daily" / "ai_intelligence_verification.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(data: Any) -> None:
    REPORT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expected_score(category: dict[str, Any], source: dict[str, Any], item: dict[str, Any], generated: datetime, created: datetime) -> tuple[float, dict[str, float]]:
    relevance = relevance_score(category, str(item.get("text") or ""), source)
    age_hours = max(0.0, (generated - created).total_seconds() / 3600)
    recency = max(0.0, 1 - age_hours / WINDOW_HOURS)
    components = {
        "source_quality": round(100 * float(source["weight"]), 1),
        "keyword_relevance": round(100 * relevance, 1),
        "recency": round(100 * recency, 1),
    }
    score = round(100 * (0.50 * float(source["weight"]) + 0.30 * relevance + 0.20 * recency), 1)
    return score, components


def main() -> int:
    source = json.loads(OUTPUT_PATH.read_text(encoding="utf-8-sig"))
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8-sig"))
    failures: list[str] = []
    degradations: list[str] = []
    generated = parse_time(source.get("generated_at"))
    current = datetime.now(timezone.utc)
    if source.get("schema") != 1 or generated is None:
        failures.append("AI 情報 schema 或 generated_at 錯誤")
    elif generated > current + timedelta(minutes=5) or current - generated > timedelta(hours=30):
        failures.append("AI 情報時間戳位於未來或超過每日新鮮度契約")
    if source.get("window_hours") != WINDOW_HOURS:
        failures.append(f"AI 情報觀察視窗必須為 {WINDOW_HOURS} 小時")
    quality = source.get("quality", {})
    if quality.get("provider") != "official_feeds_github_releases_arxiv":
        failures.append("AI 情報 provider 契約錯配")
    if quality.get("execution_gate_eligible") is not False:
        failures.append("AI 情報不得進入交易執行閘門")

    configs = {item["key"]: item for item in SOURCES}
    checks = source.get("source_checks", [])
    check_map = {item.get("source_key"): item for item in checks if isinstance(item, dict)}
    if set(check_map) != set(configs):
        failures.append("AI 情報來源檢查清單不完整或含未知來源")
    for key, config in configs.items():
        check = check_map.get(key, {})
        if check.get("url") != config["url"] or check.get("source_type") != config["kind"] or check.get("category_id") != config["category_id"]:
            failures.append(f"來源 {key} 的 URL、類型或分類契約錯配")
        if check.get("status") not in {"pass", "fail"}:
            failures.append(f"來源 {key} 狀態未知")

    category_configs = {item["id"]: item for item in CATEGORIES}
    category_map = {item.get("id"): item for item in source.get("categories", []) if isinstance(item, dict)}
    if set(category_map) != set(category_configs):
        failures.append("AI 情報分類不完整或含未知分類")
    seen: set[str] = set()
    item_map: dict[str, dict[str, Any]] = {}
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for category_id, category_config in category_configs.items():
        category = category_map.get(category_id, {})
        items = category.get("items", [])
        category_counts[category_id] = len(items)
        expected_source_keys = [item["key"] for item in SOURCES if item["category_id"] == category_id]
        if category.get("source_keys") != expected_source_keys:
            failures.append(f"{category_config['title']}來源清單與程式設定不一致")
        if len(items) > MAX_ITEMS_PER_CATEGORY:
            failures.append(f"{category_config['title']}超過每類 {MAX_ITEMS_PER_CATEGORY} 則")
        scores: list[float] = []
        for item in items:
            item_id = str(item.get("id") or "")
            source_key = str(item.get("source_key") or "")
            config = configs.get(source_key)
            created = parse_time(item.get("created_at"))
            if not item_id or item_id in seen:
                failures.append(f"{category_config['title']}包含空白或重複 ID")
            seen.add(item_id)
            item_map[item_id] = item
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            if not config or config["category_id"] != category_id:
                failures.append(f"{category_config['title']}包含未知或跨分類來源 {source_key}")
                continue
            expected_hosts = set(config.get("allowed_hosts") or [urlparse(config["url"]).hostname])
            actual_url = str(item.get("url") or "")
            actual_host = urlparse(actual_url).hostname
            github_expected = config["kind"] == "github_release" and actual_host == "github.com"
            if not actual_url.startswith("https://") or (actual_host not in expected_hosts and not github_expected):
                failures.append(f"{category_config['title']}來源 URL 網域不符：{actual_url}")
            if item.get("source_type") != config["kind"] or item.get("source_tier") != config["tier"] or item.get("source_label") != config["label"]:
                failures.append(f"{category_config['title']}來源 metadata 錯配：{source_key}")
            if created is None or generated is None or created < generated - timedelta(hours=WINDOW_HOURS, minutes=5) or created > generated + timedelta(minutes=5):
                failures.append(f"{category_config['title']}日期不在觀察視窗內")
                continue
            if not all(str(item.get(field) or "").strip() for field in ("title", "text", "why_it_matters", "next_action")):
                failures.append(f"{category_config['title']}缺少標題、內文、解讀或行動")
            if item.get("decision_use") != "learning_context_not_execution_gate":
                failures.append(f"{category_config['title']}決策用途契約錯配")
            score = item.get("ranking_score_0_100")
            if not isinstance(score, (int, float)) or not 0 <= score <= 100:
                failures.append(f"{category_config['title']}排序分數超出範圍")
            else:
                scores.append(float(score))
                expected, components = expected_score(category_config, config, item, generated, created)
                if abs(float(score) - expected) > 0.11 or item.get("ranking_components_0_100") != components:
                    failures.append(f"{category_config['title']}排序分數無法獨立重算")
        if scores != sorted(scores, reverse=True):
            failures.append(f"{category_config['title']}未依排序分數遞減")
    if any(count > 3 for count in source_counts.values()):
        failures.append("單一替代來源超過每類三則的多樣性上限")

    actions = source.get("daily_actions", [])
    if len(item_map) >= 3 and len(actions) != 3:
        failures.append("有足夠消息時，每日精進行動必須正好三項")
    action_sources: set[str] = set()
    for action in actions:
        item = item_map.get(str(action.get("item_id") or ""))
        if not item or action.get("url") != item.get("url") or action.get("action") != item.get("next_action"):
            failures.append("每日精進行動未正確綁定來源消息")
            continue
        action_sources.add(str(item.get("source_key")))
    if len(actions) == 3 and len(action_sources) != 3:
        failures.append("每日三項精進行動必須來自三個不同來源")

    summary = source.get("summary", {})
    successful_sources = sum(check.get("status") == "pass" for check in checks)
    if summary.get("posts") != len(item_map) or summary.get("successful_sources") != successful_sources or summary.get("unique_sources") != len(source_counts):
        failures.append("AI 情報摘要計數與內容不一致")
    source_status = quality.get("status")
    if source_status == "pass":
        if any(count < 3 for count in category_counts.values()) or quality.get("failures") or quality.get("degradations"):
            failures.append("AI 情報 pass 與分類筆數或品質原因矛盾")
    elif source_status == "degraded":
        if not quality.get("degradations") or any(count == 0 for count in category_counts.values()):
            failures.append("AI 情報 degraded 必須有原因且兩類皆有內容")
        degradations.extend(str(item) for item in quality.get("degradations", []))
    elif source_status == "fail":
        failures.extend(str(item) for item in quality.get("failures", []) or ["AI 多來源收集失敗"])
    else:
        failures.append("AI 情報品質狀態未知")

    if history.get("schema") != 1 or history.get("last_attempt_at") != source.get("generated_at") or history.get("quality", {}).get("status") != source_status:
        failures.append("AI 情報歷史資料未綁定目前批次")
    history_items = history.get("items", [])
    if not isinstance(history_items, list) or len(history_items) > 2000:
        failures.append("AI 情報歷史資料型別或保留上限錯誤")

    status = "fail" if failures else "degraded" if degradations else "pass"
    report = {
        "schema": 1,
        "verified_at": now_iso(),
        "source_generated_at": source.get("generated_at"),
        "status": status,
        "failures": list(dict.fromkeys(failures)),
        "degradations": list(dict.fromkeys(degradations)),
        "category_counts": category_counts,
        "successful_sources": successful_sources,
        "daily_actions": len(actions),
        "history_items": len(history_items) if isinstance(history_items, list) else 0,
        "method": [
            "逐一核對官方 feed、GitHub Releases 與 arXiv 來源清單。",
            "獨立重算來源品質、關鍵字相關性與時效排序分數。",
            "每日三項行動必須綁定三個不同來源，且不進交易硬閘門。",
        ],
    }
    write_json(report)
    print(json.dumps({"status": status, "failures": len(report["failures"]), "degradations": len(report["degradations"]), "counts": category_counts, "sources": successful_sources, "actions": len(actions)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
