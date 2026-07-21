#!/usr/bin/env python3
"""Generate three daily research extensions from verified data and compiled Wiki context."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
VERIFY_PATH = DATA_DIR / "agent_verification_report.json"
ANALYTICS_PATH = DATA_DIR / "institutional_analytics.json"
KNOWLEDGE_PATH = DATA_DIR / "knowledge_context.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
EXTENSIONS_PATH = DATA_DIR / "daily_extensions.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def decision_label(snapshot: dict[str, Any]) -> str:
    labels = {
        "BLOCK_LEVERAGED_ADD": "禁止 MSTR 小倉合約加碼",
        "WATCH_ONLY_NO_CHASE": "只列觀察，不自動追價",
    }
    decision = snapshot.get("decision", {})
    return labels.get(decision.get("state_code"), str(decision.get("state", "資料不足")))


def confidence_from_verification(verification: dict[str, Any]) -> str:
    return {"pass": "中高", "degraded": "中低", "fail": "低"}.get(verification.get("status"), "低")


def usable_knowledge_pages(knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    return [page for page in knowledge.get("pages", []) if page.get("analysis_use") == "context_only"]


def knowledge_index(knowledge: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(page.get("slug")): page for page in usable_knowledge_pages(knowledge)}


def wiki_inputs(knowledge: dict[str, Any], referenced_slugs: list[str]) -> list[str]:
    pages = knowledge_index(knowledge)
    output: list[str] = []
    for slug in referenced_slugs:
        page = pages.get(slug)
        if not page:
            continue
        summary = page.get("summary") or "無可用摘要"
        output.append(
            f"{page.get('title', slug)}｜{summary}｜信心 {page.get('confidence', 'unverified')}｜"
            f"驗證距今 {page.get('stale_days', '未知')} 天｜{page.get('source_path')}"
        )
    return output


def lens_summary(signal: dict[str, Any]) -> str:
    lenses = signal.get("lenses", [])
    if not lenses:
        return "多維資料不足"
    return "；".join(
        f"{item.get('name')}={item.get('value')}（{item.get('read')}）"
        for item in lenses[:4]
    )


def signal_to_viewpoint(
    signal: dict[str, Any],
    *,
    title_prefix: str,
    lens_name: str,
    confidence: str,
) -> dict[str, Any]:
    refs = [ref.get("slug") for ref in signal.get("wiki_refs", []) if ref.get("slug")]
    source_paths = [ref.get("path") for ref in signal.get("source_refs", []) if ref.get("path")]
    return {
        "title": f"{title_prefix}：{signal.get('title', '資料不足')}",
        "lens": lens_name,
        "claim": f"{signal.get('key_number', '資料不足')}｜{signal.get('plain_read', '資料不足')}",
        "so_what": signal.get("next_trigger", "等待可驗證觸發"),
        "study_note": f"多維檢查：{lens_summary(signal)}",
        "confidence": signal.get("confidence") or confidence,
        "resonance": signal.get("resonance_status", "資料不足"),
        "wiki_refs": refs,
        "source_refs": source_paths,
    }


def thesis_study_note(market: dict[str, Any]) -> str | None:
    thesis = market.get("btc_thesis", {})
    quality = thesis.get("quality", {})
    if quality.get("coverage_status") != "complete" or quality.get("status") not in {"pass", "degraded"}:
        return None
    gold = thesis.get("gold_monetization", {})
    credit = thesis.get("digital_dollar_competition", {})
    company = thesis.get("public_company_adoption", {})
    security = thesis.get("security_consensus", {})
    sovereign = thesis.get("sovereign_credit_competition", {})
    values = {
        "黃金貨幣化": gold.get("btc_to_gold_market_value_ratio"),
        "穩定幣30日": credit.get("stablecoin_supply_30d_change"),
        "公開公司持幣": company.get("share_of_btc_supply"),
        "算力保留": security.get("hashrate_vs_90d_high"),
        "債務GDP": sovereign.get("us_federal_debt_to_gdp_pct"),
        "10年實質利率": sovereign.get("us_10y_real_yield_pct"),
    }
    if any(value is None for value in values.values()):
        return None
    return (
        f"長期論證：BTC/黃金代理總值={values['黃金貨幣化']:.1%}；"
        f"美元穩定幣30日={values['穩定幣30日']:+.1%}；公開公司樣本持有供給={values['公開公司持幣']:.1%}；"
        f"算力仍為90日高點={values['算力保留']:.1%}；美國債務/GDP={values['債務GDP']:.1f}%、"
        f"10年實質利率={values['10年實質利率']:.2f}%。這些只檢驗結構假說，不放行短線交易。"
    )


def build_viewpoints(analytics: dict[str, Any], verification: dict[str, Any], market: dict[str, Any]) -> list[dict[str, Any]]:
    brief = analytics.get("decision_brief", {})
    consensus = brief.get("consensus_signals", [])
    exclusive = brief.get("exclusive_signals", [])
    confidence = confidence_from_verification(verification)
    candidates = [
        (consensus[0] if consensus else {}, "BTC", "技術＋鏈上估值＋情緒＋週期"),
        (exclusive[0] if exclusive else {}, "MSTR", "普通股估值＋資本結構＋相對動能"),
        (exclusive[1] if len(exclusive) > 1 else {}, "BMNR", "gross-assets＋股數含幣量＋質押流動性"),
    ]
    viewpoints = [
        signal_to_viewpoint(signal, title_prefix=prefix, lens_name=lens_name, confidence=confidence)
        for signal, prefix, lens_name in candidates
        if signal
    ]
    structural_status = verification.get("structural_context_quality", {}).get("status")
    structural_note = thesis_study_note(market) if structural_status in {"pass", "degraded"} else None
    if viewpoints and structural_note:
        viewpoints[0]["study_note"] = viewpoints[0].get("study_note", "") + "｜" + structural_note
        viewpoints[0].setdefault("wiki_refs", []).append("btc-neutral-anchor")
        viewpoints[0].setdefault("source_refs", []).append("data/daily/market_universe.json")
    return viewpoints


def tomorrow_watch(snapshot: dict[str, Any], analytics: dict[str, Any]) -> dict[str, Any]:
    brief = analytics.get("decision_brief", {})
    signals = brief.get("consensus_signals", []) + brief.get("exclusive_signals", [])
    watch_items = [item.get("next_trigger") for item in signals if item.get("next_trigger")][:3]
    return {
        "date": (date.fromisoformat(snapshot["date"]) + timedelta(days=1)).isoformat(),
        "type": "tomorrow_watch",
        "title": "明日只檢查三個可否證觸發",
        "watch_items": watch_items or ["分析摘要缺漏；維持全部交易封鎖"],
    }


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    verification = load_json(VERIFY_PATH)
    analytics = load_json(ANALYTICS_PATH)
    knowledge = load_json(KNOWLEDGE_PATH, {"status": "missing", "pages": [], "quality": {}})
    market = load_json(MARKET_PATH, {})
    existing = load_json(EXTENSIONS_PATH, {"schema": 2, "items": [], "archive": []})
    snapshot_generated_at = snapshot.get("generated_at")
    if not snapshot_generated_at:
        raise SystemExit("Daily snapshot lineage is missing")
    if verification.get("snapshot_generated_at") != snapshot_generated_at:
        raise SystemExit("Verifier is not bound to the current daily snapshot")
    if analytics.get("snapshot_generated_at") != snapshot_generated_at:
        raise SystemExit("Institutional analytics is not bound to the current daily snapshot")
    viewpoints = build_viewpoints(analytics, verification, market)
    referenced_slugs = list(dict.fromkeys(slug for view in viewpoints for slug in view.get("wiki_refs", [])))
    today_item = {
        "date": snapshot["date"],
        "generated_at": now_iso(),
        "snapshot_generated_at": snapshot_generated_at,
        "type": "daily_extension",
        "analysis_mode": "deterministic_with_governed_knowledge_constraints",
        "verification_status": verification.get("status"),
        "verification_warnings": verification.get("warnings", []),
        "decision_state": decision_label(snapshot),
        "decision_state_code": snapshot.get("decision", {}).get("state_code"),
        "viewpoints": viewpoints,
        "wiki_study_inputs": wiki_inputs(knowledge, referenced_slugs),
        "knowledge_audit": {
            "status": knowledge.get("status", "missing"),
            "active_pages": knowledge.get("quality", {}).get("active_pages", 0),
            "context_only_pages": knowledge.get("quality", {}).get("context_only_pages", 0),
            "excluded_pages": knowledge.get("quality", {}).get("excluded_pages", 0),
            "usage_policy": knowledge.get("usage_policy", {}),
        },
    }

    prior_items = existing.get("items", []) + existing.get("archive", [])
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in prior_items + [today_item]:
        by_key[(str(item.get("date")), str(item.get("type")))] = item
    all_items = sorted(by_key.values(), key=lambda item: item.get("date", ""))
    yesterday = date.fromisoformat(snapshot["date"]) - timedelta(days=1)
    visible_dates = {yesterday.isoformat(), snapshot["date"]}
    visible = [item for item in all_items if item.get("date") in visible_dates and item.get("type") == "daily_extension"]
    archive = [item for item in all_items if item not in visible]
    output = {
        "schema": 2,
        "updated_at": now_iso(),
        "current_date": snapshot["date"],
        "snapshot_generated_at": snapshot_generated_at,
        "visible_window": visible + [tomorrow_watch(snapshot, analytics)],
        "items": visible,
        "archive": archive[-730:],
        "knowledge_context": {
            "status": knowledge.get("status", "missing"),
            "as_of_date": knowledge.get("as_of_date"),
            "generated_at": knowledge.get("generated_at"),
        },
    }
    write_json(EXTENSIONS_PATH, output)
    print(json.dumps({"visible": len(output["visible_window"]), "archive": len(output["archive"]), "knowledge": knowledge.get("status", "missing")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
