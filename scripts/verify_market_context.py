#!/usr/bin/env python3
"""Independently verify the daily macro, policy, equity, and on-chain artifact."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "daily" / "market_context.json"
OUTPUT_PATH = ROOT / "data" / "daily" / "market_context_verification.json"


def finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def without_hash(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "payload_hash"}


def relative_gap(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or max(abs(left), abs(right)) == 0:
        return None
    return abs(left - right) / max(abs(left), abs(right))


def verify(source: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str, *, hard: bool = True) -> None:
        checks.append({"name": name, "status": "pass" if condition else "fail" if hard else "degraded", "detail": detail})
        if not condition:
            (failures if hard else degradations).append(detail)

    check("schema", source.get("schema") == 1, "market context schema 必須為 1")
    check("payload_hash", source.get("payload_hash") == canonical_hash(without_hash(source)), "market context payload hash 不一致")
    quality = source.get("quality", {})
    check("analysis_only", quality.get("execution_gate_eligible") is False and quality.get("publication_mode") in {"analysis_only", "diagnostics_only"}, "market context 不得接入交易執行")
    generated: datetime | None = None
    try:
        generated = datetime.fromisoformat(str(source.get("generated_at")).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    except ValueError:
        age_hours = 10**9
    check("freshness", -1 <= age_hours <= 30, f"market context age_hours={age_hours:.2f}")
    check("date_binding", generated is not None and source.get("date") == generated.date().isoformat(), "market context date 與 generated_at 不一致")

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

    def age_days(value: Any) -> float | None:
        parsed = parse_time(value)
        return (generated - parsed).total_seconds() / 86400 if parsed and generated else None

    source_checks = source.get("source_checks", [])
    passed_sources = sum(item.get("status") == "pass" for item in source_checks)
    failed_sources = sum(item.get("status") == "fail" for item in source_checks)
    check("source_count", quality.get("successful_sources") == passed_sources and quality.get("failed_sources") == failed_sources, "來源健康計數與 artifact 不一致")
    check("source_urls", all(item.get("url", "").startswith("https://") for item in source_checks if item.get("status") == "pass"), "成功來源必須保留 HTTPS URL")

    def source_pass(provider: str) -> bool:
        return any(item.get("provider") == provider and item.get("status") == "pass" for item in source_checks)

    independent_pairs = {
        "fed_assets": source_pass("FRED WALCL") and source_pass("Federal Reserve H.4.1"),
        "m2_official": source_pass("FRED M2SL"),
        "reserve_balances_official": source_pass("FRED WRESBAL"),
        "treasury_10y": source_pass("FRED DGS10") and source_pass("U.S. Treasury"),
        "sp500": source_pass("FRED SP500") and source_pass("Yahoo ^GSPC"),
        "nasdaq": source_pass("FRED NASDAQCOM") and source_pass("Yahoo ^IXIC"),
        "btc_transactions": source_pass("Blockchain.com n-transactions") and source_pass("Blockchair Bitcoin"),
        "btc_hashrate": source_pass("Blockchain.com hash-rate") and source_pass("mempool.space"),
    }
    for name, passed in independent_pairs.items():
        check(f"independent_pair:{name}", passed, f"{name} 缺少兩個獨立成功來源，不得標示完整通過", hard=False)

    macro = source.get("macro", {})
    liquidity = macro.get("liquidity", {})
    fed_assets = finite(liquidity.get("fed_assets_million_usd"))
    h41_assets = finite(liquidity.get("h41_assets_million_usd"))
    tga = finite(liquidity.get("tga_million_usd"))
    rrp = finite(liquidity.get("rrp_usd"))
    net = finite(liquidity.get("net_liquidity_million_usd"))
    expected_net = fed_assets - tga - rrp / 1_000_000 if None not in {fed_assets, tga, rrp} else None
    check("net_liquidity_math", expected_net is not None and net is not None and abs(expected_net - net) < 0.01, "淨流動性公式重算不一致")
    prior_net = finite(liquidity.get("prior_net_liquidity_million_usd"))
    net_change = finite(liquidity.get("net_liquidity_30d_change"))
    expected_net_change = net / prior_net - 1 if net is not None and prior_net not in (None, 0) else None
    check("net_liquidity_change_math", expected_net_change is not None and net_change is not None and abs(expected_net_change - net_change) < 1e-12, "淨流動性 30 日變化重算不一致")
    m2_value = finite(liquidity.get("m2_money_stock_billion_usd"))
    m2_prior_365 = finite(liquidity.get("m2_money_stock_prior_365d_value"))
    m2_prior_90 = finite(liquidity.get("m2_money_stock_prior_90d_value"))
    m2_yoy = finite(liquidity.get("m2_money_stock_yoy_change"))
    m2_3m_annualized = finite(liquidity.get("m2_money_stock_3m_annualized_change"))
    expected_m2_yoy = m2_value / m2_prior_365 - 1 if m2_value is not None and m2_prior_365 not in (None, 0) else None
    expected_m2_3m = (m2_value / m2_prior_90) ** 4 - 1 if m2_value is not None and m2_prior_90 not in (None, 0) else None
    check("m2_yoy_math", expected_m2_yoy is not None and m2_yoy is not None and abs(expected_m2_yoy - m2_yoy) < 1e-12, "M2 年增率重算不一致")
    check("m2_3m_annualized_math", expected_m2_3m is not None and m2_3m_annualized is not None and abs(expected_m2_3m - m2_3m_annualized) < 1e-12, "M2 三個月年化變化重算不一致")
    reserve_value = finite(liquidity.get("reserve_balances_million_usd"))
    reserve_prior = finite(liquidity.get("reserve_balances_prior_30d_value"))
    reserve_change = finite(liquidity.get("reserve_balances_30d_change"))
    expected_reserve_change = reserve_value / reserve_prior - 1 if reserve_value is not None and reserve_prior not in (None, 0) else None
    check("reserve_balances_change_math", expected_reserve_change is not None and reserve_change is not None and abs(expected_reserve_change - reserve_change) < 1e-12, "銀行準備金 30 日變化重算不一致")

    def liquidity_vote(value: float | None, threshold: float = 0.01) -> str:
        if value is None or abs(value) < threshold:
            return "neutral"
        return "positive" if value > 0 else "negative"

    expected_components = {
        "fed_net_liquidity_30d": liquidity_vote(net_change),
        "bank_reserve_balances_30d": liquidity_vote(reserve_change),
        "m2_money_stock_yoy": liquidity_vote(m2_yoy),
    }
    positive_votes = sum(value == "positive" for value in expected_components.values())
    negative_votes = sum(value == "negative" for value in expected_components.values())
    neutral_votes = sum(value == "neutral" for value in expected_components.values())
    expected_state = "擴張共振" if positive_votes >= 2 and negative_votes == 0 else "收縮共振" if negative_votes >= 2 and positive_votes == 0 else "不同頻率分歧"
    expected_resonance = {
        "state": expected_state,
        "positive_votes": positive_votes,
        "negative_votes": negative_votes,
        "neutral_votes": neutral_votes,
        "components": expected_components,
        "method": "Fed 淨流動性 30 日、銀行準備金 30 日與 M2 年增各一票；只判方向共振，不混成黑箱指數",
    }
    check("dollar_liquidity_resonance", liquidity.get("dollar_liquidity_resonance") == expected_resonance, "美元流動性共振票數或方向不可重算")
    assets_gap = relative_gap(fed_assets, h41_assets)
    check("fed_assets_cross_source", assets_gap is not None and assets_gap <= 0.02 and abs(assets_gap - finite(liquidity.get("fed_assets_cross_source_gap"))) < 1e-9, "Fed 總資產跨來源差異超過 2%")
    component_dates = [liquidity.get("fed_assets_as_of"), liquidity.get("tga_as_of"), liquidity.get("rrp_as_of")]
    check("net_liquidity_as_of", all(parse_time(value) for value in component_dates) and liquidity.get("as_of") == min(component_dates), "淨流動性 as_of 必須等於最慢組件日期")
    prior_dates = liquidity.get("prior_component_as_of", {})
    prior_values = [prior_dates.get("fed_assets"), prior_dates.get("tga"), prior_dates.get("rrp")]
    check("prior_net_liquidity_as_of", all(parse_time(value) for value in prior_values) and liquidity.get("prior_net_liquidity_as_of") == min(prior_values), "前期淨流動性 as_of 必須等於實際最慢前期組件日期")
    h41_observed = parse_time(liquidity.get("h41_assets_as_of"))
    h41_released = parse_time(liquidity.get("h41_release_date"))
    check("h41_observation_date", liquidity.get("h41_assets_as_of") == liquidity.get("fed_assets_as_of") and h41_observed is not None and h41_released is not None and h41_released >= h41_observed, "H.4.1 觀測日與發布日語意錯誤")
    for field, maximum in (("fed_assets_as_of", 14), ("h41_assets_as_of", 14), ("tga_as_of", 10), ("rrp_as_of", 10), ("m2_money_stock_as_of", 120), ("reserve_balances_as_of", 14)):
        age = age_days(liquidity.get(field))
        check(f"fresh:{field}", age is not None and -1 <= age <= maximum, f"{field} 超過 {maximum} 日新鮮度契約")

    rates = macro.get("rates", {})
    rate_2y = finite(rates.get("treasury_2y_pct"))
    rate_10y = finite(rates.get("treasury_10y_pct"))
    curve = finite(rates.get("curve_2s10s_pp"))
    check("yield_curve_math", None not in {rate_2y, rate_10y, curve} and abs((rate_10y - rate_2y) - curve) < 1e-9, "2s10s 殖利率曲線重算不一致")
    direct_10y = finite(rates.get("direct_values", {}).get("treasury_10y_pct"))
    fred_10y = finite(rates.get("fred_values", {}).get("treasury_10y_pct"))
    direct_gap = relative_gap(direct_10y, fred_10y)
    stored_gap = finite(rates.get("direct_fred_10y_gap_pp"))
    check("treasury_fred_gap", direct_10y is not None and fred_10y is not None and stored_gap is not None and abs(abs(direct_10y - fred_10y) - stored_gap) < 1e-9 and stored_gap <= 0.15, "Treasury 與 FRED 10 年殖利率未以獨立原值對帳", hard=False)
    rates_age = age_days(rates.get("as_of"))
    check("rates_as_of", rates_age is not None and -1 <= rates_age <= 10, "公債殖利率超過 10 日新鮮度契約")

    credit = macro.get("credit", {})
    hy = finite(credit.get("high_yield_oas_pct"))
    ig = finite(credit.get("investment_grade_oas_pct"))
    check("credit_ranges", hy is not None and ig is not None and 0 <= ig <= hy <= 30, "信用利差超出合理範圍")
    credit_age = age_days(credit.get("as_of"))
    check("credit_as_of", credit_age is not None and -1 <= credit_age <= 10, "信用利差超過 10 日新鮮度契約")
    oil = macro.get("oil", {})
    check("oil_range", any(value is not None and 10 <= value <= 300 for value in (finite(oil.get("wti_spot_usd")), finite(oil.get("wti_future_proxy_usd")))), "WTI 現貨與期貨代理皆不可用")

    equities = macro.get("equities", {})
    for name in ("sp500", "nasdaq"):
        canonical = finite(equities.get(name, {}).get("value"))
        independent = finite(equities.get(f"{name}_independent_check", {}).get("value"))
        gap = relative_gap(canonical, independent)
        check(f"{name}_cross_source", gap is not None and gap <= 0.01, f"{name} 缺少獨立來源或差異超過 1%", hard=False)
        as_of_age = age_days(equities.get(name, {}).get("as_of"))
        check(f"{name}_as_of", as_of_age is not None and -1 <= as_of_age <= 7, f"{name} 超過 7 日新鮮度契約")
    vix = finite(equities.get("vix", {}).get("value"))
    check("vix_range", vix is not None and 5 <= vix <= 150, "VIX 超出合理範圍")
    vix_age = age_days(equities.get("vix", {}).get("as_of"))
    check("vix_as_of", vix_age is not None and -1 <= vix_age <= 7, "VIX 超過 7 日新鮮度契約")
    oil_dates = [oil.get("wti_spot_as_of"), oil.get("wti_future_proxy_as_of")]
    check("oil_as_of", any((age := age_days(value)) is not None and -1 <= age <= 14 for value in oil_dates), "WTI 現貨與期貨代理皆超過新鮮度契約")

    btc = source.get("onchain", {}).get("BTC", {})
    tx = finite(btc.get("transactions", {}).get("value"))
    tx_check = finite(btc.get("blockchair_transactions_24h"))
    hash_rate = finite(btc.get("hashrate", {}).get("value"))
    hash_check = finite(btc.get("mempool_hashrate_ths"))
    tx_gap = relative_gap(tx, tx_check)
    hash_gap = relative_gap(hash_rate, hash_check)
    check("btc_transaction_cross_source", tx_gap is not None and tx_gap <= 0.65 and abs(tx_gap - finite(btc.get("transactions_cross_source_gap"))) < 1e-9, "BTC 交易數跨來源驗證失敗")
    check("btc_hashrate_cross_source", hash_gap is not None and hash_gap <= 0.25 and abs(hash_gap - finite(btc.get("hashrate_cross_source_gap"))) < 1e-9, "BTC 算力跨來源驗證失敗")
    for name in ("transactions", "active_addresses", "hashrate"):
        onchain_age = age_days(btc.get(name, {}).get("as_of"))
        check(f"btc_{name}_as_of", onchain_age is not None and -1 <= onchain_age <= 3, f"BTC {name} 超過 3 日新鮮度契約")

    eth = source.get("onchain", {}).get("ETH", {})
    heads = [int(value) for value in eth.get("provider_heads", {}).values()]
    head_gap = max(heads) - min(heads) if heads else None
    check("eth_head_cross_source", len(heads) >= 2 and head_gap is not None and head_gap <= 3 and head_gap == eth.get("head_gap_blocks"), "ETH 至少需要兩個 RPC 且高度差不得超過 3", hard=False)
    check("eth_sample_agreement", eth.get("sample_agreement") is True, "ETH 抽樣區塊 hash 與交易數未通過雙 RPC 對帳", hard=False)
    current_sample = eth.get("current_sample", {})
    prior_sample = eth.get("prior_week_sample", {})
    check("eth_sample_depth", current_sample.get("sample_blocks", 0) >= 10 and prior_sample.get("sample_blocks", 0) >= 10, "ETH 目前與前週抽樣深度不足", hard=False)
    eth_age = age_days(eth.get("as_of"))
    check("eth_as_of", eth_age is not None and -1 <= eth_age <= 1, "ETH RPC 抽樣超過 1 日新鮮度契約")

    policy = source.get("policy", {})
    check("policy_sources", policy.get("successful_sources", 0) >= 3, "政策研究至少需要三個成功官方來源", hard=False)
    check("policy_event_contract", all(item.get("url", "").startswith("https://") and item.get("matched_terms") for item in policy.get("events", [])), "政策事件缺少官方連結或命中詞")

    expected_groups = {
        "macro": macro.get("status", "fail"),
        "policy": policy.get("status", "fail"),
        "onchain_btc": btc.get("status", "fail"),
        "onchain_eth": eth.get("status", "fail"),
    }
    check("group_status", quality.get("group_status") == expected_groups, "quality group_status 與實際研究桌不一致")
    usable_groups = sum(value in {"pass", "degraded"} for value in expected_groups.values())
    expected_quality = "pass" if all(value == "pass" for value in expected_groups.values()) and all(independent_pairs.values()) else "degraded" if usable_groups >= 3 else "fail"
    check("quality_status", quality.get("status") == expected_quality, f"market context quality 應為 {expected_quality}，不得假 PASS")

    expected_status = "fail" if failures else "degraded" if degradations or quality.get("status") == "degraded" else "pass"
    report = {
        "schema": 1,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_generated_at": source.get("generated_at"),
        "source_hash": source.get("payload_hash"),
        "status": expected_status,
        "failures": failures,
        "degradations": degradations,
        "checks": checks,
        "source_summary": {"passed": passed_sources, "failed": failed_sources, "total": len(source_checks)},
        "execution_gate_eligible": False,
    }
    return report


def main() -> int:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8-sig"))
    report = verify(source)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT_PATH), "status": report["status"], "failures": len(report["failures"]), "degradations": len(report["degradations"])}, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
