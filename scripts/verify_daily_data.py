#!/usr/bin/env python3
"""Independent data-verification agent for the daily MSTR/BTC dataset."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
RAW_PATH = DATA_DIR / "raw_observations.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
REPORT_PATH = DATA_DIR / "agent_verification_report.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def obs_map(raw: dict[str, Any]) -> dict[str, Any]:
    return {item["name"]: item for item in raw.get("observations", [])}


def pct_gap(a: float, b: float) -> float:
    return abs(a - b) / ((abs(a) + abs(b)) / 2)


def check_cross_source(name: str, left: float | None, right: float | None, threshold: float, failures: list[str], warnings: list[str]) -> None:
    if left is None or right is None:
        failures.append(f"{name}: 缺少交叉來源")
        return
    gap = pct_gap(left, right)
    if gap > threshold:
        failures.append(f"{name}: 來源差距 {gap:.2%} > {threshold:.2%}")
    elif gap > threshold / 2:
        warnings.append(f"{name}: 來源差距 {gap:.2%} 接近門檻")


def main() -> int:
    raw = load_json(RAW_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    observations = obs_map(raw)
    failures: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []

    for required in ["btc_usd_coingecko", "btc_usd_coinbase", "mstr_usd_yahoo"]:
        item = observations.get(required)
        if not item or not item.get("ok"):
            failures.append(f"必要來源失敗: {required}")
        else:
            evidence.append(f"{required}={item.get('value')} from {item.get('source')}")

    check_cross_source(
        "BTC spot",
        as_float(observations.get("btc_usd_coingecko", {}).get("value")),
        as_float(observations.get("btc_usd_coinbase", {}).get("value")),
        0.015,
        failures,
        warnings,
    )
    for ticker in ["mstr", "bmnr", "strc"]:
        yahoo = as_float(observations.get(f"{ticker}_usd_yahoo", {}).get("value"))
        stooq = as_float(observations.get(f"{ticker}_usd_stooq", {}).get("value"))
        if yahoo is not None and stooq is not None:
            check_cross_source(f"{ticker.upper()} equity", yahoo, stooq, 0.08, failures, warnings)
        else:
            warnings.append(f"{ticker.upper()} 僅有單一可用來源，已保留但降信心")

    metrics = snapshot.get("metrics", {}).get("mstr_metrics", {})
    numeric_ranges = {
        "equity_mnav": (0, 20),
        "enterprise_mnav": (0, 20),
        "coverage_months": (0, 120),
        "sale_ratio": (0, 100),
        "sats_per_share": (0, 10_000_000),
        "strc_discount": (-1, 1),
    }
    for key, (low, high) in numeric_ranges.items():
        value = as_float(metrics.get(key))
        if value is None:
            if key in {"equity_mnav", "enterprise_mnav", "strc_discount"}:
                warnings.append(f"{key}: 今日無法計算")
            else:
                failures.append(f"{key}: 缺值")
            continue
        if not low <= value <= high:
            failures.append(f"{key}: {value} 超出合理範圍 {low}..{high}")

    latest_form = str(observations.get("mstr_sec_latest_form", {}).get("value") or "")
    if latest_form not in {"8-K", "10-K", "10-Q", "S-3ASR", "424B5", "4", "144"}:
        warnings.append(f"SEC 最新表單型別非核心清單: {latest_form}")

    report = {
        "schema": 1,
        "agent": "daily-data-verifier",
        "verified_at": now_iso(),
        "date": snapshot.get("date"),
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "warnings": warnings,
        "evidence": evidence,
        "policy": {
            "btc_cross_source_max_gap": "1.5%",
            "equity_cross_source_max_gap": "8%",
            "required_sources": ["CoinGecko", "Coinbase", "Yahoo Finance"],
            "manual_review_sources": ["SEC EDGAR / Strategy filings for capital-structure inputs"],
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": report["status"], "failures": len(failures), "warnings": len(warnings)}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
