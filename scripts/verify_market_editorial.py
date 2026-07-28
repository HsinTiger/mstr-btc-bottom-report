#!/usr/bin/env python3
"""Verify market editorial lineage, deterministic contracts, and history integrity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "daily"
SOURCE_PATH = DATA / "market_editorial.json"
HISTORY_PATH = DATA / "market_editorial_history.json"
OUTPUT_PATH = DATA / "market_editorial_verification.json"

EXPECTED_DESKS = [
    "crypto-core",
    "institutional-flows",
    "policy",
    "onchain",
    "technical-positioning",
    "liquidity-fed-oil",
    "credit-bonds",
    "us-equities",
]


def load(name: str) -> dict[str, Any]:
    return json.loads((DATA / name).read_text(encoding="utf-8-sig"))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def without(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def expected_direction(value: float | None, threshold: float, *, inverse: bool = False) -> str:
    if value is None or abs(value) < threshold:
        return "neutral"
    positive = value > 0
    if inverse:
        positive = not positive
    return "positive" if positive else "negative"


def verify(
    source: dict[str, Any],
    history: dict[str, Any],
    context: dict[str, Any],
    context_verify: dict[str, Any],
    market: dict[str, Any],
    market_verify: dict[str, Any],
    timescale: dict[str, Any],
    timescale_verify: dict[str, Any],
    snapshot: dict[str, Any],
    knowledge: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append({"name": name, "status": "pass" if condition else "fail", "detail": detail})
        if not condition:
            failures.append(detail)

    check("schema", source.get("schema") == 1, "market editorial schema 必須為 1")
    check("artifact_hash", source.get("editorial_hash") == canonical_hash(without(source, "editorial_hash")), "market editorial artifact hash 不一致")
    quality = source.get("quality", {})
    check("scope", quality.get("execution_gate_eligible") is False and quality.get("publication_mode") in {"analysis_only", "diagnostics_only"}, "市場總編不得接入交易執行")
    try:
        generated = datetime.fromisoformat(str(source.get("generated_at")).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    except ValueError:
        age_hours = 10**9
    check("freshness", -1 <= age_hours <= 30, f"market editorial age_hours={age_hours:.2f}")

    lineage = source.get("lineage", {})
    expected_lineage = {
        "market_context_generated_at": context.get("generated_at"),
        "market_context_hash": context.get("payload_hash"),
        "market_universe_generated_at": market.get("generated_at"),
        "market_universe_hash": canonical_hash(market),
        "timescale_generated_at": timescale.get("generated_at"),
        "timescale_hash": canonical_hash(timescale),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "snapshot_hash": canonical_hash(snapshot),
        "knowledge_context_generated_at": knowledge.get("generated_at"),
        "knowledge_context_hash": canonical_hash(knowledge),
    }
    check("lineage_hashes", lineage == expected_lineage, "市場總編輸入 lineage 或完整 artifact hash 不一致")
    check("context_verifier_binding", context_verify.get("source_generated_at") == context.get("generated_at") and context_verify.get("source_hash") == context.get("payload_hash") and context_verify.get("status") in {"pass", "degraded"}, "market context verifier 未綁定目前 hash")
    check("market_verifier_binding", market_verify.get("market_generated_at") == market.get("generated_at") and market_verify.get("status") in {"pass", "degraded"}, "market universe verifier 未綁定目前批次")
    check("timescale_verifier_binding", timescale_verify.get("analysis_generated_at") == timescale.get("generated_at") and timescale_verify.get("status") == "pass", "timescale verifier 未綁定目前批次")
    expected_upstream_statuses = {
        "market_context": context.get("quality", {}).get("status"),
        "market_context_verifier": context_verify.get("status"),
        "market_universe": market.get("quality", {}).get("status"),
        "market_universe_verifier": market_verify.get("status"),
        "timescale": timescale.get("quality", {}).get("status"),
        "timescale_verifier": timescale_verify.get("status"),
    }
    check("upstream_statuses", quality.get("upstream_statuses") == expected_upstream_statuses, "市場總編未完整傳播上游品質狀態")

    desks = source.get("desks", [])
    if quality.get("publication_mode") == "diagnostics_only":
        check("diagnostics_empty", not desks, "diagnostics-only 不得保留舊研究結論")
    else:
        check("desk_order", [item.get("id") for item in desks] == EXPECTED_DESKS, "八個研究桌缺漏、重複或順序錯誤")
        knowledge_slugs = {page.get("slug") for page in knowledge.get("pages", [])}
        market_radar = snapshot.get("metrics", {}).get("market_radar", {})
        btc_dat = market.get("dat", {}).get("BTC", {})
        eth_dat = market.get("dat", {}).get("ETH", {})
        mstr = next((item for item in btc_dat.get("companies", []) if item.get("symbol") == "MSTR"), {})
        bmnr = next((item for item in eth_dat.get("companies", []) if item.get("symbol") == "BMNR"), {})
        policy_events = context.get("policy", {}).get("events", [])
        macro = context.get("macro", {})
        liquidity = macro.get("liquidity", {})
        rates = macro.get("rates", {})
        credit = macro.get("credit", {})
        equities = macro.get("equities", {})
        oil = macro.get("oil", {})
        btc_chain = context.get("onchain", {}).get("BTC", {})
        eth_chain = context.get("onchain", {}).get("ETH", {})
        horizons = timescale.get("horizons", {})
        tracked = market.get("analysis", {}).get("breadth", {}).get("tracked_assets", 0)
        positive_assets = market.get("analysis", {}).get("breadth", {}).get("positive_assets", 0)
        expected_values = {
            "btc-24h-return": finite(market.get("assets", {}).get("BTC", {}).get("change_24h")),
            "eth-24h-return": finite(market.get("assets", {}).get("ETH", {}).get("change_24h")),
            "btc-monthly-return": finite(horizons.get("monthly", {}).get("metrics", {}).get("btc_return")),
            "btc-funding": finite(market.get("analysis", {}).get("BTC", {}).get("funding_annualized_median")),
            "btc-etf-7d": finite(market_radar.get("etf_flow_7d_usd")),
            "eth-etf-7d": finite(market_radar.get("eth_etf_flow_7d_usd")),
            "btc-dat-share": finite(btc_dat.get("supply_share")),
            "eth-dat-share": finite(eth_dat.get("supply_share")),
            "institutional-price-absorption": finite(market.get("assets", {}).get("BTC", {}).get("change_24h")),
            "policy-events-7d": finite(context.get("policy", {}).get("event_count_7d")),
            "policy-rulemaking": float(sum(item.get("source_type") == "official_rulemaking" for item in policy_events)),
            "policy-legislation": float(sum(item.get("source_type") == "official_legislation" for item in policy_events)),
            "btc-mvrv": finite(market_radar.get("btc_mvrv_current")),
            "btc-transactions": finite(btc_chain.get("transactions", {}).get("change_30d")),
            "btc-active-addresses": finite(btc_chain.get("active_addresses", {}).get("change_30d")),
            "btc-hashrate": finite(btc_chain.get("hashrate", {}).get("change_30d")),
            "eth-tx-block": finite(eth_chain.get("transactions_per_block_7d_change")),
            "btc-daily-window": finite(horizons.get("daily", {}).get("metrics", {}).get("btc_return")),
            "btc-weekly-window": finite(horizons.get("weekly", {}).get("metrics", {}).get("btc_return")),
            "crypto-breadth": positive_assets / tracked if tracked else None,
            "btc-perp-funding": finite(market.get("analysis", {}).get("BTC", {}).get("funding_annualized_median")),
            "net-liquidity": finite(liquidity.get("net_liquidity_million_usd")),
            "m2-money-stock-yoy": finite(liquidity.get("m2_money_stock_yoy_change")),
            "bank-reserves-30d": finite(liquidity.get("reserve_balances_30d_change")),
            "fed-funds": finite(rates.get("fed_funds_pct")),
            "broad-dollar": finite(equities.get("broad_dollar", {}).get("change_30d")),
            "wti-oil": finite(oil.get("wti_spot_30d_change")) if finite(oil.get("wti_spot_30d_change")) is not None else finite(oil.get("wti_future_proxy_30d_change")),
            "hy-oas": finite(credit.get("high_yield_oas_pct")),
            "ig-oas": finite(credit.get("investment_grade_oas_pct")),
            "treasury-10y": finite(rates.get("treasury_10y_pct")),
            "curve-2s10s": finite(rates.get("curve_2s10s_pp")),
            "sp500-30d": finite(equities.get("sp500", {}).get("change_30d")),
            "nasdaq-30d": finite(equities.get("nasdaq", {}).get("change_30d")),
            "vix-level": finite(equities.get("vix", {}).get("value")),
            "btc-30d-relative": finite(market_radar.get("btc_return_30d_pct")),
        }
        btc_change = expected_values["btc-24h-return"]
        btc_etf_7d = expected_values["btc-etf-7d"]
        funding = expected_values["btc-funding"]
        mvrv = expected_values["btc-mvrv"]
        net_change = finite(liquidity.get("net_liquidity_30d_change"))
        m2_yoy = expected_values["m2-money-stock-yoy"]
        reserve_change = expected_values["bank-reserves-30d"]
        fed_funds = expected_values["fed-funds"]
        oil_change = expected_values["wti-oil"]
        hy_change = finite(credit.get("high_yield_oas_30d_change_pp"))
        ig_change = finite(credit.get("investment_grade_oas_30d_change_pp"))
        yield_10y = expected_values["treasury-10y"]
        vix = expected_values["vix-level"]
        latest_policy_at = policy_events[0].get("published_at") if policy_events else None
        funding_sources = int(market.get("derivatives", {}).get("BTC", {}).get("perpetual", {}).get("funding_source_count", 0))
        btc_sources = int(market.get("assets", {}).get("BTC", {}).get("source_count", 0))
        expected_contracts = {
            "btc-24h-return": (market.get("assets", {}).get("BTC", {}).get("as_of"), btc_sources, expected_direction(btc_change, 0.02)),
            "eth-24h-return": (market.get("assets", {}).get("ETH", {}).get("as_of"), int(market.get("assets", {}).get("ETH", {}).get("source_count", 0)), expected_direction(expected_values["eth-24h-return"], 0.02)),
            "btc-monthly-return": (horizons.get("monthly", {}).get("data_depth", {}).get("as_of"), int(horizons.get("monthly", {}).get("data_depth", {}).get("source_count", 0)), expected_direction(expected_values["btc-monthly-return"], 0.04)),
            "btc-funding": (market.get("derivatives", {}).get("BTC", {}).get("perpetual", {}).get("okx", {}).get("as_of"), funding_sources, expected_direction(funding, 0.12, inverse=True)),
            "btc-etf-7d": (market_radar.get("etf_flow_as_of"), int(market_radar.get("etf_flow_source_count") or 0), expected_direction(btc_etf_7d, 100_000_000)),
            "eth-etf-7d": (market_radar.get("eth_etf_flow_as_of"), int(market_radar.get("eth_etf_flow_source_count") or 0), expected_direction(expected_values["eth-etf-7d"], 50_000_000)),
            "btc-dat-share": (mstr.get("as_of") or btc_dat.get("as_of"), int(btc_dat.get("source_count", 0)), expected_direction(finite(mstr.get("holdings_change")), 1)),
            "eth-dat-share": (bmnr.get("as_of") or eth_dat.get("as_of"), int(eth_dat.get("source_count", 0)), expected_direction(finite(bmnr.get("holdings_change")), 1)),
            "institutional-price-absorption": (market.get("assets", {}).get("BTC", {}).get("as_of"), btc_sources, "positive" if (btc_etf_7d or 0) > 0 and (btc_change or 0) > 0 else "negative" if (btc_etf_7d or 0) > 0 and (btc_change or 0) < 0 else "neutral"),
            "policy-events-7d": (context.get("generated_at"), int(context.get("policy", {}).get("successful_sources", 0)), expected_direction(expected_values["policy-events-7d"], 1)),
            "policy-rulemaking": (latest_policy_at, 1, expected_direction(expected_values["policy-rulemaking"], 1)),
            "policy-legislation": (latest_policy_at, 1, expected_direction(expected_values["policy-legislation"], 1)),
            "btc-mvrv": (snapshot.get("date"), 1, expected_direction((1.5 - mvrv) if mvrv is not None else None, 0.15)),
            "btc-transactions": (btc_chain.get("transactions", {}).get("as_of"), 2, expected_direction(expected_values["btc-transactions"], 0.08)),
            "btc-active-addresses": (btc_chain.get("active_addresses", {}).get("as_of"), 1, expected_direction(expected_values["btc-active-addresses"], 0.08)),
            "btc-hashrate": (btc_chain.get("hashrate", {}).get("as_of"), 2, expected_direction(expected_values["btc-hashrate"], 0.08)),
            "eth-tx-block": (eth_chain.get("as_of"), int(eth_chain.get("source_count", 0)), expected_direction(expected_values["eth-tx-block"], 0.08)),
            "btc-daily-window": (horizons.get("daily", {}).get("data_depth", {}).get("as_of"), int(horizons.get("daily", {}).get("data_depth", {}).get("source_count", 0)), expected_direction(expected_values["btc-daily-window"], 0.02)),
            "btc-weekly-window": (horizons.get("weekly", {}).get("data_depth", {}).get("as_of"), int(horizons.get("weekly", {}).get("data_depth", {}).get("source_count", 0)), expected_direction(expected_values["btc-weekly-window"], 0.04)),
            "crypto-breadth": (market.get("generated_at"), 2, expected_direction((expected_values["crypto-breadth"] or 0) - 0.5, 0.2)),
            "btc-perp-funding": (market.get("generated_at"), funding_sources, expected_direction(funding, 0.12, inverse=True)),
            "net-liquidity": (liquidity.get("as_of"), 3, expected_direction(net_change, 0.01)),
            "m2-money-stock-yoy": (liquidity.get("m2_money_stock_as_of"), 1, expected_direction(m2_yoy, 0.01)),
            "bank-reserves-30d": (liquidity.get("reserve_balances_as_of"), 1, expected_direction(reserve_change, 0.01)),
            "fed-funds": (rates.get("fed_funds_as_of"), 1, expected_direction((4.0 - fed_funds) if fed_funds is not None else None, 0.25)),
            "broad-dollar": (equities.get("broad_dollar", {}).get("as_of"), 1, expected_direction(expected_values["broad-dollar"], 0.01, inverse=True)),
            "wti-oil": (oil.get("wti_spot_as_of") or oil.get("wti_future_proxy_as_of"), 2, expected_direction(oil_change, 0.08, inverse=True)),
            "hy-oas": (credit.get("as_of"), 2, expected_direction(hy_change, 0.25, inverse=True)),
            "ig-oas": (credit.get("as_of"), 1, expected_direction(ig_change, 0.12, inverse=True)),
            "treasury-10y": (rates.get("as_of"), 2, expected_direction((4.5 - yield_10y) if yield_10y is not None else None, 0.25)),
            "curve-2s10s": (rates.get("as_of"), 2, expected_direction(expected_values["curve-2s10s"], 0.25)),
            "sp500-30d": (equities.get("sp500", {}).get("as_of"), 2, expected_direction(expected_values["sp500-30d"], 0.03)),
            "nasdaq-30d": (equities.get("nasdaq", {}).get("as_of"), 2, expected_direction(expected_values["nasdaq-30d"], 0.03)),
            "vix-level": (equities.get("vix", {}).get("as_of"), 2, expected_direction((20 - vix) if vix is not None else None, 3)),
            "btc-30d-relative": (snapshot.get("date"), 3, expected_direction(expected_values["btc-30d-relative"], 0.08)),
        }
        for brief in desks:
            desk_id = str(brief.get("id"))
            required = all(brief.get(field) for field in (
                "title", "headline", "conclusion", "common_interpretation", "variant_view",
                "second_order_effect", "practical_readthrough", "falsifier", "what_changed",
            ))
            check(f"brief_fields:{desk_id}", required, f"{desk_id} 編輯欄位不完整")
            check(f"brief_scope:{desk_id}", brief.get("editorial_scope") == "deterministic_research_hypothesis_not_source_claim", f"{desk_id} 未聲明來源不背書本站假說")
            check(f"brief_hash:{desk_id}", brief.get("brief_hash") == canonical_hash(without(brief, "brief_hash")), f"{desk_id} brief hash 不一致")
            evidence = brief.get("evidence", [])
            known = [item for item in evidence if item.get("value") is not None]
            dimensions = {item.get("dimension") for item in known}
            check(f"evidence_depth:{desk_id}", len(known) >= 3 and len(dimensions) >= 3, f"{desk_id} 未達三個獨立視角")
            check(f"evidence_contract:{desk_id}", all(
                item.get("metric_id") and item.get("full_name") and item.get("display")
                and item.get("as_of") and item.get("direction") in {"positive", "negative", "neutral"}
                and int(item.get("source_count", 0)) >= 1 and item.get("interpretation")
                for item in known
            ), f"{desk_id} 證據欄位缺漏")
            check(f"evidence_values:{desk_id}", all(
                item.get("metric_id") in expected_values
                and (
                    expected_values[item["metric_id"]] is None and item.get("value") is None
                    or expected_values[item["metric_id"]] is not None and finite(item.get("value")) is not None
                    and abs(expected_values[item["metric_id"]] - finite(item.get("value"))) <= max(1e-9, abs(expected_values[item["metric_id"]]) * 1e-12)
                )
                for item in evidence
            ), f"{desk_id} 證據值未由目前已驗證輸入重算")
            check(f"evidence_metadata:{desk_id}", all(
                item.get("metric_id") in expected_contracts
                and item.get("as_of") == expected_contracts[item["metric_id"]][0]
                and int(item.get("source_count", 0)) == expected_contracts[item["metric_id"]][1]
                and item.get("direction") == expected_contracts[item["metric_id"]][2]
                for item in evidence
            ), f"{desk_id} as_of、來源數或方向未由目前輸入重算")
            check(f"source_links:{desk_id}", all(
                entry.get("label") and (str(entry.get("url", "")).startswith("https://") or str(entry.get("url", "")).endswith(".html") or ".html#" in str(entry.get("url", "")))
                for item in evidence for entry in item.get("sources", [])
            ), f"{desk_id} 證據來源連結無效")
            check(f"knowledge_links:{desk_id}", bool(brief.get("knowledge_links")) and all(item.get("slug") in knowledge_slugs for item in brief.get("knowledge_links", [])), f"{desk_id} 未連結 LLM Wiki 知識基礎")
            recomputed = {
                "positive": sum(item.get("direction") == "positive" for item in known),
                "negative": sum(item.get("direction") == "negative" for item in known),
                "neutral": sum(item.get("direction") == "neutral" for item in known),
                "known": len(known),
            }
            resonance = brief.get("resonance", {})
            check(f"resonance_counts:{desk_id}", all(resonance.get(key) == value for key, value in recomputed.items()), f"{desk_id} 共振票數不可重算")

        digest = source.get("editorial_digest", {})
        valid = [item for item in desks if item.get("status") != "fail"]
        expected_lead = max(valid, key=lambda item: float(item.get("materiality_score", 0)), default={}).get("id")
        check("lead_selection", digest.get("lead_desk_id") == expected_lead and digest.get("desk_count") == len(desks), "主文未綁定最高 materiality 研究桌")
        check("digest_method", "來源只支持原始數字" in str(digest.get("method")), "總編方法未聲明來源與本站假說邊界")
        source_health = digest.get("source_health", {})
        check("source_health", source_health == {
            "successful": context.get("quality", {}).get("successful_sources", 0),
            "failed_with_fallback": context.get("quality", {}).get("failed_sources", 0),
            "total": context.get("quality", {}).get("successful_sources", 0) + context.get("quality", {}).get("failed_sources", 0),
        }, "總編來源健康摘要與市場 context 不一致")
        upstream_degraded = any(value == "degraded" for value in expected_upstream_statuses.values())
        expected_editorial_status = "degraded" if upstream_degraded or any(item.get("status") == "degraded" for item in desks) else "pass"
        check("quality_status", quality.get("status") == expected_editorial_status and source.get("editorial_digest", {}).get("status") == expected_editorial_status, f"市場總編品質應為 {expected_editorial_status}，不得假 PASS")

    previous_hash = None
    history_ok = history.get("schema") == 1 and isinstance(history.get("runs"), list) and len(history.get("runs", [])) <= 5000
    for run in history.get("runs", []):
        if run.get("previous_run_hash") != previous_hash or run.get("run_hash") != canonical_hash(without(run, "run_hash")):
            history_ok = False
            break
        previous_hash = run.get("run_hash")
    check("history_chain", history_ok and history.get("head_hash") == previous_hash, "market editorial history chain 或 head hash 不一致")
    if quality.get("publication_mode") == "analysis_only":
        latest = history.get("runs", [])[-1] if history.get("runs") else {}
        expected_compact = [
            {
                "id": brief["id"],
                "status": brief["status"],
                "headline": brief["headline"],
                "resonance_state": brief["resonance"]["state"],
                "brief_hash": brief["brief_hash"],
                "evidence_fingerprint": canonical_hash([
                    {"metric_id": item["metric_id"], "value": item["value"], "as_of": item["as_of"]}
                    for item in brief["evidence"]
                ]),
            }
            for brief in desks
        ]
        check("current_history_run", latest.get("generated_at") == source.get("generated_at") and latest.get("editorial_hash") == source.get("editorial_hash") and latest.get("briefs") == expected_compact, "目前總編輸出未綁定 history 最新 run")

    return {
        "schema": 1,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_generated_at": source.get("generated_at"),
        "source_hash": source.get("editorial_hash"),
        "history_head_hash": history.get("head_hash"),
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "checks": checks,
        "desk_count": len(desks),
        "execution_gate_eligible": False,
    }


def main() -> int:
    source = load("market_editorial.json")
    history = load("market_editorial_history.json")
    report = verify(
        source,
        history,
        load("market_context.json"),
        load("market_context_verification.json"),
        load("market_universe.json"),
        load("market_universe_verification.json"),
        load("timescale_intelligence.json"),
        load("timescale_intelligence_verification.json"),
        load("latest_snapshot.json"),
        load("knowledge_context.json"),
    )
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT_PATH), "status": report["status"], "checks": len(report["checks"]), "failures": len(report["failures"])}, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
