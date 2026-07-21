#!/usr/bin/env python3
"""Build decision-first institutional analytics from the verified daily snapshot.

The browser renders this file; it does not invent investment conclusions. Every
decision card therefore carries lenses, leading evidence, lagging confirmation,
confidence, and traceable source references.
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
KNOWLEDGE_PATH = DATA_DIR / "knowledge_context.json"


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        return None if math.isnan(parsed) or math.isinf(parsed) else parsed
    except (TypeError, ValueError):
        return None


def pct_change(current: Any, previous: Any) -> float | None:
    current_value = number(current)
    previous_value = number(previous)
    if current_value is None or previous_value in (None, 0):
        return None
    return current_value / previous_value - 1


def latest_previous(database: dict[str, Any], current_date: str) -> dict[str, Any] | None:
    snapshots = [item for item in database.get("snapshots", []) if item.get("date") != current_date]
    snapshots.sort(key=lambda item: item.get("date", ""))
    return snapshots[-1] if snapshots else None


def record_advantage(database: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshots = sorted(database.get("snapshots", []), key=lambda item: item.get("date", ""))
    current_version = snapshot.get("metrics", {}).get("btc_standard", {}).get("model_version")
    comparable = [
        item for item in snapshots
        if item.get("metrics", {}).get("btc_standard", {}).get("model_version") == current_version
    ]
    dates = [item.get("date") for item in snapshots if item.get("date")]
    return {
        "historical_snapshots": len(snapshots),
        "date_range": [dates[0], dates[-1]] if dates else [],
        "previous_observation_date": latest_previous(database, snapshot.get("date", ""))["date"] if latest_previous(database, snapshot.get("date", "")) else None,
        "current_model_version": current_version or "legacy_unversioned",
        "comparable_model_observations": len(comparable),
        "uses": [
            "計算前次價格變化與 MSTR 相對 BTC 動能",
            "保存每日驗證、資料降級與決策狀態供追溯",
            "只在模型版本一致時比較市場體制分數",
            "用 Wiki 驗證日期與 stale_days 防止過期假說冒充今日事實",
        ],
        "limits": [
            "目前同版本模型觀察數不足，禁止宣稱已完成回測或具有統計顯著性",
            "股票收盤與 BTC 24/7 現貨時間基準不同，相對日報酬只作先行觀察",
        ],
    }


def fmt_money(value: Any, decimals: int = 0) -> str:
    parsed = number(value)
    return "資料不足" if parsed is None else f"${parsed:,.{decimals}f}"


def fmt_pct(value: Any, decimals: int = 1) -> str:
    parsed = number(value)
    return "資料不足" if parsed is None else f"{parsed * 100:.{decimals}f}%"


def fmt_multiple(value: Any, decimals: int = 2) -> str:
    parsed = number(value)
    return "資料不足" if parsed is None else f"{parsed:.{decimals}f}x"


def quality_score(verification: dict[str, Any], provenance: dict[str, Any], expected_date: str | None = None) -> int:
    status = verification.get("status")
    if expected_date and verification.get("date") != expected_date:
        return 0
    if status not in {"pass", "degraded", "fail"}:
        return 0
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


def confidence_label(score: int) -> str:
    if score >= 85:
        return "高"
    if score >= 70:
        return "中"
    if score >= 50:
        return "中低"
    return "低"


def lens(name: str, direction: str, value: str, read: str, independence_key: str | None = None) -> dict[str, str]:
    return {
        "name": name,
        "direction": direction,
        "value": value,
        "read": read,
        "independence_key": independence_key or name,
    }


def indicator(name: str, value: str, state: str, read: str) -> dict[str, str]:
    return {"name": name, "value": value, "state": state, "read": read}


def dominant_resonance(lenses: list[dict[str, str]]) -> tuple[int, int, str, str]:
    independent: dict[str, str] = {}
    for item in lenses:
        if item["direction"] != "unknown":
            independent.setdefault(item.get("independence_key", item["name"]), item["direction"])
    known = list(independent.values())
    if not known:
        return 0, 0, "資料不足", "unknown"
    counts = {direction: known.count(direction) for direction in {"bullish", "bearish", "neutral"}}
    count = max(counts.values())
    winners = [direction for direction, votes in counts.items() if votes == count and votes > 0]
    if len(winners) != 1 or count < 2:
        return count, len(known), "訊號分歧", "mixed"
    direction = winners[0]
    labels = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}
    status = f"{count}/{len(known)} {labels[direction]}共振"
    return count, len(known), status, direction


def source(path: str, label: str, as_of: str | None = None, tier: str = "internal_derived") -> dict[str, Any]:
    return {"label": label, "path": path, "as_of": as_of, "tier": tier}


def knowledge_refs(knowledge: dict[str, Any], slugs: list[str]) -> list[dict[str, Any]]:
    nodes = knowledge.get("nodes") or knowledge.get("pages") or knowledge.get("items") or []
    refs: list[dict[str, Any]] = []
    for slug in slugs:
        match = next((node for node in nodes if node.get("slug") == slug), None)
        if match:
            refs.append({
                "slug": slug,
                "title": match.get("title", slug),
                "path": match.get("source_path") or match.get("path"),
                "confidence": match.get("effective_confidence") or match.get("confidence", "未知"),
                "quality_flags": match.get("quality_flags", []),
            })
        else:
            refs.append({"slug": slug, "title": slug, "path": None, "confidence": "未編譯", "quality_flags": ["missing_from_knowledge_context"]})
    return refs


def usable_knowledge_page(knowledge: dict[str, Any], slug: str) -> dict[str, Any] | None:
    pages = knowledge.get("pages") or knowledge.get("nodes") or []
    return next(
        (page for page in pages if page.get("slug") == slug and page.get("analysis_use") == "context_only"),
        None,
    )


def mstr_common_nav_per_share(snapshot: dict[str, Any], btc_price: float) -> float | None:
    inputs = snapshot.get("metrics", {}).get("manual_inputs", {})
    required = {
        "holdings": number(inputs.get("mstr_btc_holdings")),
        "shares": number(inputs.get("common_shares_outstanding_m")),
        "cash": number(inputs.get("usd_reserve_musd")),
        "other_cash": number(inputs.get("cash_other_musd")),
        "debt": number(inputs.get("debt_face_musd")),
        "deferred_tax": number(inputs.get("deferred_tax_liability_musd")),
    }
    preferred_items = inputs.get("preferred")
    if any(value is None for value in required.values()) or not required["shares"] or not isinstance(preferred_items, dict) or not preferred_items:
        return None
    preferred_values = [number(item.get("notional_musd")) for item in preferred_items.values()]
    if any(value is None for value in preferred_values):
        return None
    preferred = sum(value for value in preferred_values if value is not None)
    net_to_common = (
        required["holdings"] * btc_price / 1_000_000
        + required["cash"]
        + required["other_cash"]
        - required["debt"]
        - preferred
        - required["deferred_tax"]
    )
    return net_to_common / required["shares"] if net_to_common > 0 else None


def bottom_assessment(snapshot: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot.get("metrics", {})
    prices = metrics.get("prices", {})
    radar = metrics.get("market_radar", {})
    btc_standard = metrics.get("btc_standard", {})
    btc = number(prices.get("btc_usd"))
    ma50 = number(radar.get("btc_50dma"))
    ma200 = number(radar.get("btc_200dma"))
    ma200w = number(radar.get("btc_200wma"))
    mvrv = number(radar.get("btc_mvrv_current"))
    fear = number(radar.get("fear_greed"))
    drawdown = number(radar.get("btc_drawdown_1y_pct"))

    confirmations = [
        indicator("站上 50 日均線", fmt_money(ma50), "pass" if btc is not None and ma50 is not None and btc >= ma50 else "fail", "短中期趨勢先止跌"),
        indicator("站上 200 日均線", fmt_money(ma200), "pass" if btc is not None and ma200 is not None and btc >= ma200 else "fail", "主要右側趨勢確認"),
        indicator("至少 45 天未創新低", "尚未自動化", "unknown", "用時間排除短暫反彈；資料未接入前不得計票"),
        indicator("週線形成較高低點", "尚未自動化", "unknown", "用完成週 K 驗證價格結構；資料未接入前不得計票"),
    ]
    confirmed = sum(item["state"] == "pass" for item in confirmations)
    target_page = usable_knowledge_page(knowledge, "target-price")
    decision_inputs = set(target_page.get("decision_inputs", [])) if target_page else set()
    hypothesis_authorized = {
        "btc_bottom_hypothesis_40000_52000",
        "requires_price_structure_confirmation",
        "never_confirm_on_touch_alone",
    }.issubset(decision_inputs)
    hypothesis_low = 40_000.0 if hypothesis_authorized else None
    hypothesis_high = 52_000.0 if hypothesis_authorized else None
    hypothesis_touched = btc is not None and hypothesis_high is not None and btc <= hypothesis_high
    near_200w = btc is not None and ma200w is not None and abs(btc / ma200w - 1) <= 0.08
    status = "knowledge_blocked" if not hypothesis_authorized else "candidate_not_confirmed" if near_200w else "not_confirmed"
    headline = "知識假說未通過治理，停止底部區判讀" if status == "knowledge_blocked" else "接近長期支撐，但底部尚未確認" if near_200w else "底部尚未確認"
    return {
        "status": status,
        "key_number": "已確認" if status == "confirmed" else "未確認",
        "headline": headline,
        "plain_read": (
            f"BTC 現價 {fmt_money(btc)}，距 200 週均線 {fmt_pct(btc / ma200w - 1) if btc is not None and ma200w else '資料不足'}；"
            f"右側確認 {confirmed}/{len(confirmations)}，研究假說區 "
            f"{'$40,000–$52,000 ' + ('已觸及' if hypothesis_touched else '尚未觸及') if hypothesis_authorized else '因知識治理未通過而停用'}。"
        ),
        "current_price": btc,
        "support_200wma": ma200w,
        "research_hypothesis": {
            "lower": hypothesis_low,
            "upper": hypothesis_high,
            "status": "blocked" if not hypothesis_authorized else "touched" if hypothesis_touched else "not_touched",
            "distance_to_upper": btc / hypothesis_high - 1 if btc is not None and hypothesis_high is not None else None,
            "classification": "未回測研究假說，不是保證目標",
            "knowledge_authorized": hypothesis_authorized,
        },
        "leading_indicators": [
            indicator("MVRV 比率", fmt_multiple(mvrv), "cold" if mvrv is not None and mvrv <= 1.3 else "neutral", "鏈上估值偏冷"),
            indicator("恐懼貪婪指數", "資料不足" if fear is None else f"{fear:.0f}", "cold" if fear is not None and fear <= 25 else "neutral", "恐懼可形成機會，但不能單獨抄底"),
            indicator("距一年高點回撤", fmt_pct(drawdown), "cold" if drawdown is not None and drawdown <= -0.45 else "neutral", "週期回撤已深"),
        ],
        "lagging_confirmations": confirmations,
        "next_trigger": f"先站回 200 日均線 {fmt_money(ma200)}，再取得至少 45 天未創新低與週線較高低點；目前 {confirmed}/4，單一條件通過不得宣告見底。",
        "model_status": btc_standard.get("model_status", "unknown"),
        "model_version": btc_standard.get("model_version", "legacy_unversioned"),
        "wiki_refs": knowledge_refs(knowledge, ["target-price", "indicator-regime-change", "two-tranche-plan"]),
        "source_refs": [
            source("data/daily/latest_snapshot.json", "每日已驗證快照", snapshot.get("date")),
            source("wiki/concepts/target-price.md", "底部區研究假說", "2026-07-21", "internal_hypothesis"),
        ],
    }


def signal_card(
    *,
    signal_id: str,
    title: str,
    key_number: str,
    plain_read: str,
    lenses: list[dict[str, str]],
    leading: list[dict[str, str]],
    lagging: list[dict[str, str]],
    next_trigger: str,
    confidence: str,
    wiki_refs_value: list[dict[str, Any]],
    source_refs_value: list[dict[str, Any]],
    hard_gate: dict[str, str] | None = None,
) -> dict[str, Any]:
    count, total, status, direction = dominant_resonance(lenses)
    if hard_gate and hard_gate.get("status") != "pass":
        count, status, direction = 0, hard_gate.get("read", "共振遭硬閘門封鎖"), "unknown"
    return {
        "id": signal_id,
        "title": title,
        "key_number": key_number,
        "plain_read": plain_read,
        "lenses": lenses,
        "resonance_count": count,
        "resonance_total": total,
        "resonance_status": status,
        "dominant_direction": direction,
        "hard_gate": hard_gate,
        "leading_indicators": leading,
        "lagging_confirmations": lagging,
        "next_trigger": next_trigger,
        "confidence": confidence,
        "wiki_refs": wiki_refs_value,
        "source_refs": source_refs_value,
    }


def build_exclusive_signals(
    snapshot: dict[str, Any], previous: dict[str, Any] | None, knowledge: dict[str, Any], confidence: str
) -> list[dict[str, Any]]:
    metrics = snapshot.get("metrics", {})
    prices = metrics.get("prices", {})
    mstr = metrics.get("mstr_metrics", {})
    bmnr = metrics.get("bmnr_metrics", {})
    previous_metrics = previous.get("metrics", {}) if previous else {}
    previous_prices = previous_metrics.get("prices", {})
    common_ratio = number(mstr.get("common_equity_price_to_nav") or mstr.get("equity_mnav"))
    enterprise_ratio = number(mstr.get("enterprise_value_to_btc_nav") or mstr.get("enterprise_mnav"))
    strc_discount = number(mstr.get("strc_discount"))
    sale_ratio = number(mstr.get("sale_ratio"))
    mstr_return = pct_change(prices.get("mstr_usd"), previous_prices.get("mstr_usd"))
    btc_return = pct_change(prices.get("btc_usd"), previous_prices.get("btc_usd"))
    relative_return = None if mstr_return is None or btc_return is None else mstr_return - btc_return

    mstr_lenses = [
        lens("普通股估值", "bullish" if common_ratio is not None and common_ratio <= 1 else "bearish" if common_ratio is not None else "unknown", fmt_multiple(common_ratio), "低於 1.0x 才是普通股折價；目前高於 1.0x 代表溢價"),
        lens("優先股信任", "bullish" if strc_discount is not None and strc_discount <= 0.05 else "bearish" if strc_discount is not None else "unknown", fmt_pct(strc_discount), "STRC 折價超過 5%，表示資本市場要求更高風險補償"),
        lens("融資飛輪", "bullish" if enterprise_ratio is not None and enterprise_ratio >= 1 else "bearish" if enterprise_ratio is not None else "unknown", fmt_multiple(enterprise_ratio), "企業價值高於 BTC 總值時，增發換幣的反身性仍可能運作；不等於普通股便宜"),
        lens("相對動能", "bullish" if relative_return is not None and relative_return > 0 else "bearish" if relative_return is not None else "unknown", fmt_pct(relative_return), "單日跑贏 BTC 只能當先行動能，不能抵銷資本結構紅燈"),
    ]
    mstr_signal = signal_card(
        signal_id="mstr_valuation_trust_split",
        title="MSTR 普通股估值與融資信任分離",
        key_number=fmt_multiple(common_ratio),
        plain_read=f"普通股相對自算淨值為 {fmt_multiple(common_ratio)}，但 STRC 仍折價 {fmt_pct(strc_discount)}；目前不是『普通股便宜＋融資市場信任』的雙重共振。",
        lenses=mstr_lenses,
        leading=[
            indicator("MSTR 相對 BTC 日報酬", fmt_pct(relative_return), "improving" if relative_return is not None and relative_return > 0 else "weak", "股價反身性先行"),
            indicator("企業價值／BTC 總值", fmt_multiple(enterprise_ratio), "open" if enterprise_ratio is not None and enterprise_ratio >= 1 else "closed", "融資飛輪背景，不是便宜度"),
        ],
        lagging=[
            indicator("普通股市值／普通股淨值 ≤1.0x", fmt_multiple(common_ratio), "pass" if common_ratio is not None and common_ratio <= 1 else "fail", "估值安全邊際"),
            indicator("STRC 折價 ≤5%", fmt_pct(strc_discount), "pass" if strc_discount is not None and strc_discount <= 0.05 else "fail", "優先股市場信任"),
            indicator("7 日賣幣壓力可觀測且 ≤2x", fmt_multiple(sale_ratio), "unknown" if sale_ratio is None else "pass" if sale_ratio <= 2 else "fail", "固定義務現金壓力"),
        ],
        next_trigger="普通股市值／普通股淨值降至 1.0x 以下、STRC 折價收斂至 5% 以下，且 7 日賣幣資料恢復可觀測，才重新評估 2.5x 合約。",
        confidence=confidence,
        wiki_refs_value=knowledge_refs(knowledge, ["gaap-vs-mnav", "mnav-definition-risk", "delayed-pro-cyclical", "strc-preferred"]),
        source_refs_value=[source("data/daily/latest_snapshot.json", "MSTR 自算指標", snapshot.get("date")), source("data/inputs/mstr_capital_structure_provenance.json", "資本結構來源")],
    )

    bmnr_ratio = number(bmnr.get("market_cap_to_gross_treasury"))
    bmnr_staked = number(bmnr.get("staked_eth_ratio"))
    bmnr_gap = number(bmnr.get("reported_total_crosscheck_gap"))
    bmnr_return = pct_change(prices.get("bmnr_usd"), previous_prices.get("bmnr_usd"))
    bmnr_lenses = [
        lens("資產折溢價", "bullish" if bmnr_ratio is not None and bmnr_ratio < 1 else "bearish" if bmnr_ratio is not None else "unknown", fmt_multiple(bmnr_ratio), "低於 1.0x 是相對 gross treasury 折價，不是淨 NAV 折價"),
        lens("持倉交叉驗證", "bullish" if bmnr_gap is not None and bmnr_gap <= 0.05 else "bearish" if bmnr_gap is not None else "unknown", fmt_pct(bmnr_gap), "由持倉逐項重算與管理層總額差距"),
        lens("質押流動性", "neutral" if bmnr_staked is not None else "unknown", fmt_pct(bmnr_staked), "高質押率兼具收益與解鎖、驗證者及流動性風險"),
        lens("價格動能", "bullish" if bmnr_return is not None and bmnr_return > 0 else "bearish" if bmnr_return is not None else "unknown", fmt_pct(bmnr_return), "單日動能只作先行觀察"),
    ]
    bmnr_signal = signal_card(
        signal_id="bmnr_gross_treasury_discount",
        title="BMNR gross treasury 折價與持倉品質",
        key_number=fmt_multiple(bmnr_ratio),
        plain_read=f"市值約為 gross treasury 的 {fmt_multiple(bmnr_ratio)}；有資產折價背景，但尚未扣完整負債與潛在稀釋，不能稱為淨 NAV 安全邊際。",
        lenses=bmnr_lenses,
        leading=[
            indicator("每千股 ETH", "資料不足" if number(bmnr.get("eth_per_1000_shares")) is None else f"{number(bmnr.get('eth_per_1000_shares')):.2f}", "watch", "監測回購或增發後是否真正增厚"),
            indicator("BMNR 日報酬", fmt_pct(bmnr_return), "improving" if bmnr_return is not None and bmnr_return > 0 else "weak", "市場先行動能"),
        ],
        lagging=[
            indicator("持倉重算差距 ≤5%", fmt_pct(bmnr_gap), "pass" if bmnr_gap is not None and bmnr_gap <= 0.05 else "fail", "管理層總額交叉驗證"),
            indicator("完整負債與稀釋解析", "尚未完成", "unknown", "未完成前只准用 gross-assets 視角"),
        ],
        next_trigger="下一份 SEC 文件更新股數、負債與潛在稀釋後，重算每千股 ETH 與淨額；若每千股 ETH 下滑，即使折價仍需降權。",
        confidence="低" if confidence == "低" else "中低",
        wiki_refs_value=knowledge_refs(knowledge, ["bmnr", "coin-per-share-accretion"]),
        source_refs_value=[source("data/daily/latest_snapshot.json", "BMNR 持倉與股數重算", snapshot.get("date"), "official_plus_derived")],
        hard_gate={"status": "blocked", "read": "共振封鎖：完整負債與稀釋尚未解析"},
    )
    return [mstr_signal, bmnr_signal]


def build_consensus_signals(
    snapshot: dict[str, Any], bottom: dict[str, Any], knowledge: dict[str, Any], confidence: str
) -> list[dict[str, Any]]:
    metrics = snapshot.get("metrics", {})
    prices = metrics.get("prices", {})
    radar = metrics.get("market_radar", {})
    btc_standard = metrics.get("btc_standard", {})
    btc = number(prices.get("btc_usd"))
    ma50 = number(radar.get("btc_50dma"))
    ma200 = number(radar.get("btc_200dma"))
    ma200w = number(radar.get("btc_200wma"))
    mvrv = number(radar.get("btc_mvrv_current"))
    fear = number(radar.get("fear_greed"))
    drawdown = number(radar.get("btc_drawdown_1y_pct"))
    etf_flow = number(radar.get("etf_flow_7d_usd"))
    treasury = number(radar.get("treasury_avg_bill_rate_pct"))
    confirmation_items = bottom.get("lagging_confirmations", [])
    confirmed = sum(item.get("state") == "pass" for item in confirmation_items)

    bottom_lenses = [
        lens("技術趨勢", "neutral" if btc is not None and ma50 is not None and btc >= ma50 and ma200 is not None and btc < ma200 else "bullish" if btc is not None and ma200 is not None and btc >= ma200 else "bearish", f"50日 {fmt_money(ma50)}／200日 {fmt_money(ma200)}", "已站上 50 日線但仍低於 200 日線，屬止跌未翻多"),
        lens("鏈上估值", "bullish" if mvrv is not None and mvrv <= 1.3 else "neutral" if mvrv is not None else "unknown", fmt_multiple(mvrv), "MVRV 偏冷，但尚非單獨見底證據"),
        lens("市場情緒", "bullish" if fear is not None and fear <= 25 else "neutral" if fear is not None else "unknown", "資料不足" if fear is None else f"{fear:.0f}", "極度恐懼提供逆向背景，不是買點"),
        lens("週期回撤", "bullish" if drawdown is not None and drawdown <= -0.45 else "neutral" if drawdown is not None else "unknown", fmt_pct(drawdown), "回撤深度接近歷史壓力區，但週期樣本有限"),
    ]
    bottom_signal = signal_card(
        signal_id="btc_bottom_confirmation",
        title="BTC 底部候選與右側確認",
        key_number=f"{confirmed}/4",
        plain_read=f"偏冷領先訊號已有多維共振，但右側確認只有 {confirmed}/4；未站回 200 日均線前，不把反彈寫成底部完成。",
        lenses=bottom_lenses,
        leading=bottom.get("leading_indicators", []),
        lagging=confirmation_items,
        next_trigger=bottom.get("next_trigger", "等待右側確認"),
        confidence=confidence,
        wiki_refs_value=bottom.get("wiki_refs", []),
        source_refs_value=bottom.get("source_refs", []),
    )

    liquidity_lenses = [
        lens("ETF 邊際買盤", "bullish" if etf_flow is not None and etf_flow > 0 else "bearish" if etf_flow is not None else "unknown", fmt_money(etf_flow), "7 日淨流為正，但只有第三方單一來源，權重固定 0.5"),
        lens("利率環境", "bearish" if treasury is not None and treasury > 4.5 else "neutral" if treasury is not None else "unknown", "資料不足" if treasury is None else f"{treasury:.2f}%", "高於 4.5% 才額外壓低估值容忍度"),
        lens("中期趨勢", "bullish" if btc is not None and ma200 is not None and btc >= ma200 else "bearish" if btc is not None and ma200 is not None else "unknown", fmt_pct(btc / ma200 - 1) if btc is not None and ma200 else "資料不足", "200 日均線是落後但必要的趨勢確認"),
        lens("長期支撐", "bullish" if btc is not None and ma200w is not None and btc >= ma200w else "bearish" if btc is not None and ma200w is not None else "unknown", fmt_pct(btc / ma200w - 1) if btc is not None and ma200w else "資料不足", "現價與 200 週均線距離可衡量長期支撐壓力"),
    ]
    liquidity_signal = signal_card(
        signal_id="btc_liquidity_trend",
        title="資金流與長短期趨勢",
        key_number=fmt_pct(btc / ma200 - 1) if btc is not None and ma200 else "資料不足",
        plain_read=f"BTC 距 200 日均線 {fmt_pct(btc / ma200 - 1) if btc is not None and ma200 else '資料不足'}，距 200 週均線 {fmt_pct(btc / ma200w - 1) if btc is not None and ma200w else '資料不足'}；長期支撐靠近，但中期趨勢尚未翻多。",
        lenses=liquidity_lenses,
        leading=[indicator("ETF 7 日淨流", fmt_money(etf_flow), "background_only", "單源資料只作背景，不計確認票")],
        lagging=[indicator("站回 200 日均線", fmt_money(ma200), "pass" if btc is not None and ma200 is not None and btc >= ma200 else "fail", "趨勢確認")],
        next_trigger=f"價格站穩 {fmt_money(ma200)} 且 ETF 流取得第二來源交叉驗證，才把流動性與趨勢調升為有效共振。",
        confidence="低" if confidence == "低" else "中低",
        wiki_refs_value=knowledge_refs(knowledge, ["data-feeds", "indicator-regime-change", "five-dimension-model"]),
        source_refs_value=[source("data/daily/latest_snapshot.json", "BTC 市場雷達", snapshot.get("date")), source("data/daily/agent_verification_report.json", "獨立資料驗證", snapshot.get("date"))],
    )
    return [bottom_signal, liquidity_signal]


def conditional_targets(snapshot: dict[str, Any], bottom: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot.get("metrics", {})
    prices = metrics.get("prices", {})
    radar = metrics.get("market_radar", {})
    bmnr = metrics.get("bmnr_metrics", {})
    btc = number(prices.get("btc_usd"))
    hypothesis = bottom.get("research_hypothesis", {})
    lower = number(hypothesis.get("lower"))
    upper = number(hypothesis.get("upper"))
    authorized = hypothesis.get("knowledge_authorized") is True and lower is not None and upper is not None
    mstr_low = mstr_common_nav_per_share(snapshot, lower) if authorized else None
    mstr_high = mstr_common_nav_per_share(snapshot, upper) if authorized else None
    mstr_current = mstr_common_nav_per_share(snapshot, btc) if btc is not None else None
    bmnr_gross = number(bmnr.get("gross_treasury_value_per_share"))
    return {
        "classification": "條件式估值，不是單點預測或保證報酬",
        "btc": {
            "research_hypothesis_range": [lower, upper] if authorized else [],
            "status": hypothesis.get("status"),
            "current_price": btc,
            "confirmation_level_200dma": number(radar.get("btc_200dma")),
            "plain_read": f"$40,000–$52,000 是未回測底部假說；現價 {fmt_money(btc)} 尚未觸及。{fmt_money(radar.get('btc_200dma'))} 是右側確認門檻，不是獲利目標。" if authorized else "底部假說的知識節點未通過治理；停止輸出區間。",
        },
        "mstr": {
            "common_nav_per_share_at_btc_lower": mstr_low,
            "common_nav_per_share_at_btc_upper": mstr_high,
            "common_nav_per_share_at_current_btc": mstr_current,
            "plain_read": f"若 BTC 落在假說區，按目前股數、現金、債務、優先股與已揭露遞延稅負債靜態估算，MSTR 普通股淨值約 {fmt_money(mstr_low)}–{fmt_money(mstr_high)}／股；不含未來 ATM、稅額變動與其他資產。" if authorized else "底部假說未獲知識治理授權，停止換算 MSTR 情境。",
        },
        "bmnr": {
            "gross_treasury_value_per_share": bmnr_gross,
            "indicative_0_8x_to_1_0x": [bmnr_gross * 0.8 if bmnr_gross is not None else None, bmnr_gross],
            "plain_read": f"目前 gross treasury 約 {fmt_money(bmnr_gross, 2)}／股；0.8–1.0x 僅為 {fmt_money(bmnr_gross * 0.8, 2) if bmnr_gross is not None else '資料不足'}–{fmt_money(bmnr_gross, 2)} 的資產倍率參考，未扣完整負債，不能稱目標價。",
        },
        "assumptions": [
            "MSTR 採目前普通股股數、BTC 持倉、現金、債務、優先股清算面額與已揭露遞延稅負債靜態計算。",
            "MSTR 未納入未來 ATM、可轉債稀釋、BTC 稅務基礎變動及未驗證其他資產。",
            "BMNR 只提供 gross-assets 指示值，因完整負債與潛在稀釋尚未自動解析。",
        ],
    }


def build_summary_cards(
    snapshot: dict[str, Any], verification: dict[str, Any], quality: int, bottom: dict[str, Any], targets: dict[str, Any], logic_audit: dict[str, Any]
) -> list[dict[str, Any]]:
    metrics = snapshot.get("metrics", {})
    mstr = metrics.get("mstr_metrics", {})
    logic_safe = logic_audit.get("status") == "consistent"
    contract_blocked = mstr.get("contract_red_light") is not False or not logic_safe
    btc_target = targets.get("btc", {}).get("research_hypothesis_range", [])
    return [
        {
            "id": "today_action",
            "label": "今日動作",
            "key_number": "MSTR 合約禁開" if contract_blocked else "只列觀察",
            "plain_read": "邏輯稽核未通過，全部交易封鎖。" if not logic_safe else "STRC 折價、賣幣可觀測性與估值閘門尚未同時通過；BTC 偏冷不等於 2.5x 合約可開。" if contract_blocked else "載具紅燈暫時解除，仍需 MSTR/BTC 右側確認與風險額度。",
            "tone": "bad" if contract_blocked else "warn",
        },
        {
            "id": "bottom_status",
            "label": "BTC 底部",
            "key_number": bottom.get("key_number", "資料不足"),
            "plain_read": bottom.get("headline", "資料不足"),
            "tone": "good" if bottom.get("status") == "confirmed" else "warn",
        },
        {
            "id": "conditional_target",
            "label": "條件式估值",
            "key_number": f"${btc_target[0] / 1000:.0f}k–${btc_target[1] / 1000:.0f}k" if len(btc_target) == 2 else "已停用",
            "plain_read": "這是尚未觸及、未回測的 BTC 底部研究假說，不是已確認底或保證目標。",
            "tone": "info",
        },
        {
            "id": "data_quality",
            "label": "資料可信度",
            "key_number": f"{quality}/100",
            "plain_read": "邏輯稽核未通過；停止所有行動結論。" if not logic_safe else "驗證失敗時只供研究；不沿用舊綠燈，也不把缺值當零。" if verification.get("status") != "pass" else "獨立驗證已通過，仍須遵守模型與資料來源限制。",
            "tone": "bad" if not logic_safe or verification.get("status") in {"fail", "stale_or_mismatched"} else "warn" if verification.get("status") == "degraded" else "good",
        },
    ]


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    database = load_json(DATABASE_PATH, {"snapshots": []})
    verification = load_json(VERIFY_PATH)
    logic_audit = load_json(LOGIC_AUDIT_PATH, {})
    knowledge = load_json(KNOWLEDGE_PATH, {"schema": 0, "nodes": [], "quality": {"status": "missing"}})
    previous = latest_previous(database, snapshot["date"])
    verification_current = (
        verification.get("date") == snapshot.get("date")
        and verification.get("snapshot_generated_at") == snapshot.get("generated_at")
    )
    effective_verification = dict(verification)
    if not verification_current:
        effective_verification["status"] = "stale_or_mismatched"
        effective_verification.setdefault("degradations", []).append("驗證報告日期與每日快照不一致")
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    score = quality_score(effective_verification, provenance, snapshot.get("date"))
    confidence = confidence_label(score)
    bottom = bottom_assessment(snapshot, knowledge)
    bottom["decision_grade"] = "research_only" if effective_verification.get("status") != "pass" or bottom.get("model_status") != "validated" else "decision_support"
    targets = conditional_targets(snapshot, bottom)
    exclusive = build_exclusive_signals(snapshot, previous, knowledge, confidence)
    consensus = build_consensus_signals(snapshot, bottom, knowledge, confidence)
    knowledge_quality = knowledge.get("quality", {})

    analytics = {
        "schema": 2,
        "date": snapshot["date"],
        "generated_at": now_iso(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "quality": {
            "verification_status": effective_verification.get("status"),
            "verification_date_matches_snapshot": verification.get("date") == snapshot.get("date"),
            "verification_bound_to_snapshot": verification_current,
            "confidence": confidence,
            "confidence_score_0_100": score,
            "degradations": effective_verification.get("degradations", []),
            "warnings": effective_verification.get("warnings", []),
            "knowledge_status": knowledge.get("status", "missing"),
            "knowledge_nodes": knowledge_quality.get("context_only_pages", 0),
            "knowledge_excluded_nodes": knowledge_quality.get("excluded_pages", 0),
            "logic_audit_status": logic_audit.get("status", "not_run"),
            "logic_failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "logic_contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
        "decision_brief": {
            "summary_cards": build_summary_cards(snapshot, effective_verification, score, bottom, targets, logic_audit),
            "bottom_assessment": bottom,
            "conditional_targets": targets,
            "exclusive_signals": exclusive,
            "consensus_signals": consensus,
            "record_advantage": record_advantage(database, snapshot),
            "method": {
                "minimum_lenses": 3,
                "resonance_rule": "同方向至少兩個已知視角才稱共振；未知不計票；共振不等於交易放行。",
                "leading_vs_lagging": "領先指標用來形成候選情境；落後確認用來放行或否定情境。",
                "fail_closed": True,
            },
        },
        "logic_audit": {
            "status": logic_audit.get("status", "not_run"),
            "plain_english": logic_audit.get("decision", {}).get("plain_english", "邏輯稽核尚未執行"),
            "blocked_actions": logic_audit.get("decision", {}).get("blocked_actions", []),
            "failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
    }
    write_json(ANALYTICS_PATH, analytics)
    print(json.dumps({"analytics": str(ANALYTICS_PATH), "confidence": confidence, "schema": 2}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
