#!/usr/bin/env python3
"""Generate deterministic, source-bounded daily market editorial research."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
CONTEXT_PATH = DATA_DIR / "market_context.json"
CONTEXT_VERIFY_PATH = DATA_DIR / "market_context_verification.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
MARKET_VERIFY_PATH = DATA_DIR / "market_universe_verification.json"
TIMESCALE_PATH = DATA_DIR / "timescale_intelligence.json"
TIMESCALE_VERIFY_PATH = DATA_DIR / "timescale_intelligence_verification.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
KNOWLEDGE_PATH = DATA_DIR / "knowledge_context.json"
OUTPUT_PATH = DATA_DIR / "market_editorial.json"
HISTORY_PATH = DATA_DIR / "market_editorial_history.json"

DESK_ORDER = [
    "crypto-core",
    "institutional-flows",
    "policy",
    "onchain",
    "technical-positioning",
    "liquidity-fed-oil",
    "credit-bonds",
    "us-equities",
]

KNOWLEDGE_MAP = {
    "crypto-core": ["overview", "five-dimension-model"],
    "institutional-flows": ["mnav-reflexivity", "coin-per-share-accretion", "mstr", "bmnr"],
    "policy": ["data-feeds"],
    "onchain": ["indicator-regime-change", "btc-neutral-anchor"],
    "technical-positioning": ["five-dimension-model", "cycle-diminishing-returns"],
    "liquidity-fed-oil": ["btc-neutral-anchor"],
    "credit-bonds": ["btc-neutral-anchor"],
    "us-equities": ["delayed-pro-cyclical"],
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_payload(value: dict[str, Any], hash_field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != hash_field}


def signed_pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:+.{digits}%}"


def points(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:+.{digits}f} 個百分點"


def compact_usd(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    amount = abs(value)
    if amount >= 1e12:
        return f"{sign}${amount / 1e12:.2f}T"
    if amount >= 1e9:
        return f"{sign}${amount / 1e9:.2f}B"
    if amount >= 1e6:
        return f"{sign}${amount / 1e6:.0f}M"
    return f"{sign}${amount:,.0f}"


def compact_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.{digits}f}B"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.{digits}f}M"
    if abs(value) >= 1e3:
        return f"{value / 1e3:.{digits}f}K"
    return f"{value:.{digits}f}"


def direction(value: float | None, threshold: float, *, inverse: bool = False) -> str:
    if value is None or abs(value) < threshold:
        return "neutral"
    positive = value > 0
    if inverse:
        positive = not positive
    return "positive" if positive else "negative"


def evidence(
    metric_id: str,
    label: str,
    value: float | None,
    display: str,
    change: str,
    as_of: str | None,
    signal: str,
    source_count: int,
    sources: list[dict[str, str]],
    interpretation: str,
    dimension: str,
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "full_name": label,
        "value": value,
        "display": display,
        "change": change,
        "as_of": as_of,
        "direction": signal,
        "source_count": source_count,
        "sources": sources,
        "interpretation": interpretation,
        "dimension": dimension,
    }


def resonance(items: list[dict[str, Any]]) -> dict[str, Any]:
    directions = [item["direction"] for item in items if item.get("value") is not None]
    positive = directions.count("positive")
    negative = directions.count("negative")
    neutral = directions.count("neutral")
    if positive >= 3 and positive > negative:
        state = "偏正向共振"
        tone = "positive"
    elif negative >= 3 and negative > positive:
        state = "偏負向共振"
        tone = "negative"
    elif positive >= 2 and negative >= 2:
        state = "多維訊號分歧"
        tone = "mixed"
    else:
        state = "尚未形成共振"
        tone = "neutral"
    return {"state": state, "tone": tone, "positive": positive, "negative": negative, "neutral": neutral, "known": len(directions)}


def source(label: str, url: str) -> dict[str, str]:
    return {"label": label, "url": url}


def knowledge_links(desk_id: str, pages: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"slug": slug, "title": pages[slug]["title"], "url": f"wiki.html#{slug}"}
        for slug in KNOWLEDGE_MAP[desk_id]
        if slug in pages
    ]


def finish_brief(
    desk_id: str,
    title: str,
    headline: str,
    conclusion: str,
    common: str,
    variant: str,
    second_order: str,
    practical: str,
    falsifier: str,
    items: list[dict[str, Any]],
    pages: dict[str, dict[str, Any]],
    materiality: float,
) -> dict[str, Any]:
    valid = [item for item in items if item.get("value") is not None]
    dimensions = sorted({item["dimension"] for item in valid})
    status = "pass" if len(valid) >= 3 and len(dimensions) >= 3 else "degraded" if len(valid) >= 2 else "fail"
    result = resonance(valid)
    brief = {
        "id": desk_id,
        "title": title,
        "status": status,
        "confidence": "高" if status == "pass" and all(item.get("source_count", 0) >= 2 for item in valid[:3]) else "中" if status != "fail" else "低",
        "headline": headline,
        "conclusion": conclusion,
        "common_interpretation": common,
        "variant_view": variant,
        "second_order_effect": second_order,
        "practical_readthrough": practical,
        "falsifier": falsifier,
        "resonance": result,
        "evidence": items,
        "independent_dimensions": dimensions,
        "knowledge_links": knowledge_links(desk_id, pages),
        "materiality_score": round(materiality, 3),
        "editorial_scope": "deterministic_research_hypothesis_not_source_claim",
        "what_changed": "尚未與前一個不同日期比較。",
    }
    brief["brief_hash"] = canonical_hash(hash_payload(brief, "brief_hash"))
    return brief


def build_briefs(
    context: dict[str, Any],
    market: dict[str, Any],
    timescale: dict[str, Any],
    snapshot: dict[str, Any],
    knowledge: dict[str, Any],
) -> list[dict[str, Any]]:
    pages = {page["slug"]: page for page in knowledge.get("pages", [])}
    market_radar = snapshot.get("metrics", {}).get("market_radar", {})
    btc = market.get("assets", {}).get("BTC", {})
    eth = market.get("assets", {}).get("ETH", {})
    horizons = timescale.get("horizons", {})
    daily_return = finite(horizons.get("daily", {}).get("metrics", {}).get("btc_return"))
    weekly_return = finite(horizons.get("weekly", {}).get("metrics", {}).get("btc_return"))
    monthly_return = finite(horizons.get("monthly", {}).get("metrics", {}).get("btc_return"))
    quarterly_return = finite(horizons.get("quarterly", {}).get("metrics", {}).get("btc_return"))
    btc_change = finite(btc.get("change_24h"))
    eth_change = finite(eth.get("change_24h"))
    funding = finite(market.get("analysis", {}).get("BTC", {}).get("funding_annualized_median"))
    tracked = market.get("analysis", {}).get("breadth", {}).get("tracked_assets", 0)
    positive_assets = market.get("analysis", {}).get("breadth", {}).get("positive_assets", 0)

    if btc_change is not None and monthly_return is not None and btc_change < -0.02 < monthly_return:
        crypto_headline = "短線去風險，但月線修復尚未被推翻"
    elif btc_change is not None and monthly_return is not None and btc_change < 0 and monthly_return < 0:
        crypto_headline = "短線壓力正與中期弱勢同向"
    elif btc_change is not None and monthly_return is not None and btc_change > 0 and monthly_return > 0:
        crypto_headline = "現貨動能與月線方向同步改善"
    else:
        crypto_headline = "BTC 與 ETH 的短中期訊號仍在分化"
    crypto_items = [
        evidence("btc-24h-return", "比特幣 24 小時報酬", btc_change, signed_pct(btc_change), "相較昨日報價", btc.get("as_of"), direction(btc_change, 0.02), int(btc.get("source_count", 0)), [source("CoinGecko／OKX／Coinbase／Kraken", "market-monitor.html")], "短線風險偏好，不等於月線趨勢。", "價格"),
        evidence("eth-24h-return", "以太幣 24 小時報酬", eth_change, signed_pct(eth_change), "相較昨日報價", eth.get("as_of"), direction(eth_change, 0.02), int(eth.get("source_count", 0)), [source("CoinGecko／OKX／Coinbase／Kraken", "market-monitor.html")], "ETH 相對 BTC 的弱強可辨識風險偏好是否外溢。", "跨資產"),
        evidence("btc-monthly-return", "比特幣月線等長窗口報酬", monthly_return, signed_pct(monthly_return), horizons.get("monthly", {}).get("what_changed", ""), horizons.get("monthly", {}).get("data_depth", {}).get("as_of"), direction(monthly_return, 0.04), int(horizons.get("monthly", {}).get("data_depth", {}).get("source_count", 0)), [source("雙來源完成日 K", "analytics.html")], "月線用來區分反彈與中期趨勢，不以單日價格取代。", "趨勢"),
        evidence("btc-funding", "比特幣永續合約年化資金費率中位數", funding, signed_pct(funding), "跨可觀測交易所", market.get("derivatives", {}).get("BTC", {}).get("perpetual", {}).get("okx", {}).get("as_of"), direction(funding, 0.12, inverse=True), int(market.get("derivatives", {}).get("BTC", {}).get("perpetual", {}).get("funding_source_count", 0)), [source("OKX／Hyperliquid", "market-monitor.html")], "正費率代表多方付費，但低於擁擠門檻時不等於過熱。", "槓桿"),
    ]
    crypto = finish_brief(
        "crypto-core", "BTC／ETH 核心市場", crypto_headline,
        f"BTC 24 小時 {signed_pct(btc_change)}、ETH {signed_pct(eth_change)}；月線 BTC {signed_pct(monthly_return)}。先把短線去風險與中期結構分開。",
        "市場常把單日大跌直接解讀為趨勢反轉，或把單日反彈直接解讀為底部確認。",
        "本站更在意價格方向是否與槓桿成本、ETH 相對強弱及月線窗口同向；若資金費率沒有同步過熱，跌幅可能主要是去槓桿，而非長期論點瞬間消失。",
        "若 ETH 持續弱於 BTC，資金通常先收縮至流動性最深的資產；反之 ETH 轉強才較像風險偏好擴散。",
        "今天先判斷是全市場去風險、BTC 獨強，或 ETH 帶動的擴散，不用一根 K 線回答全部問題。",
        "若週線與月線報酬同時轉負、廣度惡化且資金費率仍居高不下，『只是短線去槓桿』假說失效。",
        crypto_items, pages, abs(btc_change or 0) * 20 + abs(eth_change or 0) * 12 + (1 if (btc_change or 0) * (monthly_return or 0) < 0 else 0.2),
    )

    btc_etf_7d = finite(market_radar.get("etf_flow_7d_usd"))
    eth_etf_7d = finite(market_radar.get("eth_etf_flow_7d_usd"))
    btc_dat = market.get("dat", {}).get("BTC", {})
    eth_dat = market.get("dat", {}).get("ETH", {})
    mstr = next((item for item in btc_dat.get("companies", []) if item.get("symbol") == "MSTR"), {})
    bmnr = next((item for item in eth_dat.get("companies", []) if item.get("symbol") == "BMNR"), {})
    flow_total = (btc_etf_7d or 0) + (eth_etf_7d or 0)
    institutional_headline = "機構買盤仍在，但吸收能力要由價格確認" if flow_total > 0 else "ETF 與財庫需求暫未形成一致增量"
    institution_items = [
        evidence("btc-etf-7d", "美國比特幣現貨 ETF 七日淨流", btc_etf_7d, compact_usd(btc_etf_7d), f"資料日 {market_radar.get('etf_flow_as_of')}", market_radar.get("etf_flow_as_of"), direction(btc_etf_7d, 100_000_000), int(market_radar.get("etf_flow_source_count") or 0), [source("ETF 多來源＋iShares 核對", "market-monitor.html")], "已驗證七日淨流描述邊際需求；不把流入直接等同價格上漲。", "ETF 流向"),
        evidence("eth-etf-7d", "美國以太幣現貨 ETF 七日淨流", eth_etf_7d, compact_usd(eth_etf_7d), f"資料日 {market_radar.get('eth_etf_flow_as_of')}", market_radar.get("eth_etf_flow_as_of"), direction(eth_etf_7d, 50_000_000), int(market_radar.get("eth_etf_flow_source_count") or 0), [source("ETF 多來源＋iShares 核對", "market-monitor.html")], "ETH ETF 是制度化需求旁證，也可能與現貨弱勢並存。", "ETF 流向"),
        evidence("btc-dat-share", "上市公司比特幣財庫占供給比", finite(btc_dat.get("supply_share")), signed_pct(finite(btc_dat.get("supply_share"))), f"MSTR 本週持幣變化 {compact_number(finite(mstr.get('holdings_change')), 0)} BTC", mstr.get("as_of") or btc_dat.get("as_of"), direction(finite(mstr.get("holdings_change")), 1), int(btc_dat.get("source_count", 0)), [source("SEC＋DAT 多來源", "market-monitor.html")], "存量占比是結構採用，不等於每日買盤。", "公司財庫"),
        evidence("eth-dat-share", "上市公司以太幣財庫占供給比", finite(eth_dat.get("supply_share")), signed_pct(finite(eth_dat.get("supply_share"))), f"BMNR 本週持幣變化 {compact_number(finite(bmnr.get('holdings_change')), 0)} ETH", bmnr.get("as_of") or eth_dat.get("as_of"), direction(finite(bmnr.get("holdings_change")), 1), int(eth_dat.get("source_count", 0)), [source("SEC＋DAT 多來源", "market-monitor.html")], "ETH 財庫集中度高，須同時看公司負債與稀釋。", "公司財庫"),
        evidence("institutional-price-absorption", "ETF 流入期間的比特幣短線價格反應", btc_change, signed_pct(btc_change), f"七日 ETF 淨流 {compact_usd(btc_etf_7d)}", btc.get("as_of"), "positive" if (btc_etf_7d or 0) > 0 and (btc_change or 0) > 0 else "negative" if (btc_etf_7d or 0) > 0 and (btc_change or 0) < 0 else "neutral", int(btc.get("source_count", 0)), [source("ETF 驗證流量＋四來源現貨", "market-monitor.html")], "流入而價格下跌代表可觀測買盤未完全吸收賣壓；不是 ETF 數據失效。", "價格吸收"),
    ]
    institution = finish_brief(
        "institutional-flows", "ETF／DAT 機構流向", institutional_headline,
        f"BTC 與 ETH ETF 七日合計 {compact_usd(flow_total)}；MSTR、BMNR 的官方持幣變化分別為 {compact_number(finite(mstr.get('holdings_change')), 0)} BTC 與 {compact_number(finite(bmnr.get('holdings_change')), 0)} ETH。",
        "常見解讀是 ETF 流入與公司增持等於價格下檔有保證。",
        "本站把 ETF 流量、DAT 存量與價格反應拆開：若資金流入但價格仍弱，代表未被觀測的賣壓更大，反而是重要警訊。",
        "ETF 是贖回速度快的流量，DAT 是資本結構驅動的存量；兩者同向才是制度化需求共振，不能混成一個數字。",
        "今天先看新增需求是否真的被價格吸收，再看公司持幣是否伴隨稀釋、負債或優先股成本。",
        "若七日 ETF 流轉負、DAT 未增持且價格跌破中期結構，『機構吸收』假說失效。",
        institution_items, pages, abs(flow_total) / 1e9 + (1 if flow_total > 0 and (btc_change or 0) < 0 else 0),
    )

    policy = context.get("policy", {})
    event_7d = finite(policy.get("event_count_7d"))
    event_30d = finite(policy.get("event_count_30d"))
    policy_events = policy.get("events", [])
    latest_event = policy_events[0] if policy_events else {}
    official_rule_count = sum(item.get("source_type") == "official_rulemaking" for item in policy_events)
    legislation_count = sum(item.get("source_type") == "official_legislation" for item in policy_events)
    policy_headline = "政策催化正在增加，但文件效力比消息數量重要" if (event_7d or 0) > 0 else "近期沒有新增命中，不代表監管風險消失"
    policy_items = [
        evidence("policy-events-7d", "七日加密政策官方事件數", event_7d, compact_number(event_7d, 0), f"30 日共 {compact_number(event_30d, 0)} 件", context.get("generated_at"), direction(event_7d, 1), int(policy.get("successful_sources", 0)), [source("Congress.gov／Federal Register／SEC 等", "market-intelligence.html#policy")], "事件數只表示政策注意力，需再區分法案、正式規則與新聞稿。", "政策動能"),
        evidence("policy-rulemaking", "正式規則與擬議規則命中數", float(official_rule_count), compact_number(float(official_rule_count), 0), "近 40 筆官方命中內", latest_event.get("published_at"), direction(float(official_rule_count), 1), 1, [source("Federal Register", "https://www.federalregister.gov")], "規則制定比一般評論更接近可執行的監管變化。", "法規效力"),
        evidence("policy-legislation", "國會法案命中數", float(legislation_count), compact_number(float(legislation_count), 0), "依最新更新排序", latest_event.get("published_at"), direction(float(legislation_count), 1), 1, [source("Congress.gov", "https://www.congress.gov")], "法案更新不等於通過；仍需追蹤最新 action 與生效日。", "立法進程"),
    ]
    policy_brief = finish_brief(
        "policy", "加密法案與監管政策", policy_headline,
        f"近七日命中 {compact_number(event_7d, 0)} 件、三十日 {compact_number(event_30d, 0)} 件官方事件；最新事件為「{latest_event.get('title', '近期無新增事件')}」。",
        "市場常把法案提出、監管機構發言與正式生效規則放在同一層解讀。",
        "本站只把正式法律、已生效規則視為制度改變；提案與新聞稿是領先訊號，必須等待 action、effective date 或執法落地驗證。",
        "政策利多可能先反映在交易所、穩定幣與代幣化資產，再傳導至 BTC／ETH；政策利空也常先透過流動性與市場准入發生。",
        "今天先辨識文件效力，再看受影響的是資產本身、交易通路、銀行託管或 ETF 配套。",
        "若後續 action 停滯、規則被撤回或法院推翻，政策催化假說失效。",
        policy_items, pages, (event_7d or 0) * 0.15 + (0.3 if official_rule_count else 0),
    )

    btc_chain = context.get("onchain", {}).get("BTC", {})
    eth_chain = context.get("onchain", {}).get("ETH", {})
    mvrv = finite(market_radar.get("btc_mvrv_current"))
    btc_tx_change = finite(btc_chain.get("transactions", {}).get("change_30d"))
    btc_active_change = finite(btc_chain.get("active_addresses", {}).get("change_30d"))
    btc_hash_change = finite(btc_chain.get("hashrate", {}).get("change_30d"))
    eth_tx_change = finite(eth_chain.get("transactions_per_block_7d_change"))
    onchain_headline = "估值降溫，但鏈上活動尚未全面共振" if mvrv is not None and mvrv < 1.5 else "鏈上使用與估值訊號仍需分開驗證"
    onchain_items = [
        evidence("btc-mvrv", "比特幣市值對實現價值比率", mvrv, f"{mvrv:.2f}x" if mvrv is not None else "—", "低於 1.5x 為偏冷估值背景", snapshot.get("date"), direction((1.5 - mvrv) if mvrv is not None else None, 0.15), 1, [source("Coin Metrics Community", "wiki.html#indicator-regime-change")], "MVRV 描述成本基礎位置，不是單獨的底部觸發。", "估值"),
        evidence("btc-transactions", "比特幣每日確認交易數 30 日變化", btc_tx_change, signed_pct(btc_tx_change), compact_number(finite(btc_chain.get("transactions", {}).get("value")), 0), btc_chain.get("transactions", {}).get("as_of"), direction(btc_tx_change, 0.08), 2, [source("Blockchain.com／Blockchair", "https://www.blockchain.com/explorer/charts/n-transactions")], "交易數與活躍地址用來驗證使用強度，不直接對應幣價。", "網路使用"),
        evidence("btc-active-addresses", "比特幣活躍地址 30 日變化", btc_active_change, signed_pct(btc_active_change), compact_number(finite(btc_chain.get("active_addresses", {}).get("value")), 0), btc_chain.get("active_addresses", {}).get("as_of"), direction(btc_active_change, 0.08), 1, [source("Blockchain.com", "https://www.blockchain.com/explorer/charts/n-unique-addresses")], "地址數可能受批次交易與地址重用影響，只作使用代理。", "使用廣度"),
        evidence("btc-hashrate", "比特幣算力 30 日變化", btc_hash_change, signed_pct(btc_hash_change), compact_number(finite(btc_chain.get("hashrate", {}).get("value")), 1) + " TH/s", btc_chain.get("hashrate", {}).get("as_of"), direction(btc_hash_change, 0.08), 2, [source("Blockchain.com／mempool.space", "https://mempool.space")], "算力反映安全投入，短期下降也可能是難度與能源經濟調整。", "網路安全"),
        evidence("eth-tx-block", "以太坊每區塊交易數七日變化", eth_tx_change, signed_pct(eth_tx_change), compact_number(finite(eth_chain.get("current_sample", {}).get("average_transactions_per_block")), 1) + " 筆/區塊", eth_chain.get("as_of"), direction(eth_tx_change, 0.08), int(eth_chain.get("source_count", 0)), [source("PublicNode／1RPC／Flashbots", "https://ethereum.org/developers/docs/apis/json-rpc/")], "以雙 RPC 區塊抽樣觀察活動，不把抽樣估計冒充全日總量。", "ETH 活動"),
    ]
    onchain = finish_brief(
        "onchain", "BTC／ETH 鏈上數據", onchain_headline,
        f"BTC MVRV {mvrv:.2f}x；交易數 30 日 {signed_pct(btc_tx_change)}、活躍地址 {signed_pct(btc_active_change)}、算力 {signed_pct(btc_hash_change)}；ETH 每區塊交易數七日 {signed_pct(eth_tx_change)}。" if mvrv is not None else "鏈上估值資料不足，僅保留已驗證活動數據。",
        "常見解讀是 MVRV 偏低就等於價格已到底，或鏈上活動增加就等於需求即將推升價格。",
        "本站要求估值便宜、使用活動與安全投入至少兩類同向；便宜但使用收縮，可能只是價格先反映更弱的需求。",
        "BTC 與 ETH 的活動若同時轉弱，會先降低手續費與結算需求，再影響礦工、驗證者與應用層收入。",
        "今天把『便宜』和『正在被使用』分開看；只有兩者共振，鏈上訊號才比價格單獨更有說服力。",
        "若 MVRV 回升但交易、地址與 ETH 活動持續下降，『鏈上修復』假說失效。",
        onchain_items, pages, abs(btc_tx_change or 0) + abs(btc_hash_change or 0) + abs(eth_tx_change or 0),
    )

    breadth_ratio = positive_assets / tracked if tracked else None
    technical_headline = "日線反彈、週線承壓，廣度仍未確認" if (daily_return or 0) > 0 > (weekly_return or 0) else "趨勢、廣度與槓桿正在重新定價"
    technical_items = [
        evidence("btc-daily-window", "比特幣日線等長窗口報酬", daily_return, signed_pct(daily_return), horizons.get("daily", {}).get("status", ""), horizons.get("daily", {}).get("data_depth", {}).get("as_of"), direction(daily_return, 0.02), int(horizons.get("daily", {}).get("data_depth", {}).get("source_count", 0)), [source("雙來源完成日 K", "analytics.html")], "領先觀察短期動能。", "動能"),
        evidence("btc-weekly-window", "比特幣週線等長窗口報酬", weekly_return, signed_pct(weekly_return), horizons.get("weekly", {}).get("status", ""), horizons.get("weekly", {}).get("data_depth", {}).get("as_of"), direction(weekly_return, 0.04), int(horizons.get("weekly", {}).get("data_depth", {}).get("source_count", 0)), [source("雙來源完成日 K", "analytics.html")], "用來驗證日線反彈是否升級。", "趨勢"),
        evidence("crypto-breadth", "追蹤加密資產上漲廣度", breadth_ratio, f"{positive_assets}/{tracked}", "24 小時正報酬資產", market.get("generated_at"), direction((breadth_ratio or 0) - 0.5, 0.2), 2, [source("固定資產清單多來源", "market-monitor.html")], "廣度確認行情是否只集中在少數資產。", "籌碼廣度"),
        evidence("btc-perp-funding", "比特幣永續合約年化資金費率中位數", funding, signed_pct(funding), market.get("analysis", {}).get("BTC", {}).get("leverage_temperature", ""), market.get("generated_at"), direction(funding, 0.12, inverse=True), int(market.get("derivatives", {}).get("BTC", {}).get("perpetual", {}).get("funding_source_count", 0)), [source("OKX／Hyperliquid", "market-monitor.html")], "用來辨識趨勢背後是否伴隨擁擠槓桿。", "槓桿籌碼"),
    ]
    technical = finish_brief(
        "technical-positioning", "動能、技術與籌碼", technical_headline,
        f"BTC 日線 {signed_pct(daily_return)}、週線 {signed_pct(weekly_return)}、月線 {signed_pct(monthly_return)}、季線 {signed_pct(quarterly_return)}；24 小時廣度 {positive_assets}/{tracked}。",
        "常見技術分析會挑選單一週期或單一均線給出方向。",
        "本站要求價格趨勢、跨資產廣度與槓桿溫度三維共振；日線翻多但週線、廣度未確認，只能稱為候選修復。",
        "若價格上漲卻廣度收窄，資金容易集中於 BTC；若 funding 同時升高，回撤對槓桿部位的破壞力會放大。",
        "今天先看短線訊號能否升級到週線，再用廣度與 funding 驗證，不反過來拿情緒替價格作答。",
        "若週線轉正、廣度超過一半且 funding 維持非擁擠，『只是候選修復』將被推翻並升級。",
        technical_items, pages, abs((daily_return or 0) - (weekly_return or 0)) * 8 + (1 - (breadth_ratio or 0)),
    )

    macro = context.get("macro", {})
    liquidity = macro.get("liquidity", {})
    equities = macro.get("equities", {})
    oil = macro.get("oil", {})
    net_liquidity = finite(liquidity.get("net_liquidity_million_usd"))
    net_change = finite(liquidity.get("net_liquidity_30d_change"))
    fed_funds = finite(macro.get("rates", {}).get("fed_funds_pct"))
    dollar_change = finite(equities.get("broad_dollar", {}).get("change_30d"))
    oil_change = finite(oil.get("wti_spot_30d_change"))
    if oil_change is None:
        oil_change = finite(oil.get("wti_future_proxy_30d_change"))
    oil_source_count = sum(finite(value) is not None for value in (oil.get("wti_spot_usd"), oil.get("wti_future_proxy_usd")))
    liquidity_headline = "流動性與美元尚未給出單邊答案" if direction(net_change, 0.01) != direction(dollar_change, 0.01, inverse=True) else "美元與系統流動性正在形成同向壓力"
    liquidity_items = [
        evidence("net-liquidity", "聯準會資產減財政部現金與隔夜逆回購", net_liquidity, compact_usd(net_liquidity * 1e6 if net_liquidity is not None else None), signed_pct(net_change) + "／30 日", liquidity.get("as_of"), direction(net_change, 0.01), 3, [source("Fed H.4.1／NY Fed／Treasury", "market-intelligence.html#liquidity-fed-oil")], "跨頻率合成，保留最慢組件 as_of；不是即時可交易流動性。", "美元流動性"),
        evidence("fed-funds", "有效聯邦資金利率", fed_funds, f"{fed_funds:.2f}%" if fed_funds is not None else "—", "政策資金成本", macro.get("rates", {}).get("fed_funds_as_of"), direction((4.0 - fed_funds) if fed_funds is not None else None, 0.25), 1, [source("Federal Reserve via FRED", "https://fred.stlouisfed.org/series/DFF")], "利率決定無收益資產的機會成本。", "聯準會"),
        evidence("broad-dollar", "廣義美元指數 30 日變化", dollar_change, signed_pct(dollar_change), compact_number(finite(equities.get("broad_dollar", {}).get("value")), 1), equities.get("broad_dollar", {}).get("as_of"), direction(dollar_change, 0.01, inverse=True), 1, [source("Federal Reserve via FRED", "https://fred.stlouisfed.org/series/DTWEXBGS")], "美元走強通常收緊全球金融條件。", "美元"),
        evidence("wti-oil", "西德州原油 30 日變化", oil_change, signed_pct(oil_change), f"${finite(oil.get('wti_spot_usd')):.2f}/桶" if finite(oil.get("wti_spot_usd")) is not None else "期貨代理", oil.get("wti_spot_as_of") or oil.get("wti_future_proxy_as_of"), direction(oil_change, 0.08, inverse=True), oil_source_count, [source("EIA via FRED／CL 期貨代理", "https://fred.stlouisfed.org/series/DCOILWTICO")], "油價上行可能抬高通膨尾部風險，延後寬鬆預期。", "通膨"),
    ]
    liquidity_brief = finish_brief(
        "liquidity-fed-oil", "宏觀流動性、聯準會與原油", liquidity_headline,
        f"淨流動性約 {compact_usd(net_liquidity * 1e6 if net_liquidity is not None else None)}、30 日 {signed_pct(net_change)}；美元 {signed_pct(dollar_change)}、WTI {signed_pct(oil_change)}。",
        "常見解讀是聯準會資產增加就必然利多 BTC，或油價上升就必然利空風險資產。",
        "本站同時看 Fed 資產、TGA、逆回購、美元與油價；只有資金供給、融資成本與通膨約束同向，才稱為宏觀共振。",
        "油價上升若推高通膨預期，可能透過債券殖利率與美元再次收緊流動性，而不是直接作用在 BTC。",
        "今天先看流動性是否真的進入市場，再看美元和油價是否抵銷這個效果。",
        "若淨流動性持續增加、美元轉弱且油價壓力回落，『宏觀仍分歧』假說失效。",
        liquidity_items, pages, abs(net_change or 0) * 10 + abs(dollar_change or 0) * 10 + abs(oil_change or 0) * 3,
    )

    credit = macro.get("credit", {})
    rates = macro.get("rates", {})
    hy_oas = finite(credit.get("high_yield_oas_pct"))
    hy_change = finite(credit.get("high_yield_oas_30d_change_pp"))
    ig_change = finite(credit.get("investment_grade_oas_30d_change_pp"))
    yield_10y = finite(rates.get("treasury_10y_pct"))
    curve_2s10s = finite(rates.get("curve_2s10s_pp"))
    treasury_source_count = sum(finite(value) is not None for value in (rates.get("direct_values", {}).get("treasury_10y_pct"), rates.get("fred_values", {}).get("treasury_10y_pct")))
    credit_source_count = 1 + int(finite(credit.get("fallback_proxy", {}).get("value")) is not None)
    credit_headline = "信用利差尚穩，但長端利率仍是估值壓力" if (hy_change or 0) < 0.25 and (yield_10y or 0) > 4 else "信用與債券市場開始同向重定價"
    credit_items = [
        evidence("hy-oas", "美國高收益債選擇權調整利差", hy_oas, f"{hy_oas:.2f}%" if hy_oas is not None else "—", points(hy_change), credit.get("as_of"), direction(hy_change, 0.25, inverse=True), credit_source_count, [source("ICE BofA via FRED／HYG-IEF 代理", "https://fred.stlouisfed.org/series/BAMLH0A0HYM2")], "利差擴大代表信用風險升溫；HYG/IEF 只作備援方向代理。", "信用風險"),
        evidence("ig-oas", "美國投資級債選擇權調整利差", finite(credit.get("investment_grade_oas_pct")), f"{finite(credit.get('investment_grade_oas_pct')):.2f}%" if finite(credit.get("investment_grade_oas_pct")) is not None else "—", points(ig_change), credit.get("as_of"), direction(ig_change, 0.12, inverse=True), 1, [source("ICE BofA via FRED", "https://fred.stlouisfed.org/series/BAMLC0A0CM")], "投資級利差可辨識壓力是否從高收益債外溢。", "信用廣度"),
        evidence("treasury-10y", "美國十年期公債殖利率", yield_10y, f"{yield_10y:.2f}%" if yield_10y is not None else "—", "無現金流資產機會成本", rates.get("as_of"), direction((4.5 - yield_10y) if yield_10y is not None else None, 0.25), treasury_source_count, [source("U.S. Treasury／FRED", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates")], "殖利率越高，長久期與無現金流資產估值壓力越大。", "利率"),
        evidence("curve-2s10s", "美債二年與十年期利差", curve_2s10s, points(curve_2s10s), "正值代表曲線正斜率", rates.get("as_of"), direction(curve_2s10s, 0.25), treasury_source_count, [source("U.S. Treasury／FRED", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates")], "曲線轉正可能來自降息預期，也可能來自長端期限溢價上升。", "殖利率曲線"),
    ]
    credit_brief = finish_brief(
        "credit-bonds", "信用利差與債券市場", credit_headline,
        f"高收益債利差 {hy_oas:.2f}%、30 日 {points(hy_change)}；10 年殖利率 {yield_10y:.2f}%、2s10s {points(curve_2s10s)}。" if hy_oas is not None and yield_10y is not None else "信用或債券核心數據不足。",
        "常見解讀是利差沒擴大就代表風險資產安全，或曲線轉正就代表降息利多。",
        "本站把信用風險與無風險利率拆開：利差平穩只能說違約風險未惡化，不能抵銷高長端利率對估值的壓力。",
        "若長端殖利率上升而信用利差不動，壓力先打在估值；若兩者同時上升，才是融資與風險偏好雙重收緊。",
        "今天判斷市場是在交易通膨／期限溢價，還是在交易信用事件，兩者對 BTC 與美股的傳導不同。",
        "若 10 年殖利率回落、利差保持穩定且曲線由政策端帶動轉陡，『長端估值壓力』假說失效。",
        credit_items, pages, abs(hy_change or 0) * 3 + abs(ig_change or 0) * 4 + max((yield_10y or 0) - 4, 0),
    )

    sp500 = equities.get("sp500", {})
    nasdaq = equities.get("nasdaq", {})
    vix = equities.get("vix", {})
    sp_change = finite(sp500.get("change_30d"))
    nasdaq_change = finite(nasdaq.get("change_30d"))
    vix_value = finite(vix.get("value"))
    btc_30d = finite(market_radar.get("btc_return_30d_pct"))
    sp500_source_count = sum(finite(item.get("value")) is not None for item in (sp500, equities.get("sp500_independent_check", {})))
    nasdaq_source_count = sum(finite(item.get("value")) is not None for item in (nasdaq, equities.get("nasdaq_independent_check", {})))
    vix_source_count = sum(finite(item.get("value")) is not None for item in (vix, equities.get("vix_independent_check", {})))
    btc_return_source_count = int(horizons.get("monthly", {}).get("data_depth", {}).get("source_count", 0))
    equities_headline = "大型股指數表面平穩，但科技與加密風險偏好分化" if (sp_change or 0) > (nasdaq_change or 0) else "美股與加密風險偏好正在重新同步"
    equity_items = [
        evidence("sp500-30d", "標普 500 指數 30 日報酬", sp_change, signed_pct(sp_change), compact_number(finite(sp500.get("value")), 1), sp500.get("as_of"), direction(sp_change, 0.03), sp500_source_count, [source("FRED／Yahoo", "https://fred.stlouisfed.org/series/SP500")], "大型股風險偏好的基準。", "大型股"),
        evidence("nasdaq-30d", "那斯達克綜合指數 30 日報酬", nasdaq_change, signed_pct(nasdaq_change), compact_number(finite(nasdaq.get("value")), 1), nasdaq.get("as_of"), direction(nasdaq_change, 0.03), nasdaq_source_count, [source("FRED／Yahoo", "https://fred.stlouisfed.org/series/NASDAQCOM")], "成長股與長久期風險偏好代理。", "科技股"),
        evidence("vix-level", "芝加哥選擇權交易所波動率指數", vix_value, f"{vix_value:.1f}" if vix_value is not None else "—", signed_pct(finite(vix.get("change_30d"))) + "／30 日", vix.get("as_of"), direction((20 - vix_value) if vix_value is not None else None, 3), vix_source_count, [source("CBOE via FRED／Yahoo", "https://fred.stlouisfed.org/series/VIXCLS")], "VIX 描述避險定價，不是方向預測器。", "波動率"),
        evidence("btc-30d-relative", "比特幣 30 日報酬", btc_30d, signed_pct(btc_30d), f"相對標普差 {signed_pct((btc_30d - sp_change) if btc_30d is not None and sp_change is not None else None)}", snapshot.get("date"), direction(btc_30d, 0.08), btc_return_source_count, [source("雙來源 BTC 日 K", "analytics.html")], "用來判斷 BTC 是高 beta 風險資產，或正在走獨立路徑。", "跨市場"),
    ]
    equity_brief = finish_brief(
        "us-equities", "美股市場與風險偏好", equities_headline,
        f"標普 500 近 30 日 {signed_pct(sp_change)}、那斯達克 {signed_pct(nasdaq_change)}、VIX {vix_value:.1f}；BTC 同期 {signed_pct(btc_30d)}。" if vix_value is not None else "美股波動率資料不足。",
        "常見解讀是美股上漲就會帶動 BTC，或 VIX 下降就等於所有風險資產安全。",
        "本站觀察標普、科技股、VIX 與 BTC 的相對路徑；只有方向、波動與跨市場相對強弱同向，才叫風險偏好共振。",
        "若 BTC 強於科技股，可能是資金在交易加密特有催化；若兩者同跌，宏觀流動性通常比幣圈敘事更重要。",
        "今天先辨識 BTC 是跟隨美股 beta，還是走獨立 alpha，再決定該追哪一組證據。",
        "若標普、那斯達克、VIX 與 BTC 同時回到一致方向，『跨市場分化』假說失效。",
        equity_items, pages, abs((sp_change or 0) - (nasdaq_change or 0)) * 10 + abs((btc_30d or 0) - (sp_change or 0)) * 4,
    )

    return [crypto, institution, policy_brief, onchain, technical, liquidity_brief, credit_brief, equity_brief]


def previous_different_date(history: dict[str, Any], current_date: str) -> dict[str, Any] | None:
    return next((run for run in reversed(history.get("runs", [])) if run.get("date") != current_date), None)


def apply_changes(briefs: list[dict[str, Any]], previous: dict[str, Any] | None) -> None:
    prior = {item["id"]: item for item in (previous or {}).get("briefs", [])}
    for brief in briefs:
        previous_brief = prior.get(brief["id"])
        if not previous_brief:
            brief["what_changed"] = "建立第一個可比較基線；不製造不存在的跨日故事。"
        elif previous_brief.get("resonance_state") != brief["resonance"]["state"]:
            brief["what_changed"] = f"多維狀態由「{previous_brief.get('resonance_state')}」轉為「{brief['resonance']['state']}」。"
        elif previous_brief.get("headline") != brief["headline"]:
            brief["what_changed"] = f"狀態仍為「{brief['resonance']['state']}」，但核心矛盾改為：{brief['headline']}。"
        else:
            brief["what_changed"] = f"核心判斷未變，仍為「{brief['resonance']['state']}」；今日只更新證據值與 as_of。"
        brief["brief_hash"] = canonical_hash(hash_payload(brief, "brief_hash"))


def compact_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return {
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


def run_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in run.items() if key != "run_hash"}


def validate_history(history: dict[str, Any]) -> None:
    previous_hash = None
    for run in history.get("runs", []):
        if run.get("previous_run_hash") != previous_hash:
            raise ValueError("market editorial history chain broken")
        if run.get("run_hash") != canonical_hash(run_payload(run)):
            raise ValueError("market editorial history hash invalid")
        previous_hash = run["run_hash"]


def main() -> int:
    context = load(CONTEXT_PATH)
    context_verify = load(CONTEXT_VERIFY_PATH)
    market = load(MARKET_PATH)
    market_verify = load(MARKET_VERIFY_PATH)
    timescale = load(TIMESCALE_PATH)
    timescale_verify = load(TIMESCALE_VERIFY_PATH)
    snapshot = load(SNAPSHOT_PATH)
    knowledge = load(KNOWLEDGE_PATH)
    history = load(HISTORY_PATH) if HISTORY_PATH.exists() else {"schema": 1, "runs": []}
    validate_history(history)

    hard_errors: list[str] = []
    if context_verify.get("source_generated_at") != context.get("generated_at") or context_verify.get("source_hash") != context.get("payload_hash"):
        hard_errors.append("market context verifier 未綁定目前 artifact hash")
    if context_verify.get("status") == "fail" or market_verify.get("status") == "fail" or timescale_verify.get("status") == "fail":
        hard_errors.append("至少一個必要資料 verifier 失敗")
    if market_verify.get("market_generated_at") != market.get("generated_at"):
        hard_errors.append("market universe verifier 批次不一致")
    if timescale_verify.get("analysis_generated_at") != timescale.get("generated_at"):
        hard_errors.append("timescale verifier 批次不一致")
    required_slugs = {slug for slugs in KNOWLEDGE_MAP.values() for slug in slugs}
    available_slugs = {page.get("slug") for page in knowledge.get("pages", [])}
    if not required_slugs.issubset(available_slugs):
        hard_errors.append("LLM Wiki knowledge context 缺少市場總編必要頁面")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    current_date = context.get("date") or generated_at[:10]
    briefs = [] if hard_errors else build_briefs(context, market, timescale, snapshot, knowledge)
    previous = previous_different_date(history, current_date)
    apply_changes(briefs, previous)
    valid_briefs = [brief for brief in briefs if brief["status"] != "fail"]
    lead = max(valid_briefs, key=lambda item: item["materiality_score"], default=None)
    positive = sum(brief["resonance"]["tone"] == "positive" for brief in valid_briefs)
    negative = sum(brief["resonance"]["tone"] == "negative" for brief in valid_briefs)
    mixed = sum(brief["resonance"]["tone"] == "mixed" for brief in valid_briefs)
    upstream_statuses = {
        "market_context": context.get("quality", {}).get("status"),
        "market_context_verifier": context_verify.get("status"),
        "market_universe": market.get("quality", {}).get("status"),
        "market_universe_verifier": market_verify.get("status"),
        "timescale": timescale.get("quality", {}).get("status"),
        "timescale_verifier": timescale_verify.get("status"),
    }
    upstream_degradations = [f"{name}={value}" for name, value in upstream_statuses.items() if value == "degraded"]
    status = "fail" if hard_errors or len(valid_briefs) < 6 or lead is None else "degraded" if len(valid_briefs) < len(DESK_ORDER) or any(brief["status"] == "degraded" for brief in briefs) or upstream_degradations else "pass"
    publication_mode = "diagnostics_only" if status == "fail" else "analysis_only"
    digest = {
        "status": status,
        "lead_desk_id": lead["id"] if lead else None,
        "lead_headline": lead["headline"] if lead else "必要研究資料未通過",
        "lead_conclusion": lead["conclusion"] if lead else "只發布紅燈診斷，不沿用上一期結論。",
        "dominant_tension": lead["variant_view"] if lead else None,
        "falsifier": lead["falsifier"] if lead else None,
        "desk_count": len(briefs),
        "resonance_summary": {"positive": positive, "negative": negative, "mixed": mixed, "known": len(valid_briefs)},
        "source_health": {
            "successful": context.get("quality", {}).get("successful_sources", 0),
            "failed_with_fallback": context.get("quality", {}).get("failed_sources", 0),
            "total": context.get("quality", {}).get("successful_sources", 0) + context.get("quality", {}).get("failed_sources", 0),
        },
        "method": "八個研究桌以已驗證市場、宏觀、政策、鏈上與 LLM Wiki 定義做確定性綜合；來源只支持原始數字，不背書本站假說",
    }
    output = {
        "schema": 1,
        "date": current_date,
        "generated_at": generated_at,
        "quality": {
            "status": status,
            "publication_mode": publication_mode,
            "execution_gate_eligible": False,
            "failures": hard_errors,
            "degradations": list(dict.fromkeys(context_verify.get("degradations", []) + upstream_degradations)),
            "upstream_statuses": upstream_statuses,
        },
        "lineage": {
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
        },
        "editorial_digest": digest,
        "desks": briefs if publication_mode == "analysis_only" else [],
    }
    output["editorial_hash"] = canonical_hash(hash_payload(output, "editorial_hash"))

    if publication_mode == "analysis_only":
        prior_hash = history.get("runs", [])[-1]["run_hash"] if history.get("runs") else None
        same_day = [run for run in history.get("runs", []) if run.get("date") == current_date]
        run = {
            "date": current_date,
            "generated_at": generated_at,
            "revision": len(same_day) + 1,
            "supersedes_generated_at": same_day[-1]["generated_at"] if same_day else None,
            "previous_run_hash": prior_hash,
            "editorial_hash": output["editorial_hash"],
            "lead_desk_id": digest["lead_desk_id"],
            "briefs": [compact_brief(brief) for brief in briefs],
        }
        run["run_hash"] = canonical_hash(run_payload(run))
        history.setdefault("runs", []).append(run)
        if len(history["runs"]) > 5000:
            raise ValueError("market editorial history exceeds migration threshold")
        history.update({"schema": 1, "updated_at": generated_at, "head_hash": run["run_hash"]})

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT_PATH), "status": status, "desks": len(briefs), "lead": digest["lead_desk_id"]}, ensure_ascii=False))
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
