#!/usr/bin/env python3
"""Verify four-horizon analysis math, lineage, scope, and append-only history."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
ANALYSIS_PATH = DATA_DIR / "timescale_intelligence.json"
HISTORY_PATH = DATA_DIR / "timescale_intelligence_history.json"
PRICE_HISTORY_PATH = DATA_DIR / "timescale_price_history.json"
DATA_VERIFICATION_PATH = DATA_DIR / "timescale_data_verification.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
OUTPUT_PATH = DATA_DIR / "timescale_intelligence_verification.json"

HORIZON_BARS = {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63}
ASSETS = ("BTC", "ETH", "MSTR", "BMNR", "STRC")
PROHIBITED_ACTION_PHRASES = ("建議買進", "建議賣出", "應加碼", "應減碼", "開槓桿", "目標價為")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def expected_return(rows: list[dict[str, Any]], bars: int) -> float | None:
    if len(rows) <= bars:
        return None
    current = number(rows[-1].get("close"))
    previous = number(rows[-bars - 1].get("close"))
    return current / previous - 1 if current is not None and previous not in (None, 0) else None


def close_enough(first: Any, second: Any, tolerance: float = 1e-10) -> bool:
    first_number = number(first)
    second_number = number(second)
    if first_number is None or second_number is None:
        return first_number is None and second_number is None
    return abs(first_number - second_number) <= tolerance * max(1.0, abs(first_number), abs(second_number))


def collect_analysis_text(analysis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for horizon in analysis.get("horizons", {}).values():
        for key in ("plain_read", "what_changed", "falsifier"):
            if horizon.get(key):
                values.append(str(horizon[key]))
        values.extend(str(item.get("plain_read")) for item in horizon.get("perspectives", []) if item.get("plain_read"))
    for insight in analysis.get("exclusive_insights", []):
        values.extend(str(insight.get(key)) for key in ("claim", "what_changed", "falsifier") if insight.get(key))
    return values


def verify() -> dict[str, Any]:
    analysis = load_json(ANALYSIS_PATH)
    history = load_json(HISTORY_PATH)
    price_history = load_json(PRICE_HISTORY_PATH)
    data_verification = load_json(DATA_VERIFICATION_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    market = load_json(MARKET_PATH)
    failures: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    if analysis.get("schema") != 1:
        failures.append("analysis schema is not 1")
    if history.get("schema") != 1:
        failures.append("analysis history schema is not 1")
    lineage = {
        "snapshot": analysis.get("snapshot_generated_at") == snapshot.get("generated_at"),
        "market": analysis.get("market_universe_generated_at") == market.get("generated_at"),
        "price_history": analysis.get("price_history_generated_at") == price_history.get("generated_at"),
        "price_verification": data_verification.get("history_generated_at") == price_history.get("generated_at"),
    }
    if not all(lineage.values()):
        failures.append(f"analysis lineage mismatch: {lineage}")
    quality = analysis.get("quality", {})
    if quality.get("execution_gate_eligible") is not False:
        failures.append("timescale analysis must never be execution-gate eligible")
    if quality.get("publication_mode") not in {"analysis_only", "diagnostics_only"}:
        failures.append("invalid publication mode")
    if quality.get("status") not in {"pass", "degraded", "fail"}:
        failures.append("invalid quality status")
    if quality.get("status") == "fail":
        if analysis.get("horizons") or analysis.get("exclusive_insights"):
            failures.append("failed analysis still exposes conclusions")
    else:
        if set(analysis.get("horizons", {})) != set(HORIZON_BARS):
            failures.append("four-horizon analysis is incomplete")
        if set(analysis.get("asset_matrix", {})) != set(ASSETS):
            failures.append("asset matrix is incomplete")
        for symbol in ASSETS:
            price_asset = price_history.get("assets", {}).get(symbol, {})
            provider = price_asset.get("canonical_provider")
            rows = (price_asset.get("sources", {}).get(provider) or {}).get("rows") or []
            for horizon, bars in HORIZON_BARS.items():
                actual = (((analysis.get("asset_matrix") or {}).get(symbol) or {}).get(horizon) or {}).get("return")
                expected = expected_return(rows, bars)
                passed = close_enough(actual, expected)
                checks.append({"name": f"{symbol}_{horizon}_return", "status": "pass" if passed else "fail", "actual": actual, "expected": expected})
                if not passed:
                    failures.append(f"{symbol} {horizon} return does not match canonical completed bars")
                if symbol != "BTC":
                    btc_return = (((analysis.get("asset_matrix") or {}).get("BTC") or {}).get(horizon) or {}).get("return")
                    relative = (((analysis.get("asset_matrix") or {}).get(symbol) or {}).get(horizon) or {}).get("relative_to_btc")
                    expected_relative = number(actual) - number(btc_return) if number(actual) is not None and number(btc_return) is not None else None
                    if not close_enough(relative, expected_relative):
                        failures.append(f"{symbol} {horizon} relative-to-BTC return is inconsistent")
        alignment = analysis.get("alignment", {})
        if alignment.get("known_horizons") != 4:
            failures.append("alignment does not cover all four horizons")
        if len(analysis.get("exclusive_insights", [])) < 3:
            failures.append("exclusive insight set is incomplete")
    text = "\n".join(collect_analysis_text(analysis))
    for phrase in PROHIBITED_ACTION_PHRASES:
        if phrase in text:
            failures.append(f"analysis contains prohibited strategy output: {phrase}")
    items = history.get("items") or []
    if not items or items[-1].get("generated_at") != analysis.get("generated_at"):
        failures.append("append-only history is not bound to the current analysis")
    if items:
        same_date = [item for item in items if item.get("date") == analysis.get("date")]
        if items[-1].get("revision") != len(same_date):
            failures.append("same-day revision numbering is inconsistent")
        if len(same_date) > 1 and not items[-1].get("supersedes_generated_at"):
            failures.append("same-day revision does not preserve superseded observation")
        if not items[-1].get("revision_note"):
            failures.append("append-only history is missing a revision note")
    distinct_dates = {item.get("date") for item in items if item.get("date")}
    if analysis.get("record_advantage", {}).get("observations") != len(items):
        failures.append("analysis observation count does not match append-only history")
    if analysis.get("record_advantage", {}).get("distinct_dates") != len(distinct_dates):
        failures.append("analysis distinct-date count does not match append-only history")
    if len(distinct_dates) < 20:
        warnings.append("history has fewer than 20 distinct dates; percentile claims remain disabled")
        if any((item.get("historical_percentile") or {}).get("status") == "available" for item in analysis.get("horizons", {}).values()):
            failures.append("percentile was published before the 20-date minimum")
    status = "fail" if failures else "pass"
    return {
        "schema": 1,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "analysis_generated_at": analysis.get("generated_at"),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
        "lineage": lineage,
        "scope": "analysis_integrity_only",
        "execution_gate_eligible": False,
    }


def main() -> int:
    report = verify()
    write_json(OUTPUT_PATH, report)
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "status": report["status"],
        "checks": len(report["checks"]),
        "failures": len(report["failures"]),
        "warnings": len(report["warnings"]),
    }, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
