#!/usr/bin/env python3
"""Independently verify the X-intelligence artifact and its fail-closed behavior."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collect_x_intelligence import CATEGORIES, ENDPOINT, MAX_ITEMS_PER_CATEGORY, WINDOW_HOURS, parse_time

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "daily" / "x_intelligence.json"
HISTORY_PATH = ROOT / "data" / "daily" / "x_intelligence_history.json"
REPORT_PATH = ROOT / "data" / "daily" / "x_intelligence_verification.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(data: Any) -> None:
    REPORT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def term_matches(text: str, term: str) -> bool:
    normalized_text = re.sub(r"[-_]", " ", text.lower())
    normalized_term = re.sub(r"[-_]", " ", term.lower()).strip()
    escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
    plural = "" if normalized_term.endswith("s") else "s?"
    return re.search(rf"(?<![a-z0-9]){escaped}{plural}(?![a-z0-9])", normalized_text) is not None


def expected_ranking(config: dict[str, Any], item: dict[str, Any], generated: datetime, created: datetime) -> tuple[float, dict[str, float]]:
    username = str(item.get("username") or "").lower()
    source_weight = float(config["accounts"][username][1])
    hits = sum(1 for term in config["relevance_terms"] if term_matches(str(item.get("text") or ""), term))
    relevance = min(1.0, hits / 4)
    age_hours = max(0.0, (generated - created).total_seconds() / 3600)
    recency = max(0.0, 1 - age_hours / WINDOW_HOURS)
    metrics = item.get("public_metrics") or {}
    weighted = (
        float(metrics.get("likes") or 0)
        + 2 * float(metrics.get("reposts") or 0)
        + 1.5 * float(metrics.get("quotes") or 0)
        + 0.5 * float(metrics.get("replies") or 0)
    )
    engagement = min(1.0, math.log1p(weighted) / math.log(10_001))
    components = {
        "source_quality": round(100 * source_weight, 1),
        "keyword_relevance": round(100 * relevance, 1),
        "recency": round(100 * recency, 1),
        "engagement": round(100 * engagement, 1),
    }
    score = round(100 * (0.40 * source_weight + 0.30 * relevance + 0.20 * recency + 0.10 * engagement), 1)
    return score, components


def main() -> int:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8-sig"))
    failures: list[str] = []
    degradations: list[str] = []
    generated = parse_time(source.get("generated_at"))
    now = datetime.now(timezone.utc)
    if source.get("schema") != 1:
        failures.append("schema 必須為 1")
    if generated is None or (now - generated).total_seconds() > 30 * 3600 or generated > now + timedelta(minutes=5):
        failures.append("產物時間戳缺漏、過期或位於未來")
    quality = source.get("quality", {})
    if quality.get("api_provider") != "X API" or quality.get("endpoint") != ENDPOINT:
        failures.append("X API 供應商或 endpoint 契約錯配")
    if quality.get("execution_gate_eligible") is not False:
        failures.append("X 情報不得控制交易執行閘門")
    if source.get("window_hours") != WINDOW_HOURS:
        failures.append(f"X 情報觀察視窗必須為 {WINDOW_HOURS} 小時")
    category_map = {category.get("id"): category for category in source.get("categories", []) if isinstance(category, dict) and category.get("id")}
    if set(category_map) != {category["id"] for category in CATEGORIES}:
        failures.append("三類消息不完整或出現未知分類")
    seen: set[str] = set()
    counts: dict[str, int] = {}
    category_statuses: dict[str, str] = {}
    for config in CATEGORIES:
        category = category_map.get(config["id"], {})
        items = category.get("items", [])
        counts[config["id"]] = len(items)
        category_statuses[config["id"]] = str(category.get("status") or "")
        allowed = set(config["accounts"])
        scores: list[float] = []
        if category.get("source_accounts") != sorted(allowed):
            failures.append(f"{config['title']}：策展帳號清單與程式設定不一致")
        if len(items) > MAX_ITEMS_PER_CATEGORY:
            failures.append(f"{config['title']}：前端資料超過每類 {MAX_ITEMS_PER_CATEGORY} 則上限")
        for item in items:
            post_id = str(item.get("id") or "")
            username = str(item.get("username") or "").lower()
            created = parse_time(item.get("created_at"))
            if not post_id or post_id in seen:
                failures.append(f"{config['title']}：貼文 ID 缺漏或跨分類重複")
            seen.add(post_id)
            if username not in allowed:
                failures.append(f"{config['title']}：非策展來源 @{username}")
            if item.get("url") != f"https://x.com/{username}/status/{post_id}":
                failures.append(f"{config['title']}：貼文 URL 與 ID/作者不一致")
            if created is None or generated is None or created < generated - timedelta(hours=WINDOW_HOURS, minutes=5) or created > generated + timedelta(minutes=5):
                failures.append(f"{config['title']}：貼文日期不在 {WINDOW_HOURS} 小時視窗內或位於未來")
            if not str(item.get("text") or "").strip() or not str(item.get("why_it_matters") or "").strip():
                failures.append(f"{config['title']}：原文或解讀缺漏")
            metrics = item.get("public_metrics")
            if not isinstance(metrics, dict) or any(type(metrics.get(field)) is not int or metrics[field] < 0 for field in ("likes", "reposts", "quotes", "replies")):
                failures.append(f"{config['title']}：公開互動數缺漏、非整數或小於零")
            if item.get("decision_use") != "context_only_not_execution_gate":
                failures.append(f"{config['title']}：決策用途契約錯配")
            score = item.get("ranking_score_0_100")
            if not isinstance(score, (int, float)) or not 0 <= score <= 100:
                failures.append(f"{config['title']}：綜合排序分數超出範圍")
            else:
                scores.append(float(score))
                if created is not None and generated is not None and username in allowed:
                    expected_score, expected_components = expected_ranking(config, item, generated, created)
                    if abs(float(score) - expected_score) > 0.11:
                        failures.append(f"{config['title']}：綜合排序分數無法由原始欄位重算")
                    components = item.get("ranking_components_0_100")
                    if components != expected_components:
                        failures.append(f"{config['title']}：排序分數分量與獨立重算不一致")
        if scores != sorted(scores, reverse=True):
            failures.append(f"{config['title']}：貼文未依相關性排序")
    source_status = quality.get("status")
    if source_status == "unconfigured":
        if quality.get("authentication") != "missing_secret":
            failures.append("未設定狀態的 authentication 標記錯誤")
        if any(counts.values()):
            failures.append("未設定 API 時不得保留看似最新的貼文")
        if set(category_statuses.values()) != {"unconfigured"}:
            failures.append("未設定 API 時三個分類都必須標記 unconfigured")
        degradations.append("X_BEARER_TOKEN 尚未設定")
    elif source_status == "fail":
        if quality.get("authentication") != "app_only_bearer_secret":
            failures.append("已呼叫 API 時的 authentication 標記錯誤")
        failures.extend(str(item) for item in quality.get("failures", []) or ["X API 收集失敗"])
        if "fail" not in category_statuses.values():
            failures.append("整體 fail 時至少一個分類必須標記 fail")
    elif source_status == "degraded":
        if quality.get("authentication") != "app_only_bearer_secret":
            failures.append("已呼叫 API 時的 authentication 標記錯誤")
        degradations.extend(str(item) for item in quality.get("degradations", []))
        if any(value not in {"pass", "degraded"} for value in category_statuses.values()):
            failures.append("整體 degraded 時分類只能是 pass 或 degraded")
        if not quality.get("degradations"):
            failures.append("整體 degraded 時必須列出降級原因")
    elif source_status == "pass":
        if quality.get("authentication") != "app_only_bearer_secret":
            failures.append("已呼叫 API 時的 authentication 標記錯誤")
        if any(count < 3 for count in counts.values()) or set(category_statuses.values()) != {"pass"}:
            failures.append("整體 pass 必須三類各至少 3 則且分類狀態全為 pass")
        if quality.get("failures") or quality.get("degradations"):
            failures.append("整體 pass 不得同時帶有失敗或降級原因")
    else:
        failures.append("未知的 X 情報品質狀態")
    summary = source.get("summary", {})
    if summary.get("categories") != len(CATEGORIES) or summary.get("posts") != sum(counts.values()):
        failures.append("X 情報摘要計數與分類內容不一致")
    unique_sources = len({item.get("username") for category in source.get("categories", []) for item in category.get("items", [])})
    if summary.get("unique_sources") != unique_sources:
        failures.append("X 情報來源數與貼文內容不一致")
    history_items: list[dict[str, Any]] = []
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8-sig"))
        if history.get("schema") != 1 or history.get("last_attempt_at") != source.get("generated_at"):
            failures.append("X 歷史資料 schema 或最後嘗試時間與目前產物不一致")
        if history.get("quality", {}).get("status") != source_status:
            failures.append("X 歷史資料品質狀態與目前產物不一致")
        history_items = history.get("items", [])
        if not isinstance(history_items, list) or len(history_items) > 2000:
            failures.append("X 歷史資料型別錯誤或超過 2000 則上限")
            history_items = []
        history_ids = [str(item.get("id") or "") for item in history_items]
        if not all(history_ids) or len(history_ids) != len(set(history_ids)):
            failures.append("X 歷史資料包含空白或重複貼文 ID")
        for item in history_items:
            created = parse_time(item.get("created_at"))
            if created is None or created < now - timedelta(days=181) or created > now + timedelta(minutes=5):
                failures.append("X 歷史資料包含日期缺漏、超過保留期或位於未來的貼文")
                break
            if item.get("decision_use") != "context_only_not_execution_gate":
                failures.append("X 歷史資料包含可進入交易閘門的錯誤用途標記")
                break
        successful_fetch = source_status in {"pass", "degraded"} and quality.get("authentication") == "app_only_bearer_secret"
        if successful_fetch and history.get("last_successful_fetch_at") != source.get("generated_at"):
            failures.append("X 歷史資料未記錄本批次成功抓取時間")
        if successful_fetch and history.get("updated_at") != source.get("generated_at"):
            failures.append("成功抓取後 X 歷史內容更新時間未前進")
        if source_status == "unconfigured" and history.get("last_successful_fetch_at") == source.get("generated_at"):
            failures.append("未設定 Token 的嘗試不得偽裝成成功抓取")
        if source_status == "unconfigured" and history.get("updated_at") == source.get("generated_at"):
            failures.append("未設定 Token 的嘗試不得刷新歷史內容更新時間")
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"X 歷史資料無法讀取：{error}")
    status = "fail" if failures else "degraded" if degradations else "pass"
    report = {
        "schema": 1,
        "verified_at": now_iso(),
        "source_generated_at": source.get("generated_at"),
        "status": status,
        "failures": list(dict.fromkeys(failures)),
        "degradations": list(dict.fromkeys(degradations)),
        "category_counts": counts,
        "history_items": len(history_items),
        "method": [
            "只允許各分類策展帳號名單。",
            "貼文 ID、作者、URL、48 小時視窗、分類狀態與決策用途逐項驗證。",
            "由來源權重、關鍵字、時效與互動欄位獨立重算綜合排序分數。",
            "X 情報永遠只作背景，不進交易硬閘門。",
        ],
    }
    write_json(report)
    print(json.dumps({"status": status, "failures": len(report["failures"]), "degradations": len(report["degradations"]), "counts": counts}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
