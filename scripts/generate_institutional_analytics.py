#!/usr/bin/env python3
"""Institutional analytics layer for the personal Bloomberg dashboard.

This layer converts verified daily data into professional-grade analysis:
quality, attribution, scenario sensitivity, and a risk stack.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
DATABASE_PATH = DATA_DIR / "database.json"
VERIFY_PATH = DATA_DIR / "agent_verification_report.json"
ANALYTICS_PATH = DATA_DIR / "institutional_analytics.json"
LOGIC_AUDIT_PATH = DATA_DIR / "logic_audit.json"


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def pct_change(now: float | None, prev: float | None) -> float | None:
    if now is None or prev in (None, 0):
        return None
    return now / prev - 1


def delta(now: float | None, prev: float | None) -> float | None:
    if now is None or prev is None:
        return None
    return now - prev


def fmt(value: Any, digits: int = 2) -> str:
    value = f(value)
    return "N/A" if value is None else f"{value:.{digits}f}"


def pct(value: Any) -> str:
    value = f(value)
    return "N/A" if value is None else f"{value * 100:.1f}%"


def latest_previous(database: dict[str, Any], date: str) -> dict[str, Any] | None:
    snapshots = [item for item in database.get("snapshots", []) if item.get("date") != date]
    snapshots.sort(key=lambda item: item.get("date", ""))
    return snapshots[-1] if snapshots else None


def confidence_from_verification(verification: dict[str, Any], provenance: dict[str, Any]) -> str:
    score = quality_score(verification, provenance)
    if score >= 85:
        return "high"
    if score >= 70:
        return "medium"
    if score >= 50:
        return "medium-low"
    return "low"


def quality_score(verification: dict[str, Any], provenance: dict[str, Any]) -> int:
    status = verification.get("status")
    if status == "fail":
        return 20
    score = 100
    if status == "degraded":
        score -= 15
    score -= min(len(verification.get("degradations", [])) * 4, 24)
    score -= min(len(verification.get("warnings", [])) * 2, 10)
    if provenance.get("status") != "automated":
        score -= 12
    return max(score, 0)


def mstr_decomposition(snapshot: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    prices = snapshot["metrics"]["prices"]
    metrics = snapshot["metrics"]["mstr_metrics"]
    prev_prices = previous.get("metrics", {}).get("prices", {}) if previous else {}
    prev_metrics = previous.get("metrics", {}).get("mstr_metrics", {}) if previous else {}
    btc_ret = pct_change(f(prices.get("btc_usd")), f(prev_prices.get("btc_usd")))
    mstr_ret = pct_change(f(prices.get("mstr_usd")), f(prev_prices.get("mstr_usd")))
    price_to_nav_delta = delta(f(metrics.get("equity_mnav")), f(prev_metrics.get("equity_mnav")))
    strc_delta = delta(f(metrics.get("strc_discount")), f(prev_metrics.get("strc_discount")))
    beta_gap = None if btc_ret is None or mstr_ret is None else mstr_ret - btc_ret
    drivers = []
    if beta_gap is not None:
        drivers.append({"driver": "MSTR_vs_BTC", "value": beta_gap, "read": "MSTR underperformed BTC; discount/capital-structure pressure persists" if beta_gap < 0 else "MSTR outperformed BTC; verify it is not short-term reflexivity"})
    if price_to_nav_delta is not None:
        drivers.append({"driver": "普通股市值／普通股淨值變化", "value": price_to_nav_delta, "read": "倍率下降，估值相對轉便宜" if price_to_nav_delta < 0 else "倍率上升，溢價擴大或淨值惡化"})
    if strc_delta is not None:
        drivers.append({"driver": "STRC 優先股折價信任票變化", "value": strc_delta, "read": "STRC 折價擴大，市場信任變差" if strc_delta > 0 else "STRC 折價收斂，市場信任改善"})
    return {
        "btc_return_1d": btc_ret,
        "mstr_return_1d": mstr_ret,
        "mstr_minus_btc_1d": beta_gap,
        "common_equity_price_to_nav_delta_1d": price_to_nav_delta,
        "strc_discount_delta_1d": strc_delta,
        "drivers": drivers,
    }


def sensitivity(snapshot: dict[str, Any]) -> dict[str, Any]:
    prices = snapshot["metrics"]["prices"]
    inputs = snapshot["metrics"]["manual_inputs"]
    btc = f(prices.get("btc_usd"))
    mstr = f(prices.get("mstr_usd"))
    if btc is None or mstr is None:
        return {"scenarios": []}
    pref_total = sum((f(item.get("notional_musd")) or 0) for item in inputs.get("preferred", {}).values())
    shares = f(inputs.get("common_shares_outstanding_m")) or 0
    btc_holdings = f(inputs.get("mstr_btc_holdings")) or 0
    debt = f(inputs.get("debt_face_musd")) or 0
    cash = (f(inputs.get("usd_reserve_musd")) or 0) + (f(inputs.get("cash_other_musd")) or 0)
    dtl = f(inputs.get("deferred_tax_liability_musd")) or 0
    scenarios = []
    for shock in [-0.2, -0.1, 0, 0.1, 0.2]:
        btc_s = btc * (1 + shock)
        btc_nav = btc_holdings * btc_s / 1e6
        net_to_common = btc_nav + cash - debt - pref_total - dtl
        mkt_cap = shares * mstr
        m1 = mkt_cap / net_to_common if net_to_common > 0 else None
        common_nav_per_share = net_to_common / shares if shares > 0 else None
        scenarios.append({
            "btc_shock": shock,
            "btc_price": btc_s,
            "net_value_to_common_musd": net_to_common,
            "common_nav_per_current_share": common_nav_per_share,
            "current_mstr_price_to_common_nav": m1,
            "equity_mnav_at_current_mstr": m1,
        })
    return {
        "scenarios": scenarios,
        "assumptions": [
            "MSTR 股價與流通股數固定，只對 BTC 現價做靜態衝擊",
            "遞延稅負債固定採最近 SEC 揭露值，未模擬 BTC 公允價值變動後的稅務重估",
            "未模擬可轉債轉換、ATM 增發、優先股新發行或回購",
        ],
    }


def bmnr_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot.get("metrics", {}).get("bmnr_metrics", {})
    ratio = f(metrics.get("market_cap_to_gross_treasury"))
    discount = f(metrics.get("gross_treasury_discount"))
    gap = f(metrics.get("reported_total_crosscheck_gap"))
    if ratio is None:
        conclusion = "BMNR 官方 treasury 資料不足，只保留價格觀察"
    elif ratio < 0.8:
        conclusion = "BMNR 市值低於 gross treasury，但尚未扣完整負債與優先股，不等於普通股淨值折價"
    elif ratio <= 1.2:
        conclusion = "BMNR 市值大致貼近 gross treasury；重點轉向股數、質押收益與負債口徑"
    else:
        conclusion = "BMNR 市值高於 gross treasury，需用持續增幣、質押收益與回購證明溢價"
    return {
        "holdings_as_of": metrics.get("holdings_as_of"),
        "eth_holdings": metrics.get("eth_holdings"),
        "staked_eth_ratio": metrics.get("staked_eth_ratio"),
        "gross_treasury_musd": metrics.get("bottom_up_gross_treasury_musd"),
        "gross_treasury_value_per_share": metrics.get("gross_treasury_value_per_share"),
        "market_cap_to_gross_treasury": ratio,
        "gross_treasury_discount": discount,
        "reported_crosscheck_gap": gap,
        "conclusion": conclusion,
        "critical_limit": metrics.get("liability_treatment"),
    }


def executive_read(snapshot: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    btc = snapshot.get("metrics", {}).get("btc_standard", {})
    mstr = snapshot.get("metrics", {}).get("mstr_metrics", {})
    bmnr = snapshot.get("metrics", {}).get("bmnr_metrics", {})
    bmnr_ratio = f(bmnr.get("market_cap_to_gross_treasury"))
    common_price_to_nav = f(mstr.get("common_equity_price_to_nav"))
    enterprise_to_btc = f(mstr.get("enterprise_value_to_btc_nav"))
    btc_regime = btc.get("regime", "BTC 資料不足")
    mstr_state = "MSTR 合約封鎖" if mstr.get("contract_red_light") else "MSTR 合約僅可研究"
    bmnr_state = "BMNR gross treasury 待資料" if bmnr_ratio is None else f"BMNR 市值/gross treasury={bmnr_ratio:.2f}x"
    mstr_valuation = (
        "MSTR 普通股估值資料不足"
        if common_price_to_nav is None
        else f"MSTR 普通股市值/淨值={common_price_to_nav:.2f}x（{'折價' if common_price_to_nav <= 1 else '溢價'}）"
    )
    flywheel = "企業層倍率資料不足" if enterprise_to_btc is None else f"企業價值/BTC={enterprise_to_btc:.2f}x"
    quality = verification.get("status", "unknown")
    return {
        "headline": f"{btc_regime}｜{mstr_state}｜{bmnr_state}",
        "one_line": f"BTC 採 {btc_regime} 節奏；{mstr_state}；{mstr_valuation}、{flywheel}，估值便宜與融資飛輪分開判斷；{bmnr_state}，但 BMNR 尚非扣負債後淨 NAV；資料品質={quality}。",
        "large_spot_sleeve": btc.get("action", "等待 BTC 標準資料"),
        "tactical_mstr_sleeve": "禁止 2.5x 加碼" if mstr.get("contract_red_light") else "只進研究清單，仍需右側確認",
        "bmnr_sleeve": bmnr_analysis(snapshot).get("conclusion"),
    }


def risk_stack(snapshot: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = snapshot["metrics"]["mstr_metrics"]
    radar = snapshot["metrics"].get("market_radar", {})
    stack = []

    def add(name: str, severity: str, value: str, read: str) -> None:
        stack.append({"name": name, "severity": severity, "value": value, "read": read})

    add("Automation quality", "medium" if verification.get("status") == "degraded" else verification.get("status", "unknown"), verification.get("status", "unknown"), "Research-grade only" if verification.get("status") == "degraded" else "Data quality requires review")
    btc_standard = snapshot.get("metrics", {}).get("btc_standard", {})
    add("BTC 市場狀態", "opportunity" if (f(btc_standard.get("score")) or 0) <= -6 else "neutral", fmt(btc_standard.get("score"), 1), btc_standard.get("one_line", "BTC standard unavailable"))
    common_price_to_nav = f(metrics.get("equity_mnav"))
    add("普通股市值／普通股淨值", "unknown" if common_price_to_nav is None else "high" if common_price_to_nav > 1 else "opportunity", fmt(common_price_to_nav) + "x", "資料不足；禁止估值結論" if common_price_to_nav is None else "高於 1.0，普通股相對淨值有溢價" if common_price_to_nav > 1 else "低於 1.0，僅代表估值折價；仍需檢查資本結構與資料品質")
    add("每週賣幣壓力倍數", "high" if (f(metrics.get("sale_ratio")) or 0) > 2 else "low", fmt(metrics.get("sale_ratio"), 1) + "x", "高於 2 倍；凍結小倉合約加碼" if (f(metrics.get("sale_ratio")) or 0) > 2 else "低於紅線")
    add("STRC 優先股折價信任票", "high" if (f(metrics.get("strc_discount")) or 0) > 0.05 else "low", pct(metrics.get("strc_discount")), "折價太深；所有估值樂觀訊號降權" if (f(metrics.get("strc_discount")) or 0) > 0.05 else "信任票改善")
    add("Sentiment", "opportunity" if (f(radar.get("fear_greed")) or 50) <= 25 else "neutral", fmt(radar.get("fear_greed"), 0), "Fear helps setup quality; not a buy trigger by itself" if (f(radar.get("fear_greed")) or 50) <= 25 else "Not capitulation sentiment")
    bmnr = snapshot.get("metrics", {}).get("bmnr_metrics", {})
    bmnr_ratio = f(bmnr.get("market_cap_to_gross_treasury"))
    add("BMNR 市值／gross treasury", "medium" if bmnr_ratio is None else "opportunity" if bmnr_ratio < 0.8 else "neutral", "N/A" if bmnr_ratio is None else f"{bmnr_ratio:.2f}x", "只看 gross assets，未扣完整負債與優先股；不可直接稱淨值折價")
    return stack


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    database = load_json(DATABASE_PATH)
    verification = load_json(VERIFY_PATH)
    logic_audit = load_json(LOGIC_AUDIT_PATH, {})
    previous = latest_previous(database, snapshot["date"])
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    confidence = confidence_from_verification(verification, provenance)
    score = quality_score(verification, provenance)
    provenance_fields = provenance.get("fields", {})
    automated_fields = [name for name, field in provenance_fields.items() if str(field.get("source_type", "")).startswith("official_")]
    manual_fields = [name for name, field in provenance_fields.items() if field.get("source_type") == "manual"]
    analytics = {
        "schema": 1,
        "date": snapshot["date"],
        "generated_at": now_iso(),
        "quality": {
            "verification_status": verification.get("status"),
            "confidence": confidence,
            "confidence_score_0_100": score,
            "degradations": verification.get("degradations", []),
            "warnings": verification.get("warnings", []),
            "automated_capital_structure_fields": automated_fields,
            "manual_capital_structure_fields": manual_fields,
            "logic_audit_status": logic_audit.get("status", "not_run"),
            "logic_failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "logic_contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
        "executive_read": executive_read(snapshot, verification),
        "logic_audit": {
            "status": logic_audit.get("status", "not_run"),
            "plain_english": logic_audit.get("decision", {}).get("plain_english", "Logic audit has not run yet."),
            "blocked_actions": logic_audit.get("decision", {}).get("blocked_actions", []),
            "failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
        "decomposition": mstr_decomposition(snapshot, previous),
        "sensitivity": sensitivity(snapshot),
        "btc_analysis": snapshot.get("metrics", {}).get("btc_standard", {}),
        "bmnr_analysis": bmnr_analysis(snapshot),
        "risk_stack": risk_stack(snapshot, verification),
    }
    write_json(ANALYTICS_PATH, analytics)
    print(json.dumps({"analytics": str(ANALYTICS_PATH), "confidence": analytics["quality"]["confidence"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
