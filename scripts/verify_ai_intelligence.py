#!/usr/bin/env python3
"""Independently verify multi-source AI intelligence and ranking invariants."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from collect_ai_intelligence import (
    CATEGORIES,
    EDITORIAL_THEMES,
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


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def item_integrity_payload(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "url",
        "created_at",
        "title",
        "text",
        "source_key",
        "category_id",
        "source_label",
        "source_type",
        "source_tier",
        "why_it_matters",
        "next_action",
        "decision_use",
    )
    return {key: item.get(key) for key in fields}


def editorial_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "run_hash"}


def expected_brief_history_payload(brief: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "category_id": brief.get("category_id"),
        "theme_id": brief.get("theme_id"),
        "headline": brief.get("headline"),
        "standfirst": brief.get("standfirst"),
        "common_interpretation": brief.get("common_interpretation"),
        "variant_view": brief.get("variant_view"),
        "second_order_effect": brief.get("second_order_effect"),
        "practical_readthrough": brief.get("practical_readthrough"),
        "falsifier": brief.get("falsifier"),
        "evidence_refs": [
            {"item_id": item.get("item_id"), "item_integrity_hash": item.get("item_integrity_hash")}
            for item in brief.get("evidence", [])
        ],
    }
    return {**payload, "brief_hash": canonical_hash(payload)}


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


def editorial_term_matches(text: str, term: str) -> bool:
    normalized_text = re.sub(r"[-_]", " ", text.lower())
    normalized_term = re.sub(r"[-_]", " ", term.lower()).strip()
    escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
    plural = "" if normalized_term.endswith("s") else "s?"
    return re.search(rf"(?<![a-z0-9]){escaped}{plural}(?![a-z0-9])", normalized_text) is not None


def expected_editorial_theme(category_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    def theme_score(theme: dict[str, Any]) -> tuple[int, int, float]:
        item_hits = [
            (item, sum(editorial_term_matches(str(item.get("text") or ""), term) for term in theme["terms"]))
            for item in items
        ]
        supporting = [(item, hits) for item, hits in item_hits if hits > 0]
        return (
            len({str(item.get("source_key") or "") for item, _ in supporting}),
            sum(hits for _, hits in supporting),
            sum(float(item.get("ranking_score_0_100") or 0) for item, _ in supporting),
        )

    return max(
        EDITORIAL_THEMES[category_id],
        key=theme_score,
    )


def expected_lede(evidence: list[dict[str, Any]]) -> str:
    if len(evidence) >= 2:
        return (
            f"今日可回查的共同訊號來自 {evidence[0]['source_label']} 的「{evidence[0]['title']}」，"
            f"以及 {evidence[1]['source_label']} 的「{evidence[1]['title']}」。"
            "它們只證明相關主題同時出現，不代表來源認同本站假說。"
        )
    if evidence:
        return f"今日只有 {evidence[0]['source_label']} 的「{evidence[0]['title']}」命中此主題；不宣稱形成跨來源訊號。"
    return "今日沒有足夠且可追溯的證據形成編輯觀點。"


def expected_novelty(brief: dict[str, Any], previous: dict[str, Any] | None) -> tuple[str, str]:
    if previous is None:
        return "baseline", "首次建立可回查基準；明日起只在主軸或證據真的改變時更新敘事。"
    if previous.get("theme_id") != brief.get("theme_id"):
        return "changed", f"主軸由「{previous.get('headline') or previous.get('theme_id')}」轉為「{brief.get('headline')}」。"
    previous_refs = {
        str(item.get("item_id")): str(item.get("item_integrity_hash") or "") or None
        for item in previous.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    if not previous_refs:
        previous_refs = {str(item): None for item in previous.get("evidence_ids", [])}
    current_refs = {
        str(item.get("item_id")): str(item.get("item_integrity_hash") or "")
        for item in brief.get("evidence", [])
    }
    current_ids, previous_ids = set(current_refs), set(previous_refs)
    added_ids, removed_ids = current_ids - previous_ids, previous_ids - current_ids
    modified_ids = {
        item_id for item_id in current_ids & previous_ids
        if previous_refs[item_id] is not None and previous_refs[item_id] != current_refs[item_id]
    }
    new_evidence = [item for item in brief.get("evidence", []) if item.get("item_id") in added_ids]
    if modified_ids:
        return "revised", f"主軸未變，但 {len(modified_ids)} 則同一來源內容或 metadata 改變；視為修訂，不視為新證據。"
    if added_ids and removed_ids:
        labels = "、".join(dict.fromkeys(str(item.get("source_label") or "") for item in new_evidence))
        return "refreshed", f"主軸未變；替換 {len(removed_ids)} 則、加入 {len(added_ids)} 則主題訊號，新增來源為 {labels}。"
    if added_ids:
        labels = "、".join(dict.fromkeys(str(item.get("source_label") or "") for item in new_evidence))
        return "reinforced", f"主軸未變；新增 {len(added_ids)} 則主題訊號，來自 {labels}。"
    if removed_ids:
        return "weakened", f"主軸未變，但少了 {len(removed_ids)} 則先前主題訊號；證據廣度下降。"
    return "unchanged", "主軸與核心證據未變；今日不製造新故事，保留上一個不同日期的判斷。"


def main() -> int:
    source = json.loads(OUTPUT_PATH.read_text(encoding="utf-8-sig"))
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8-sig"))
    failures: list[str] = []
    degradations: list[str] = []
    generated = parse_time(source.get("generated_at"))
    current = datetime.now(timezone.utc)
    if source.get("schema") != 3 or generated is None:
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
            if item.get("integrity_hash") != canonical_hash(item_integrity_payload(item)):
                failures.append(f"{category_config['title']}消息 integrity_hash 無法重算")
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

    editorial = source.get("editorial_digest", {})
    briefs = editorial.get("briefs", [])
    brief_map = {brief.get("category_id"): brief for brief in briefs if isinstance(brief, dict)}
    if set(brief_map) != set(category_configs) or len(briefs) != len(category_configs):
        failures.append("機構觀點必須正好覆蓋三個 AI 分類")
    current_date = str(source.get("generated_at") or "")[:10]
    previous_runs = [
        run for run in history.get("editorial_runs", [])
        if isinstance(run, dict) and str(run.get("date") or "") < current_date
    ]
    previous_run = max(previous_runs, key=lambda run: str(run.get("generated_at") or ""), default=None)
    previous_briefs = {
        str(brief.get("category_id")): brief
        for brief in (previous_run or {}).get("briefs", [])
        if isinstance(brief, dict)
    }
    for category_id, category_config in category_configs.items():
        category = category_map.get(category_id, {})
        items = category.get("items", [])
        local_items = {str(item.get("id") or ""): item for item in items}
        brief = brief_map.get(category_id, {})
        theme = expected_editorial_theme(category_id, items)
        if brief.get("id") != f"brief-{category_id}" or brief.get("category_title") != category_config["title"]:
            failures.append(f"{category_config['title']}機構觀點識別或分類錯配")
        if brief.get("theme_id") != theme["id"]:
            failures.append(f"{category_config['title']}機構觀點主題無法由當日語料重算")
        for field in (
            "kicker",
            "headline",
            "standfirst",
            "common_interpretation",
            "variant_view",
            "second_order_effect",
            "practical_readthrough",
            "falsifier",
        ):
            if brief.get(field) != theme[field]:
                failures.append(f"{category_config['title']}機構觀點欄位 {field} 偏離受控論證")
        evidence = brief.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 3:
            failures.append(f"{category_config['title']}機構觀點證據數量錯誤")
            evidence = []
        evidence_sources: set[str] = set()
        expected_evidence = []
        expected_sources: set[str] = set()
        ranked_items = sorted(
            items,
            key=lambda item: (
                sum(editorial_term_matches(str(item.get("text") or ""), term) for term in theme["terms"]),
                float(item.get("ranking_score_0_100") or 0),
            ),
            reverse=True,
        )
        for item in ranked_items:
            theme_hits = sum(editorial_term_matches(str(item.get("text") or ""), term) for term in theme["terms"])
            source_key = str(item.get("source_key") or "")
            if theme_hits == 0 or source_key in expected_sources:
                continue
            expected_evidence.append(item)
            expected_sources.add(source_key)
            if len(expected_evidence) == 3:
                break
        if [str(value.get("item_id")) for value in evidence] != [str(value.get("id")) for value in expected_evidence]:
            failures.append(f"{category_config['title']}機構觀點未選用最強且來源分散的主題證據")
        for position, citation in enumerate(evidence):
            item = local_items.get(str(citation.get("item_id") or ""))
            if item is None:
                failures.append(f"{category_config['title']}機構觀點引用不存在的消息")
                continue
            for field, item_field in (
                ("title", "title"),
                ("source_key", "source_key"),
                ("source_label", "source_label"),
                ("source_type", "source_type"),
                ("created_at", "created_at"),
                ("url", "url"),
                ("ranking_score_0_100", "ranking_score_0_100"),
            ):
                if citation.get(field) != item.get(item_field):
                    failures.append(f"{category_config['title']}機構觀點引用欄位 {field} 與原消息不一致")
            matched_terms = [term for term in theme["terms"] if editorial_term_matches(str(item.get("text") or ""), term)]
            excerpt = str(item.get("text") or "")
            excerpt = excerpt if len(excerpt) <= 360 else excerpt[:359].rstrip() + "…"
            if citation.get("source_excerpt") != excerpt or citation.get("matched_terms") != matched_terms:
                failures.append(f"{category_config['title']}機構觀點來源摘錄或主題命中詞無法重算")
            if citation.get("item_integrity_hash") != item.get("integrity_hash"):
                failures.append(f"{category_config['title']}機構觀點引用未綁定消息完整性雜湊")
            if citation.get("relationship") != "topic_context_not_endorsement":
                failures.append(f"{category_config['title']}機構觀點未標示來源只提供主題背景")
            source_key = str(citation.get("source_key") or "")
            if source_key in evidence_sources:
                failures.append(f"{category_config['title']}機構觀點重複引用同一來源")
            evidence_sources.add(source_key)
        expected_status = "pass" if len(evidence_sources) >= 2 else "degraded" if evidence_sources else "fail"
        if brief.get("status") != expected_status or brief.get("evidence_source_count") != len(evidence_sources):
            failures.append(f"{category_config['title']}機構觀點狀態與來源多樣性不一致")
        matched_term_count = sum(len(item.get("matched_terms", [])) for item in evidence)
        if brief.get("matched_term_count") != matched_term_count:
            failures.append(f"{category_config['title']}機構觀點命中詞計數無法獨立重算")
        if brief.get("lede") != expected_lede(evidence):
            failures.append(f"{category_config['title']}機構觀點導言與引用不一致")
        if brief.get("editorial_scope") != "editorial_hypothesis_not_source_claim":
            failures.append(f"{category_config['title']}機構觀點未標示編輯假說與來源事實邊界")
        novelty_status, what_changed = expected_novelty(brief, previous_briefs.get(category_id))
        if brief.get("novelty_status") != novelty_status or brief.get("what_changed") != what_changed:
            failures.append(f"{category_config['title']}機構觀點未正確比較上一個不同日期")

    valid_briefs = [brief for brief in briefs if brief.get("status") in {"pass", "degraded"}]
    expected_lead = max(
        valid_briefs,
        key=lambda brief: (
            brief.get("status") == "pass",
            int(brief.get("evidence_source_count") or 0),
            int(brief.get("matched_term_count") or 0),
            sum(float(item.get("ranking_score_0_100") or 0) for item in brief.get("evidence", [])),
        ),
    )["id"] if valid_briefs else None
    expected_editorial_status = "pass" if len(briefs) == len(category_configs) and all(brief.get("status") == "pass" for brief in briefs) else "degraded" if valid_briefs else "fail"
    if editorial.get("lead_brief_id") != expected_lead or editorial.get("status") != expected_editorial_status:
        failures.append("機構觀點主文或整體狀態無法由三篇短評重算")
    if editorial.get("policy_hash") != canonical_hash(EDITORIAL_THEMES):
        failures.append("機構觀點 policy_hash 與受控編輯規則不一致")
    if editorial.get("method") != "deterministic topic-context synthesis; source excerpts do not endorse editorial hypotheses; every hypothesis includes a falsifier":
        failures.append("機構觀點方法揭露錯配")

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
    if summary.get("editorial_briefs") != len(briefs) or summary.get("editorial_pass") != sum(brief.get("status") == "pass" for brief in briefs):
        failures.append("機構觀點摘要計數與內容不一致")
    source_status = quality.get("status")
    if source_status == "pass":
        if any(count < 3 for count in category_counts.values()) or quality.get("failures") or quality.get("degradations"):
            failures.append("AI 情報 pass 與分類筆數或品質原因矛盾")
    elif source_status == "degraded":
        if not quality.get("degradations") or any(count == 0 for count in category_counts.values()):
            failures.append("AI 情報 degraded 必須有原因且所有分類皆有內容")
        degradations.extend(str(item) for item in quality.get("degradations", []))
    elif source_status == "fail":
        failures.extend(str(item) for item in quality.get("failures", []) or ["AI 多來源收集失敗"])
    else:
        failures.append("AI 情報品質狀態未知")

    if history.get("schema") != 2 or history.get("integrity_contract") != "source_content_v1" or history.get("last_attempt_at") != source.get("generated_at") or history.get("quality", {}).get("status") != source_status:
        failures.append("AI 情報歷史資料未綁定目前批次")
    history_items = history.get("items", [])
    if not isinstance(history_items, list) or len(history_items) > 50000:
        failures.append("AI 情報歷史資料型別或保留上限錯誤")
        history_items = []
    history_item_map: dict[tuple[str, str], dict[str, Any]] = {}
    for item in history_items:
        if not isinstance(item, dict) or not item.get("id"):
            failures.append("AI 情報歷史 items 含非物件或空白 ID")
            continue
        item_id = str(item["id"])
        item_hash = str(item.get("integrity_hash") or "")
        key = (item_id, item_hash)
        if key in history_item_map:
            failures.append("AI 情報歷史 items 含重複 ID 與 integrity_hash")
        history_item_map[key] = item
        if item_hash != canonical_hash(item_integrity_payload(item)):
            failures.append(f"AI 情報歷史消息 {item_id} integrity_hash 無法重算")
    editorial_runs = history.get("editorial_runs", [])
    if not isinstance(editorial_runs, list) or len(editorial_runs) > 5000:
        failures.append("AI 機構觀點歷史資料型別或保留上限錯誤")
        editorial_runs = []
    else:
        previous_hash: str | None = None
        last_same_day: dict[str, dict[str, Any]] = {}
        revisions: dict[str, int] = {}
        for run in editorial_runs:
            if not isinstance(run, dict):
                failures.append("AI 機構觀點歷史含非物件資料")
                continue
            date = str(run.get("date") or "")
            generated_at = str(run.get("generated_at") or "")
            expected_revision = revisions.get(date, 0) + 1
            revisions[date] = expected_revision
            expected_supersedes = last_same_day.get(date, {}).get("generated_at")
            if run.get("schema") != 1 or date != generated_at[:10] or run.get("revision") != expected_revision:
                failures.append("AI 機構觀點歷史日期、schema 或 revision 錯誤")
            if run.get("supersedes_generated_at") != expected_supersedes or run.get("previous_run_hash") != previous_hash:
                failures.append("AI 機構觀點歷史 supersedes 或雜湊鏈斷裂")
            if run.get("run_hash") != canonical_hash(editorial_run_payload(run)):
                failures.append("AI 機構觀點歷史 run_hash 無法重算")
            run_briefs = run.get("briefs", [])
            if not isinstance(run_briefs, list) or len(run_briefs) != len(category_configs):
                failures.append("AI 機構觀點歷史短評數量錯誤")
                run_briefs = []
            for compact in run_briefs:
                if not isinstance(compact, dict):
                    failures.append("AI 機構觀點歷史短評含非物件資料")
                    continue
                if compact.get("brief_hash") != canonical_hash({key: value for key, value in compact.items() if key != "brief_hash"}):
                    failures.append("AI 機構觀點歷史 brief_hash 無法重算")
                refs = compact.get("evidence_refs", [])
                if not isinstance(refs, list):
                    failures.append("AI 機構觀點歷史 evidence_refs 型別錯誤")
                    continue
                for ref in refs:
                    key = (str(ref.get("item_id") or ""), str(ref.get("item_integrity_hash") or "")) if isinstance(ref, dict) else ("", "")
                    item = history_item_map.get(key)
                    if item is None:
                        failures.append("AI 機構觀點歷史引用無法綁定原始消息雜湊")
            previous_hash = run.get("run_hash")
            last_same_day[date] = run

        current_runs = [run for run in editorial_runs if isinstance(run, dict) and run.get("date") == current_date]
        if not current_runs:
            failures.append("AI 機構觀點歷史缺少當日 revision")
        else:
            current_run = current_runs[-1]
            expected_compact = [expected_brief_history_payload(brief) for brief in briefs]
            if current_run.get("generated_at") != source.get("generated_at") or current_run.get("lead_brief_id") != editorial.get("lead_brief_id") or current_run.get("briefs") != expected_compact:
                failures.append("AI 機構觀點最新 revision 與目前主文不一致")

    status = "fail" if failures else "degraded" if degradations else "pass"
    report = {
        "schema": 3,
        "verified_at": now_iso(),
        "source_generated_at": source.get("generated_at"),
        "status": status,
        "failures": list(dict.fromkeys(failures)),
        "degradations": list(dict.fromkeys(degradations)),
        "category_counts": category_counts,
        "successful_sources": successful_sources,
        "daily_actions": len(actions),
        "editorial_briefs": len(briefs),
        "editorial_status": editorial.get("status"),
        "history_items": len(history_items) if isinstance(history_items, list) else 0,
        "editorial_history_runs": len(editorial_runs) if isinstance(editorial_runs, list) else 0,
        "method": [
            "逐一核對官方 feed、GitHub Releases 與 arXiv 來源清單。",
            "獨立重算來源品質、關鍵字相關性與時效排序分數。",
            "每日三項行動必須綁定三個不同來源，且不進交易硬閘門。",
            "機構觀點逐篇重算主題命中、來源摘錄、編輯假說邊界與反證欄位。",
            "機構觀點必須和上一個不同日期比較，且保存唯一當日版本。",
        ],
    }
    write_json(report)
    print(json.dumps({"status": status, "failures": len(report["failures"]), "degradations": len(report["degradations"]), "counts": category_counts, "sources": successful_sources, "actions": len(actions)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
