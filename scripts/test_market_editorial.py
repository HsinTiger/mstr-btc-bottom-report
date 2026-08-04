#!/usr/bin/env python3
"""Deterministic contract tests for the eight market editorial desks."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    editorial = json.loads((ROOT / "data/daily/market_editorial.json").read_text(encoding="utf-8-sig"))
    context = json.loads((ROOT / "data/daily/market_context.json").read_text(encoding="utf-8-sig"))
    verification = json.loads((ROOT / "data/daily/market_editorial_verification.json").read_text(encoding="utf-8-sig"))
    checks = 0

    def expect(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    expect(editorial["schema"] == 1, "schema")
    expect(editorial["quality"]["publication_mode"] == "analysis_only", "publication mode")
    expect(editorial["quality"]["execution_gate_eligible"] is False, "execution gate")
    expect(context["quality"]["execution_gate_eligible"] is False, "context execution gate")
    expect(verification["status"] == "pass", "verification")
    expect(verification["source_hash"] == editorial["editorial_hash"], "hash binding")
    expect(len(editorial["desks"]) == 8, "desk count")
    expect(editorial["editorial_digest"]["desk_count"] == 8, "digest desk count")
    expect(editorial["editorial_digest"]["lead_desk_id"] in {item["id"] for item in editorial["desks"]}, "lead desk")
    expect("來源只支持原始數字" in editorial["editorial_digest"]["method"], "source boundary")
    serialized = json.dumps(editorial, ensure_ascii=False)
    expect(not any(token in serialized for token in ("M1 指標", "M7 指標", "建議買進", "建議賣出", "建議加碼", "目標價為")), "cryptic labels or execution language")
    for brief in editorial["desks"]:
        expect(brief["status"] in {"pass", "degraded"}, f"{brief['id']} status")
        expect(len({item["dimension"] for item in brief["evidence"] if item["value"] is not None}) >= 3, f"{brief['id']} dimensions")
        expect(all(item["full_name"] and item["interpretation"] and item["as_of"] for item in brief["evidence"] if item["value"] is not None), f"{brief['id']} evidence")
        expect(brief["falsifier"] and brief["what_changed"], f"{brief['id']} falsifier/change")
        expect(brief["knowledge_links"], f"{brief['id']} knowledge links")
        expect(brief["editorial_scope"] == "deterministic_research_hypothesis_not_source_claim", f"{brief['id']} scope")
    liquidity_desk = next(item for item in editorial["desks"] if item["id"] == "liquidity-fed-oil")
    liquidity_metrics = {item["metric_id"] for item in liquidity_desk["evidence"]}
    expect({"net-liquidity", "m2-money-stock-yoy", "bank-reserves-30d"}.issubset(liquidity_metrics), "three-speed dollar liquidity evidence")
    technical_desk = next(item for item in editorial["desks"] if item["id"] == "technical-positioning")
    technical_metrics = {item["metric_id"] for item in technical_desk["evidence"]}
    expect({
        "btc-weekly-rsi", "btc-weekly-macd-histogram", "btc-weekly-volume-ratio", "btc-weekly-fast-ma-distance",
        "btc-monthly-rsi", "btc-monthly-macd-histogram", "btc-monthly-fast-ma-distance",
        "btc-weekly-news-sentiment", "btc-monthly-news-sentiment",
    }.issubset(technical_metrics), "completed weekly/monthly technical and sentiment evidence")
    expect("等長窗口" not in json.dumps(technical_desk, ensure_ascii=False), "technical desk must not relabel daily windows as weekly/monthly candles")
    print(f"market editorial tests: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
