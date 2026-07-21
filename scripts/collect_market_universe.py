#!/usr/bin/env python3
"""Collect cross-asset spot, derivatives, sector, ETF, and DAT market data.

All sources are public and require no API key. Exchange observations remain
venue-specific; partial exchange coverage is never labeled as the whole market.
"""

from __future__ import annotations

import json
import math
import csv
import io
import re
import statistics
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
OUTPUT_PATH = DATA_DIR / "market_universe.json"
HISTORY_PATH = DATA_DIR / "market_universe_history.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
USER_AGENT = "mstr-btc-bottom-report/market-universe hsin73@realtek.com"
TROY_OZ_PER_METRIC_TONNE = 32_150.746568627

ASSETS = {
    "BTC": {"coingecko": "bitcoin", "binance": "BTCUSDT", "coinbase": "BTC-USD"},
    "ETH": {"coingecko": "ethereum", "binance": "ETHUSDT", "coinbase": "ETH-USD"},
    "HYPE": {"coingecko": "hyperliquid", "coinbase": "HYPE-USD", "hyperliquid": "HYPE"},
    "SOL": {"coingecko": "solana", "binance": "SOLUSDT", "coinbase": "SOL-USD"},
    "BNB": {"coingecko": "binancecoin", "binance": "BNBUSDT"},
    "XRP": {"coingecko": "ripple", "binance": "XRPUSDT", "coinbase": "XRP-USD"},
    "DOGE": {"coingecko": "dogecoin", "binance": "DOGEUSDT", "coinbase": "DOGE-USD"},
}
STRUCTURAL_COLLECTOR_NAMES = {"thesis_credit", "thesis_gold", "thesis_hashrate", "thesis_sovereign"}

SECTORS = {
    "RWA": "real-world-assets-rwa",
    "Layer 1": "layer-1",
    "DeFi": "decentralized-finance-defi",
    "Meme": "meme-token",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def age_hours(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    except ValueError:
        return None


def millis_iso(value: Any) -> str | None:
    timestamp = finite(value)
    return datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat() if timestamp is not None else None


def readable_source_error(provider: str, exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code == 451:
        return f"{provider} 在此自動化執行環境受地區限制（HTTP 451），已切換備援來源"
    if code == 403:
        return f"{provider} 拒絕此自動化執行環境存取（HTTP 403），已切換備援來源"
    return f"{provider} 暫時無法取得（{type(exc).__name__}），已切換備援來源"


def finite(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return None if math.isnan(parsed) or math.isinf(parsed) else parsed
    except (TypeError, ValueError):
        return None


def fetch_json(url: str, *, data: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, *, timeout: int = 25) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,text/html,*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source(
    source_id: str,
    provider: str,
    url: str,
    tier: str,
    as_of: str | None,
    detail: str,
    as_of_basis: str = "provider_timestamp",
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provider": provider,
        "url": url,
        "source_tier": tier,
        "as_of": as_of,
        "as_of_basis": as_of_basis,
        "fetched_at": now_iso(),
        "detail": detail,
    }


def collect_coingecko() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ids = ",".join(item["coingecko"] for item in ASSETS.values())
    url = (
        "https://api.coingecko.com/api/v3/simple/price?"
        + urllib.parse.urlencode({
            "ids": ids,
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        })
    )
    payload = fetch_json(url)
    result: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for symbol, config in ASSETS.items():
        row = payload.get(config["coingecko"], {})
        updated = datetime.fromtimestamp(row["last_updated_at"], timezone.utc).isoformat() if row.get("last_updated_at") else None
        result[symbol] = {
            "price_usd": finite(row.get("usd")),
            "change_24h": (finite(row.get("usd_24h_change")) or 0) / 100 if row.get("usd_24h_change") is not None else None,
            "market_cap_usd": finite(row.get("usd_market_cap")),
            "volume_24h_usd": finite(row.get("usd_24h_vol")),
            "as_of": updated,
        }
        sources.append(source(f"coingecko_{symbol.lower()}", "CoinGecko", url, "independent_market_aggregator", updated, "spot, market cap, 24h volume and change"))
    return result, sources


def collect_binance_spot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    symbols = [config["binance"] for config in ASSETS.values() if config.get("binance")]
    url = "https://api.binance.com/api/v3/ticker/24hr?" + urllib.parse.urlencode({"symbols": json.dumps(symbols, separators=(",", ":"))})
    usdt_url = "https://api.exchange.coinbase.com/products/USDT-USD/ticker"
    rows = fetch_json(url)
    usdt_row = fetch_json(usdt_url)
    usdt_usd = finite(usdt_row.get("price"))
    usdt_as_of = usdt_row.get("time")
    by_pair = {row.get("symbol"): row for row in rows}
    close_times = [finite(row.get("closeTime")) for row in rows]
    close_times = [value for value in close_times if value is not None]
    timestamp = datetime.fromtimestamp(max(close_times) / 1000, timezone.utc).isoformat() if close_times else None
    result = {}
    for symbol, config in ASSETS.items():
        row = by_pair.get(config.get("binance"), {})
        if row:
            price_usdt = finite(row.get("lastPrice"))
            row_close_time = finite(row.get("closeTime"))
            result[symbol] = {
                "price_usdt": price_usdt,
                "usdt_usd": usdt_usd,
                "usdt_usd_as_of": usdt_as_of,
                "price_usd": price_usdt * usdt_usd if price_usdt is not None and usdt_usd is not None else None,
                "change_24h": (finite(row.get("priceChangePercent")) or 0) / 100 if row.get("priceChangePercent") is not None else None,
                "quote_volume_24h_usd": finite(row.get("quoteVolume")) * usdt_usd if finite(row.get("quoteVolume")) is not None and usdt_usd is not None else None,
                "as_of": datetime.fromtimestamp(row_close_time / 1000, timezone.utc).isoformat() if row_close_time else None,
            }
    return result, [
        source("binance_spot", "Binance Spot", url, "primary_market", timestamp, "交易所 USDT 現貨報價；先以 Coinbase USDT/USD 換算美元"),
        source("coinbase_usdt_usd", "Coinbase Exchange", usdt_url, "primary_market", usdt_row.get("time"), "Binance USDT 報價的美元正規化匯率"),
    ]


def collect_okx_spot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    usdt_url = "https://api.exchange.coinbase.com/products/USDT-USD/ticker"
    usdt_row = fetch_json(usdt_url)
    usdt_usd = finite(usdt_row.get("price"))
    usdt_as_of = usdt_row.get("time")
    result: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for symbol in ASSETS:
        instrument = f"{symbol}-USDT"
        url = f"https://www.okx.com/api/v5/market/ticker?instId={instrument}"
        payload = fetch_json(url)
        row = (payload.get("data") or [{}])[0]
        if not row:
            continue
        price_usdt = finite(row.get("last"))
        open_24h = finite(row.get("open24h"))
        timestamp = millis_iso(row.get("ts"))
        result[symbol] = {
            "price_usdt": price_usdt,
            "usdt_usd": usdt_usd,
            "usdt_usd_as_of": usdt_as_of,
            "price_usd": price_usdt * usdt_usd if price_usdt is not None and usdt_usd is not None else None,
            "change_24h": price_usdt / open_24h - 1 if price_usdt is not None and open_24h not in (None, 0) else None,
            "quote_volume_24h_usd": finite(row.get("volCcy24h")) * usdt_usd if finite(row.get("volCcy24h")) is not None and usdt_usd is not None else None,
            "as_of": timestamp,
        }
        sources.append(source(f"okx_{symbol.lower()}_spot", "OKX Spot", url, "primary_market", timestamp, "交易所 USDT 現貨報價；以 Coinbase USDT/USD 換算美元"))
    sources.append(source("coinbase_usdt_usd_okx", "Coinbase Exchange", usdt_url, "primary_market", usdt_as_of, "OKX USDT 報價的美元正規化匯率"))
    return result, sources


def collect_coinbase_spot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for symbol, config in ASSETS.items():
        product = config.get("coinbase")
        if not product:
            continue
        url = f"https://api.exchange.coinbase.com/products/{product}/ticker"
        row = fetch_json(url)
        result[symbol] = {"price_usd": finite(row.get("price")), "as_of": row.get("time")}
        sources.append(source(f"coinbase_{symbol.lower()}", "Coinbase Exchange", url, "primary_market", row.get("time"), "交易所 USD 現貨報價"))
    return result, sources


def collect_hyperliquid() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = "https://api.hyperliquid.xyz/info"
    payload = fetch_json(url, data={"type": "metaAndAssetCtxs"})
    universe = payload[0].get("universe", [])
    contexts = payload[1]
    rows = {meta.get("name"): contexts[index] for index, meta in enumerate(universe) if index < len(contexts)}
    row = rows.get("HYPE", {})
    mark = finite(row.get("markPx"))
    previous = finite(row.get("prevDayPx"))
    result = {
        "HYPE": {
            "perp_mark_price_usd": mark,
            "perp_oracle_price_usd": finite(row.get("oraclePx")),
            "change_24h": mark / previous - 1 if mark is not None and previous not in (None, 0) else None,
            "perp_funding_1h": finite(row.get("funding")),
            "perp_open_interest_hype": finite(row.get("openInterest")),
            "perp_open_interest_usd": (finite(row.get("openInterest")) or 0) * mark if mark is not None and row.get("openInterest") is not None else None,
            "perp_volume_24h_usd": finite(row.get("dayNtlVlm")),
            "as_of": now_iso(),
        }
    }
    return result, [source("hyperliquid_hype", "Hyperliquid", url, "primary_derivatives_market", result["HYPE"]["as_of"], "HYPE 永續標記價、預言機價、資金費率、未平倉量與名目成交額；不作現貨交叉來源", "retrieval_time")]


def collect_categories() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = "https://api.coingecko.com/api/v3/coins/categories"
    rows = fetch_json(url)
    by_id = {row.get("id"): row for row in rows}
    result = {}
    for label, category_id in SECTORS.items():
        row = by_id.get(category_id, {})
        result[label] = {
            "category_id": category_id,
            "market_cap_usd": finite(row.get("market_cap")),
            "change_24h": (finite(row.get("market_cap_change_24h")) or 0) / 100 if row.get("market_cap_change_24h") is not None else None,
            "volume_24h_usd": finite(row.get("volume_24h")),
            "top_3_coins": row.get("top_3_coins", [])[:3],
            "as_of": now_iso(),
        }
    return result, [source("coingecko_categories", "CoinGecko Categories", url, "independent_market_aggregator", now_iso(), "賽道市值與 24 小時變化；分類由供應商定義", "retrieval_time_no_upstream_timestamp")]


def collect_stablecoin_and_rwa_credit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stablecoin_url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
    protocols_url = "https://api.llama.fi/protocols"
    stablecoin_payload = fetch_json(stablecoin_url)
    protocols = fetch_json(protocols_url)

    usd_assets = [item for item in stablecoin_payload.get("peggedAssets", []) if item.get("pegType") == "peggedUSD"]
    current_values = [finite(item.get("circulating", {}).get("peggedUSD")) for item in usd_assets]
    current = sum(value for value in current_values if value is not None)
    matched_assets = [
        (finite(item.get("circulating", {}).get("peggedUSD")), finite(item.get("circulatingPrevMonth", {}).get("peggedUSD")))
        for item in usd_assets
    ]
    matched_assets = [(current_value, prior_value) for current_value, prior_value in matched_assets if current_value is not None and prior_value is not None]
    matched_current = sum(current_value for current_value, _ in matched_assets)
    matched_prior_month = sum(prior_value for _, prior_value in matched_assets)

    rwa_protocols = [item for item in protocols if item.get("category") == "RWA" and finite(item.get("tvl")) is not None]
    rwa_protocols.sort(key=lambda item: finite(item.get("tvl")) or 0, reverse=True)
    rwa_tvl = sum(finite(item.get("tvl")) or 0 for item in rwa_protocols)
    result = {
        "stablecoin_supply_usd": current or None,
        "stablecoin_supply_matched_cohort_usd": matched_current or None,
        "stablecoin_supply_matched_cohort_30d_ago_usd": matched_prior_month or None,
        "stablecoin_supply_30d_change": matched_current / matched_prior_month - 1 if matched_current and matched_prior_month else None,
        "usd_stablecoin_count": len(usd_assets),
        "stablecoin_30d_matched_count": len(matched_assets),
        "stablecoin_30d_unmatched_count": len(usd_assets) - len(matched_assets),
        "rwa_protocol_tvl_usd": rwa_tvl or None,
        "rwa_protocol_count": len(rwa_protocols),
        "rwa_top_protocols": [
            {"name": item.get("name"), "tvl_usd": finite(item.get("tvl"))}
            for item in rwa_protocols[:5]
        ],
        "as_of": now_iso(),
        "as_of_basis": "retrieval_time_no_upstream_timestamp",
        "limitations": [
            "Stablecoin supply is DefiLlama's peggedUSD aggregation, not bank deposits or transaction volume; 30-day change uses only assets with both current and prior-month values",
            "RWA TVL is the sum of protocols classified as RWA by DefiLlama and may contain provider taxonomy or double-counting risk",
            "Stablecoin and RWA scale are reported separately and are never added together",
        ],
    }
    return result, [
        source("defillama_usd_stablecoins", "DefiLlama Stablecoins", stablecoin_url, "independent_market_aggregator", result["as_of"], "美元掛鉤穩定幣供給與 30 日變化；上游未提供統一時間戳", "retrieval_time_no_upstream_timestamp"),
        source("defillama_rwa_protocols", "DefiLlama Protocols", protocols_url, "independent_market_aggregator", result["as_of"], "供應商分類為 RWA 的協議 TVL 加總；不與穩定幣供給相加", "retrieval_time_no_upstream_timestamp"),
    ]


def collect_gold_reference() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gold_url = "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?range=5d&interval=1d"
    stock_url = "https://www.gold.org/goldhub/data/how-much-gold"
    chart = fetch_json(gold_url)["chart"]["result"][0]
    meta = chart.get("meta", {})
    gold_price = finite(meta.get("regularMarketPrice"))
    gold_as_of = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).isoformat() if meta.get("regularMarketTime") else None
    html = fetch_text(stock_url)
    matches = re.findall(r"above-ground stock \(end-(\d{4})\):\s*([0-9,]+) tonnes", html, re.IGNORECASE)
    if not matches:
        raise ValueError("World Gold Council above-ground stock value not found")
    stock_year_text, tonnes_text = max(matches, key=lambda item: int(item[0]))
    tonnes = finite(tonnes_text.replace(",", ""))
    estimated_market_value = gold_price * tonnes * TROY_OZ_PER_METRIC_TONNE if gold_price is not None and tonnes is not None else None
    result = {
        "gold_price_proxy_usd_per_troy_oz": gold_price,
        "gold_price_proxy_ticker": "GC=F",
        "gold_price_as_of": gold_as_of,
        "above_ground_gold_tonnes": tonnes,
        "above_ground_stock_year": int(stock_year_text),
        "estimated_gold_market_value_usd": estimated_market_value,
        "as_of": gold_as_of,
        "limitation": "Gold market value is a scenario proxy: Yahoo COMEX front-month price multiplied by World Gold Council above-ground stock; it is not an investable market-cap series",
    }
    return result, [
        source("yahoo_gold_front_month", "Yahoo Finance / COMEX proxy", gold_url, "third_party_market_proxy", gold_as_of, "黃金前月期貨代理價格；受延遲與換月影響，不是現貨成交價"),
        source("wgc_above_ground_gold", "World Gold Council", stock_url, "official_industry_research", f"{stock_year_text}-12-31", f"全球地上黃金存量 {tonnes:,.0f} 公噸；年度更新"),
    ]


def collect_hashrate_consensus() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = "https://api.blockchain.info/charts/hash-rate?timespan=180days&format=json&cors=true"
    payload = fetch_json(url)
    values = sorted(
        [(datetime.fromtimestamp(item["x"], timezone.utc), finite(item.get("y"))) for item in payload.get("values", []) if finite(item.get("y")) is not None],
        key=lambda item: item[0],
    )
    if len(values) < 31:
        raise ValueError("Insufficient hashrate history")
    latest_time, latest_value = values[-1]

    def value_at_or_before(target: datetime) -> float | None:
        candidates = [value for timestamp, value in values if timestamp <= target and value is not None]
        return candidates[-1] if candidates else None

    prior_30d = value_at_or_before(latest_time - timedelta(days=30))
    recent_90d = [value for timestamp, value in values if timestamp >= latest_time - timedelta(days=90) and value is not None]
    high_90d = max(recent_90d) if recent_90d else None
    result = {
        "hashrate_ths": latest_value,
        "hashrate_30d_ago_ths": prior_30d,
        "hashrate_30d_change": latest_value / prior_30d - 1 if latest_value is not None and prior_30d not in (None, 0) else None,
        "hashrate_90d_high_ths": high_90d,
        "hashrate_vs_90d_high": latest_value / high_90d if latest_value is not None and high_90d not in (None, 0) else None,
        "as_of": latest_time.isoformat(),
        "limitation": "Hashrate is a network-security and miner-commitment proxy, not a direct price or adoption signal",
    }
    return result, [source("blockchain_hashrate_180d", "Blockchain.com", url, "independent_onchain", result["as_of"], "180 日算力序列，用於 30 日變化與相對 90 日高點")]


def latest_fred_observations(series_id: str) -> list[tuple[date, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    rows = csv.DictReader(io.StringIO(fetch_text(url)))
    result: list[tuple[date, float]] = []
    for row in rows:
        value = finite(row.get(series_id))
        if value is not None:
            result.append((date.fromisoformat(row["observation_date"]), value))
    if not result:
        raise ValueError(f"FRED {series_id} has no observations")
    return result


def collect_sovereign_credit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    debt_series = "GFDEGDQ188S"
    real_yield_series = "DFII10"
    debt_values = latest_fred_observations(debt_series)
    yield_values = latest_fred_observations(real_yield_series)
    debt_date, debt_value = debt_values[-1]
    yield_date, yield_value = yield_values[-1]
    debt_prior_year = debt_values[-5][1] if len(debt_values) >= 5 else None
    yield_cutoff = yield_date - timedelta(days=30)
    prior_yield_values = [value for observation_date, value in yield_values if observation_date <= yield_cutoff]
    prior_yield = prior_yield_values[-1] if prior_yield_values else None
    result = {
        "us_federal_debt_to_gdp_pct": debt_value,
        "us_federal_debt_to_gdp_yoy_change_pp": debt_value - debt_prior_year if debt_prior_year is not None else None,
        "us_federal_debt_to_gdp_as_of": debt_date.isoformat(),
        "us_10y_real_yield_pct": yield_value,
        "us_10y_real_yield_30d_change_pp": yield_value - prior_yield if prior_yield is not None else None,
        "us_10y_real_yield_as_of": yield_date.isoformat(),
        "as_of": max(debt_date, yield_date).isoformat(),
        "limitation": "Debt/GDP is a slow structural sovereign-credit proxy; the 10-year real yield is a cyclical opportunity-cost proxy. Neither is a direct BTC timing signal",
    }
    return result, [
        source("fred_us_debt_gdp", "FRED / U.S. Office of Management and Budget", f"https://fred.stlouisfed.org/series/{debt_series}", "official_macro_aggregator", debt_date.isoformat(), "美國聯邦債務占 GDP，季度結構資料；FRED 轉載 OMB 序列"),
        source("fred_us_10y_real_yield", "FRED / Federal Reserve Board", f"https://fred.stlouisfed.org/series/{real_yield_series}", "official_macro_aggregator", yield_date.isoformat(), "美國 10 年期通膨保值公債實質殖利率；FRED 轉載聯準會理事會序列"),
    ]


def collect_perpetual(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pair = f"{symbol}USDT"
    venues: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    venue_errors: list[str] = []

    try:
        premium_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={pair}"
        oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={pair}"
        ticker_url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={pair}"
        premium = fetch_json(premium_url)
        oi = fetch_json(oi_url)
        ticker = fetch_json(ticker_url)
        mark = finite(premium.get("markPrice"))
        oi_base = finite(oi.get("openInterest"))
        rate = finite(premium.get("lastFundingRate"))
        interval = 8.0
        as_of = millis_iso(premium.get("time"))
        venues["binance"] = {
            "mark_price_usd": mark, "index_price_usd": finite(premium.get("indexPrice")),
            "funding_rate": rate, "funding_interval_hours": interval,
            "funding_annualized": rate * 24 / interval * 365 if rate is not None else None,
            "open_interest_base": oi_base,
            "open_interest_usd": oi_base * mark if oi_base is not None and mark is not None else None,
            "volume_24h_usd": finite(ticker.get("quoteVolume")), "as_of": as_of,
        }
        sources.append(source(f"binance_{symbol.lower()}_perp", "Binance USD-M Futures", premium_url, "primary_derivatives_market", as_of, "永續標記價、指數價、8 小時資金費率、未平倉量與 24 小時成交額"))
    except Exception as exc:
        venue_errors.append(readable_source_error("Binance", exc))

    try:
        bybit_url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={pair}"
        instrument_url = f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={pair}"
        payload = fetch_json(bybit_url)
        row = (payload.get("result", {}).get("list") or [{}])[0]
        instrument = (fetch_json(instrument_url).get("result", {}).get("list") or [{}])[0]
        rate = finite(row.get("fundingRate"))
        interval_minutes = finite(instrument.get("fundingInterval"))
        interval = interval_minutes / 60 if interval_minutes not in (None, 0) else None
        as_of = millis_iso(payload.get("time"))
        venues["bybit"] = {
            "mark_price_usd": finite(row.get("markPrice")), "index_price_usd": finite(row.get("indexPrice")),
            "funding_rate": rate, "funding_interval_hours": interval,
            "funding_annualized": rate * 24 / interval * 365 if rate is not None and interval else None,
            "open_interest_base": finite(row.get("openInterest")), "open_interest_usd": finite(row.get("openInterestValue")),
            "volume_24h_usd": finite(row.get("turnover24h")), "as_of": as_of,
        }
        sources.append(source(f"bybit_{symbol.lower()}_perp", "Bybit Linear", bybit_url, "primary_derivatives_market", as_of, f"永續標記價、指數價、{interval:g} 小時資金費率、未平倉量與 24 小時成交額" if interval else "永續資料；資金費率週期未知"))
    except Exception as exc:
        venue_errors.append(readable_source_error("Bybit", exc))

    try:
        instrument = f"{symbol}-USDT-SWAP"
        ticker_url = f"https://www.okx.com/api/v5/market/ticker?instId={instrument}"
        mark_url = f"https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={instrument}"
        index_url = f"https://www.okx.com/api/v5/market/index-tickers?instId={symbol}-USDT"
        oi_url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={instrument}"
        funding_url = f"https://www.okx.com/api/v5/public/funding-rate?instId={instrument}"
        ticker = (fetch_json(ticker_url).get("data") or [{}])[0]
        mark_row = (fetch_json(mark_url).get("data") or [{}])[0]
        index_row = (fetch_json(index_url).get("data") or [{}])[0]
        oi_row = (fetch_json(oi_url).get("data") or [{}])[0]
        funding_row = (fetch_json(funding_url).get("data") or [{}])[0]
        rate = finite(funding_row.get("settFundingRate"))
        funding_time = finite(funding_row.get("fundingTime"))
        previous_funding_time = finite(funding_row.get("prevFundingTime"))
        interval = (funding_time - previous_funding_time) / 3_600_000 if funding_time is not None and previous_funding_time is not None else None
        timestamps = [finite(row.get("ts")) for row in (ticker, mark_row, index_row, oi_row, funding_row)]
        timestamps = [value for value in timestamps if value is not None]
        as_of = millis_iso(min(timestamps)) if timestamps else None
        index_price = finite(index_row.get("idxPx"))
        volume_base = finite(ticker.get("volCcy24h"))
        venues["okx"] = {
            "mark_price_usd": finite(mark_row.get("markPx")), "index_price_usd": index_price,
            "funding_rate": rate, "funding_interval_hours": interval,
            "funding_annualized": rate * 24 / interval * 365 if rate is not None and interval else None,
            "open_interest_base": finite(oi_row.get("oiCcy")), "open_interest_usd": finite(oi_row.get("oiUsd")),
            "volume_24h_usd": volume_base * index_price if volume_base is not None and index_price is not None else None,
            "as_of": as_of,
        }
        sources.append(source(f"okx_{symbol.lower()}_perp", "OKX Linear Swap", funding_url, "primary_derivatives_market", as_of, f"永續標記價、指數價、{interval:g} 小時已結算資金費率、未平倉量與 24 小時成交額" if interval else "永續資料；資金費率週期未知"))
    except Exception as exc:
        venue_errors.append(readable_source_error("OKX", exc))

    try:
        hyperliquid_url = "https://api.hyperliquid.xyz/info"
        payload = fetch_json(hyperliquid_url, data={"type": "metaAndAssetCtxs"})
        universe = payload[0].get("universe", [])
        contexts = payload[1]
        rows = {meta.get("name"): contexts[index] for index, meta in enumerate(universe) if index < len(contexts)}
        row = rows.get(symbol, {})
        mark = finite(row.get("markPx"))
        oi_base = finite(row.get("openInterest"))
        rate = finite(row.get("funding"))
        interval = 1.0
        as_of = now_iso()
        if mark is None or rate is None:
            raise ValueError(f"{symbol} perpetual missing")
        venues["hyperliquid"] = {
            "mark_price_usd": mark, "index_price_usd": finite(row.get("oraclePx")),
            "funding_rate": rate, "funding_interval_hours": interval,
            "funding_annualized": rate * 24 * 365,
            "open_interest_base": oi_base, "open_interest_usd": oi_base * mark if oi_base is not None else None,
            "volume_24h_usd": finite(row.get("dayNtlVlm")), "as_of": as_of,
        }
        sources.append(source(f"hyperliquid_{symbol.lower()}_perp", "Hyperliquid", hyperliquid_url, "primary_derivatives_market", as_of, "每小時資金費率、標記價、預言機價、未平倉量與 24 小時名目成交額", "retrieval_time"))
    except Exception as exc:
        venue_errors.append(readable_source_error("Hyperliquid", exc))

    valid_venues = {name: item for name, item in venues.items() if item.get("funding_annualized") is not None}
    annualized_values = [item["funding_annualized"] for item in valid_venues.values()]
    intervals = [item.get("funding_interval_hours") for item in valid_venues.values() if item.get("funding_interval_hours") is not None]
    interval_consistent = len(intervals) >= 2 and len(set(intervals)) == 1
    raw_rates = [item.get("funding_rate") for item in valid_venues.values() if item.get("funding_rate") is not None]
    result: dict[str, Any] = {
        "coverage": "+".join(valid_venues) + "; partial observable venues, not global market",
        "venues_used": list(valid_venues),
        "venue_errors": venue_errors,
        **venues,
        "funding_8h_median": statistics.median(raw_rates) if interval_consistent and intervals[0] == 8 else None,
        "funding_annualized_median": statistics.median(annualized_values) if annualized_values else None,
        "funding_source_count": len(annualized_values),
        "funding_interval_hours_consistent": interval_consistent,
        "funding_annualized_cross_venue_gap_bps": (max(annualized_values) - min(annualized_values)) * 10_000 if len(annualized_values) >= 2 else None,
        "observed_open_interest_usd": sum(item["open_interest_usd"] for item in valid_venues.values() if item.get("open_interest_usd") is not None) or None,
    }
    return result, sources


def _collect_deribit_dated_future(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    instruments_url = f"https://www.deribit.com/api/v2/public/get_instruments?currency={symbol}&kind=future&expired=false"
    instruments = fetch_json(instruments_url).get("result", [])
    now = datetime.now(timezone.utc)
    contracts = [row for row in instruments if row.get("settlement_period") == "month" and finite(row.get("expiration_timestamp"))]
    contracts = [row for row in contracts if datetime.fromtimestamp(row["expiration_timestamp"] / 1000, timezone.utc) > now]
    if not contracts:
        raise ValueError(f"No dated Deribit future for {symbol}")
    contract = min(contracts, key=lambda row: abs((datetime.fromtimestamp(row["expiration_timestamp"] / 1000, timezone.utc) - now).total_seconds() / 86400 - 90))
    ticker_url = "https://www.deribit.com/api/v2/public/ticker?" + urllib.parse.urlencode({"instrument_name": contract["instrument_name"]})
    ticker = fetch_json(ticker_url).get("result", {})
    mark = finite(ticker.get("mark_price"))
    index = finite(ticker.get("index_price"))
    delivery = datetime.fromtimestamp(contract["expiration_timestamp"] / 1000, timezone.utc)
    days = max((delivery - datetime.now(timezone.utc)).total_seconds() / 86400, 0)
    basis = mark / index - 1 if mark is not None and index not in (None, 0) else None
    result = {
        "provider": "Deribit",
        "selection_rule": "listed monthly expiry closest to 90 days",
        "contract": contract["instrument_name"],
        "delivery_date": delivery.date().isoformat(),
        "days_to_delivery": days,
        "mark_price_usd": mark,
        "index_price_usd": index,
        "basis": basis,
        "annualized_basis": basis * 365 / days if basis is not None and days > 0 else None,
        "as_of": millis_iso(ticker.get("timestamp")),
    }
    return result, [source(f"deribit_{symbol.lower()}_dated_future", "Deribit Futures", ticker_url, "primary_derivatives_market", result["as_of"], "最接近 90 天的掛牌月到期期貨；標記價相對指數價的簡單年化基差")]


def _collect_okx_dated_future(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    instruments_url = "https://www.okx.com/api/v5/public/instruments?instType=FUTURES"
    tickers_url = "https://www.okx.com/api/v5/market/tickers?instType=FUTURES"
    index_url = f"https://www.okx.com/api/v5/market/index-tickers?instId={symbol}-USD"
    instruments = fetch_json(instruments_url).get("data", [])
    tickers = {row.get("instId"): row for row in fetch_json(tickers_url).get("data", [])}
    index_rows = fetch_json(index_url).get("data", [])
    now = datetime.now(timezone.utc)
    contracts = [
        row for row in instruments
        if row.get("instFamily") == f"{symbol}-USD"
        and row.get("state") == "live"
        and finite(row.get("expTime"))
        and datetime.fromtimestamp(float(row["expTime"]) / 1000, timezone.utc) > now
        and row.get("instId") in tickers
    ]
    if not contracts or not index_rows:
        raise ValueError(f"No dated OKX future or index for {symbol}")
    contract = min(contracts, key=lambda row: abs((datetime.fromtimestamp(float(row["expTime"]) / 1000, timezone.utc) - now).total_seconds() / 86400 - 90))
    ticker = tickers[contract["instId"]]
    bid = finite(ticker.get("bidPx"))
    ask = finite(ticker.get("askPx"))
    last = finite(ticker.get("last"))
    future_price = (bid + ask) / 2 if bid is not None and ask is not None else last
    index = finite(index_rows[0].get("idxPx"))
    delivery = datetime.fromtimestamp(float(contract["expTime"]) / 1000, timezone.utc)
    days = max((delivery - datetime.now(timezone.utc)).total_seconds() / 86400, 0)
    basis = future_price / index - 1 if future_price is not None and index not in (None, 0) else None
    timestamps = [finite(ticker.get("ts")), finite(index_rows[0].get("ts"))]
    timestamps = [value for value in timestamps if value is not None]
    as_of = millis_iso(max(timestamps)) if timestamps else None
    result = {
        "provider": "OKX",
        "selection_rule": "listed coin-margined expiry closest to 90 days",
        "contract": contract["instId"],
        "delivery_date": delivery.date().isoformat(),
        "days_to_delivery": days,
        "mark_price_usd": future_price,
        "price_basis": "bid_ask_midpoint_else_last",
        "index_price_usd": index,
        "basis": basis,
        "annualized_basis": basis * 365 / days if basis is not None and days > 0 else None,
        "as_of": as_of,
    }
    return result, [
        source(f"okx_{symbol.lower()}_dated_future", "OKX Futures", tickers_url, "primary_derivatives_market", as_of, "Deribit 不可用時的備援；最接近 90 天的幣本位到期期貨，買賣中價相對 OKX 指數價的簡單年化基差"),
        source(f"okx_{symbol.lower()}_index", "OKX Index", index_url, "primary_derivatives_market", as_of, "OKX 到期期貨基差分母"),
    ]


def collect_dated_future(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return _collect_deribit_dated_future(symbol)
    except Exception as deribit_error:
        result, sources = _collect_okx_dated_future(symbol)
        result["fallback_errors"] = [readable_source_error("Deribit 到期期貨", deribit_error)]
        return result, sources


def collect_cme_proxy(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ticker = "BTC=F" if symbol == "BTC" else "ETH=F"
    encoded = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    payload = fetch_json(url)
    result = payload["chart"]["result"][0]
    meta = result.get("meta", {})
    timestamp = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None
    output = {
        "ticker": ticker,
        "front_month_price_usd": finite(meta.get("regularMarketPrice")),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "quote_delay_seconds": meta.get("exchangeDataDelayedBy"),
        "as_of": as_of,
        "limitation": "Yahoo front-month proxy; contract roll and delay mean it is context, not an execution price",
    }
    return output, [source(f"yahoo_cme_{symbol.lower()}", "Yahoo Finance / CME proxy", url, "third_party_market_proxy", as_of, "Yahoo CME 前月代理；可能延遲且受換月影響，不是可成交價格")]


def parse_option_name(name: str) -> tuple[datetime, float, str] | None:
    parts = name.split("-")
    if len(parts) != 4:
        return None
    try:
        expiry = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc, hour=8)
        return expiry, float(parts[2]), parts[3]
    except (ValueError, TypeError):
        return None


def _collect_deribit_options(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary_url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={symbol}&kind=option"
    summaries = fetch_json(summary_url).get("result", [])
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - 48 * 3600 * 1000
    dvol_url = f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={symbol}&start_timestamp={start_ms}&end_timestamp={end_ms}&resolution=3600"
    dvol_rows = fetch_json(dvol_url).get("result", {}).get("data", [])
    current_hour_ms = int(datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
    completed_dvol_rows = [row for row in dvol_rows if row and row[0] < current_hour_ms]
    dvol_row = completed_dvol_rows[-1] if completed_dvol_rows else None
    dvol = finite(dvol_row[4]) if dvol_row else None
    dvol_as_of = datetime.fromtimestamp(dvol_row[0] / 1000, timezone.utc).isoformat() if dvol_row else None
    parsed = []
    for row in summaries:
        option = parse_option_name(str(row.get("instrument_name", "")))
        if option and option[0] > datetime.now(timezone.utc):
            parsed.append((option[0], option[1], option[2], row))
    calls = [item for item in parsed if item[2] == "C"]
    puts = [item for item in parsed if item[2] == "P"]
    call_oi_values = [finite(item[3].get("open_interest")) for item in calls]
    put_oi_values = [finite(item[3].get("open_interest")) for item in puts]
    call_oi = sum(value for value in call_oi_values if value is not None) if any(value is not None for value in call_oi_values) else None
    put_oi = sum(value for value in put_oi_values if value is not None) if any(value is not None for value in put_oi_values) else None
    expiries = sorted({item[0] for item in parsed})
    target_expiry = min(expiries, key=lambda expiry: abs((expiry - datetime.now(timezone.utc)).total_seconds() / 86400 - 30)) if expiries else None
    target_rows = [item for item in parsed if item[0] == target_expiry]
    underlying_values = [finite(item[3].get("underlying_price")) for item in target_rows]
    underlying_values = [value for value in underlying_values if value is not None]
    underlying = statistics.median(underlying_values) if underlying_values else None
    atm_rows = []
    for option_type in ("C", "P"):
        candidates = [item for item in target_rows if item[2] == option_type and underlying is not None]
        if candidates:
            atm_rows.append(min(candidates, key=lambda item: abs(item[1] - underlying)))
    atm_ivs = [finite(item[3].get("mark_iv")) for item in atm_rows]
    atm_ivs = [value for value in atm_ivs if value is not None]
    max_pain = None
    if target_rows:
        strikes = sorted({item[1] for item in target_rows})
        pain_by_strike = {}
        for settlement in strikes:
            pain = 0.0
            for _, strike, option_type, row in target_rows:
                oi = finite(row.get("open_interest"))
                if oi is None:
                    continue
                intrinsic = max(settlement - strike, 0) if option_type == "C" else max(strike - settlement, 0)
                pain += intrinsic * oi
            pain_by_strike[settlement] = pain
        max_pain = min(pain_by_strike, key=pain_by_strike.get)
    all_underlying = [finite(item[3].get("underlying_price")) for item in parsed]
    all_underlying = [value for value in all_underlying if value is not None]
    spot_proxy = statistics.median(all_underlying) if all_underlying else None
    volume_values = [finite(item[3].get("volume_usd")) for item in parsed]
    oi_usd_values = []
    for _, _, _, row in parsed:
        open_interest = finite(row.get("open_interest"))
        index_price = finite(row.get("estimated_delivery_price")) or finite(row.get("underlying_price"))
        if open_interest is not None and index_price is not None:
            oi_usd_values.append(open_interest * index_price)
    option_creation_times = [finite(item[3].get("creation_timestamp")) for item in parsed]
    option_creation_times = [value for value in option_creation_times if value is not None]
    options_as_of = datetime.fromtimestamp(max(option_creation_times) / 1000, timezone.utc).isoformat() if option_creation_times else None
    oi_observed_contracts = sum(value is not None for value in call_oi_values + put_oi_values)
    result = {
        "provider": "Deribit",
        "coverage": "Deribit BTC/ETH 幣本位期權子集；不含 USDC 期權，也不代表全球期權市場",
        "contract_set": "inverse_coin_margined_only",
        "dvol": dvol,
        "dvol_as_of": dvol_as_of,
        "volatility_value": dvol,
        "volatility_metric": "deribit_dvol",
        "volatility_label": "Deribit 隱含波動率指數",
        "volatility_as_of": dvol_as_of,
        "put_call_open_interest_ratio": put_oi / call_oi if put_oi is not None and call_oi else None,
        "call_open_interest_base": call_oi,
        "put_open_interest_base": put_oi,
        "observed_open_interest_usd": sum(oi_usd_values) if oi_usd_values else None,
        "open_interest_usd_basis": "逐合約未平倉量 × estimated_delivery_price；若缺值才使用 underlying_price",
        "volume_24h_usd": sum(value for value in volume_values if value is not None) if any(value is not None for value in volume_values) else None,
        "contracts_observed": len(parsed),
        "open_interest_observed_contracts": oi_observed_contracts,
        "volume_observed_contracts": sum(value is not None for value in volume_values),
        "target_expiry": target_expiry.date().isoformat() if target_expiry else None,
        "target_days": (target_expiry - datetime.now(timezone.utc)).total_seconds() / 86400 if target_expiry else None,
        "atm_implied_volatility": statistics.mean(atm_ivs) if atm_ivs else None,
        "atm_components": [
            {
                "instrument": item[3].get("instrument_name"),
                "option_type": item[2],
                "strike_usd": item[1],
                "mark_iv_pct": finite(item[3].get("mark_iv")),
            }
            for item in atm_rows
        ],
        "max_pain_usd": max_pain,
        "max_pain_distance": max_pain / underlying - 1 if max_pain is not None and underlying else None,
        "as_of": options_as_of,
        "limits": ["Max pain is a descriptive OI concentration, not a price target", "Put/call OI does not identify trade direction or buyer/seller intent"],
    }
    return result, [
        source(f"deribit_{symbol.lower()}_options", "Deribit", summary_url, "primary_derivatives_market", result["as_of"], "Deribit 期權未平倉量、隱含波動率、成交額與自算最大痛點集中價"),
        source(f"deribit_{symbol.lower()}_dvol", "Deribit DVOL", dvol_url, "primary_derivatives_market", dvol_as_of, "最近一個完整小時的 Deribit 隱含波動率指數收盤值"),
    ]


def parse_okx_option_name(name: str, symbol: str) -> tuple[datetime, float, str] | None:
    match = re.match(rf"^{re.escape(symbol)}-USD-(\d{{6}})-([0-9.]+)-([CP])$", name)
    if not match:
        return None
    try:
        expiry = datetime.strptime(match.group(1), "%y%m%d").replace(tzinfo=timezone.utc, hour=8)
        return expiry, float(match.group(2)), match.group(3)
    except (ValueError, TypeError):
        return None


def _collect_okx_options(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    underlying = f"{symbol}-USD"
    instruments_url = f"https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly={underlying}"
    summary_url = f"https://www.okx.com/api/v5/public/opt-summary?uly={underlying}"
    oi_url = f"https://www.okx.com/api/v5/public/open-interest?instType=OPTION&uly={underlying}"
    tickers_url = f"https://www.okx.com/api/v5/market/tickers?instType=OPTION&uly={underlying}"
    instruments = fetch_json(instruments_url).get("data", [])
    eligible_instruments = {
        row.get("instId"): row
        for row in instruments
        if row.get("ctType") == "inverse"
        and row.get("settleCcy") == symbol
        and row.get("instFamily") == underlying
        and row.get("state") == "live"
    }
    excluded_linear_count = sum(
        row.get("ctType") == "linear" or row.get("settleCcy") != symbol or row.get("instFamily") != underlying
        for row in instruments
    )
    summaries = fetch_json(summary_url).get("data", [])
    oi_by_name = {row.get("instId"): row for row in fetch_json(oi_url).get("data", [])}
    ticker_by_name = {row.get("instId"): row for row in fetch_json(tickers_url).get("data", [])}
    now = datetime.now(timezone.utc)
    parsed = []
    for summary in summaries:
        name = str(summary.get("instId", ""))
        option = parse_okx_option_name(name, symbol)
        metadata = eligible_instruments.get(name)
        oi = oi_by_name.get(name)
        ticker = ticker_by_name.get(name)
        if metadata is not None and option and option[0] > now and oi is not None and ticker is not None:
            parsed.append((option[0], option[1], option[2], summary, oi, ticker))
    if not parsed:
        raise ValueError(f"No complete OKX option observations for {symbol}")

    calls = [item for item in parsed if item[2] == "C"]
    puts = [item for item in parsed if item[2] == "P"]
    call_oi_values = [finite(item[4].get("oiCcy")) for item in calls]
    put_oi_values = [finite(item[4].get("oiCcy")) for item in puts]
    call_oi = sum(value for value in call_oi_values if value is not None) if any(value is not None for value in call_oi_values) else None
    put_oi = sum(value for value in put_oi_values if value is not None) if any(value is not None for value in put_oi_values) else None
    expiries = sorted({item[0] for item in parsed})
    target_expiry = min(expiries, key=lambda expiry: abs((expiry - now).total_seconds() / 86400 - 30))
    target_rows = [item for item in parsed if item[0] == target_expiry]
    forward_values = [finite(item[3].get("fwdPx")) for item in target_rows]
    forward_values = [value for value in forward_values if value is not None]
    forward = statistics.median(forward_values) if forward_values else None
    atm_rows = []
    for option_type in ("C", "P"):
        candidates = [item for item in target_rows if item[2] == option_type and forward is not None]
        if candidates:
            atm_rows.append(min(candidates, key=lambda item: abs(item[1] - forward)))
    atm_vols = [finite(item[3].get("markVol")) for item in atm_rows]
    atm_vols = [value * 100 for value in atm_vols if value is not None]

    max_pain = None
    if target_rows:
        strikes = sorted({item[1] for item in target_rows})
        pain_by_strike = {}
        for settlement in strikes:
            pain = 0.0
            for _, strike, option_type, _, oi, _ in target_rows:
                open_interest = finite(oi.get("oiCcy"))
                if open_interest is None:
                    continue
                intrinsic = max(settlement - strike, 0) if option_type == "C" else max(strike - settlement, 0)
                pain += intrinsic * open_interest
            pain_by_strike[settlement] = pain
        max_pain = min(pain_by_strike, key=pain_by_strike.get)

    oi_usd_values = [finite(item[4].get("oiUsd")) for item in parsed]
    oi_usd_values = [value for value in oi_usd_values if value is not None]
    volume_usd_values = []
    timestamps = []
    for _, _, _, summary, oi, ticker in parsed:
        volume_base = finite(ticker.get("volCcy24h"))
        forward_price = finite(summary.get("fwdPx"))
        if volume_base is not None and forward_price is not None:
            volume_usd_values.append(volume_base * forward_price)
        timestamps.extend([finite(summary.get("ts")), finite(oi.get("ts")), finite(ticker.get("ts"))])
    timestamps = [value for value in timestamps if value is not None]
    as_of = millis_iso(max(timestamps)) if timestamps else None
    volatility = statistics.mean(atm_vols) if atm_vols else None
    result = {
        "provider": "OKX",
        "coverage": "OKX BTC/ETH 幣本位期權；Deribit 不可用時的備援，不代表全球期權市場",
        "contract_set": "okx_coin_margined_options",
        "contract_filter": {"ct_type": "inverse", "settle_ccy": symbol, "inst_family": underlying, "state": "live"},
        "contract_type_counts": {
            "provider_instruments": len(instruments),
            "eligible_inverse_instruments": len(eligible_instruments),
            "observed_inverse_contracts": len(parsed),
            "excluded_non_inverse_instruments": excluded_linear_count,
        },
        "observed_contract_ids": [item[3].get("instId") for item in parsed],
        "dvol": None,
        "dvol_as_of": None,
        "volatility_value": volatility,
        "volatility_metric": "okx_atm_mark_iv_near_30d",
        "volatility_label": "OKX 約 30 日 ATM 標記隱含波動率",
        "volatility_as_of": as_of,
        "put_call_open_interest_ratio": put_oi / call_oi if put_oi is not None and call_oi else None,
        "call_open_interest_base": call_oi,
        "put_open_interest_base": put_oi,
        "observed_open_interest_usd": sum(oi_usd_values) if oi_usd_values else None,
        "open_interest_usd_basis": "OKX oiUsd 逐合約加總",
        "volume_24h_usd": sum(volume_usd_values) if volume_usd_values else None,
        "volume_usd_basis": "OKX volCcy24h × 同到期 fwdPx 的可觀測代理",
        "contracts_observed": len(parsed),
        "open_interest_observed_contracts": sum(finite(item[4].get("oiCcy")) is not None for item in parsed),
        "volume_observed_contracts": sum(finite(item[5].get("volCcy24h")) is not None and finite(item[3].get("fwdPx")) is not None for item in parsed),
        "target_expiry": target_expiry.date().isoformat(),
        "target_days": (target_expiry - now).total_seconds() / 86400,
        "atm_implied_volatility": volatility,
        "atm_components": [
            {
                "instrument": item[3].get("instId"),
                "option_type": item[2],
                "strike_usd": item[1],
                "forward_usd": finite(item[3].get("fwdPx")),
                "mark_iv_pct": (finite(item[3].get("markVol")) * 100) if finite(item[3].get("markVol")) is not None else None,
            }
            for item in atm_rows
        ],
        "max_pain_usd": max_pain,
        "max_pain_distance": max_pain / forward - 1 if max_pain is not None and forward else None,
        "as_of": as_of,
        "limits": ["ATM mark IV is not DVOL and must not be joined as the same historical series", "Max pain is descriptive OI concentration, not a price target", "Put/call OI does not identify trade direction or buyer/seller intent"],
    }
    return result, [
        source(f"okx_{symbol.lower()}_option_instruments", "OKX Option Instruments", instruments_url, "primary_derivatives_market", as_of, f"只允許 ctType=inverse、settleCcy={symbol}、instFamily={underlying} 的 live 幣本位期權；線性 USD 結算合約排除"),
        source(f"okx_{symbol.lower()}_option_summary", "OKX Option Summary", summary_url, "primary_derivatives_market", as_of, "Deribit 不可用時的期權隱含波動率備援；取接近 30 日到期的 ATM Call/Put 標記 IV 平均"),
        source(f"okx_{symbol.lower()}_option_oi", "OKX Option Open Interest", oi_url, "primary_derivatives_market", as_of, "OKX 幣本位期權 Put/Call 未平倉量與美元名目值"),
        source(f"okx_{symbol.lower()}_option_tickers", "OKX Option Tickers", tickers_url, "primary_derivatives_market", as_of, "OKX 期權 24 小時成交量代理"),
    ]


def collect_options(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return _collect_deribit_options(symbol)
    except Exception as deribit_error:
        result, sources = _collect_okx_options(symbol)
        result["fallback_errors"] = [readable_source_error("Deribit 期權", deribit_error)]
        return result, sources


def collect_dat_treasuries(asset: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coin = "bitcoin" if asset == "BTC" else "ethereum"
    url = f"https://api.coingecko.com/api/v3/companies/public_treasury/{coin}"
    payload = fetch_json(url)
    companies = []
    company_rows = sorted(payload.get("companies", []), key=lambda row: finite(row.get("total_holdings")) or 0, reverse=True)
    for row in company_rows[:8]:
        companies.append({
            "name": row.get("name"),
            "symbol": row.get("symbol"),
            "holdings": finite(row.get("total_holdings")),
            "current_value_usd": finite(row.get("total_current_value_usd")),
            "supply_share": (finite(row.get("percentage_of_total_supply")) or 0) / 100 if row.get("percentage_of_total_supply") is not None else None,
        })
    result = {
        "asset": asset,
        "total_holdings": finite(payload.get("total_holdings")),
        "total_value_usd": finite(payload.get("total_value_usd")),
        "supply_share": (finite(payload.get("market_cap_dominance")) or 0) / 100 if payload.get("market_cap_dominance") is not None else None,
        "companies": companies,
        "as_of": now_iso(),
        "as_of_basis": "retrieval_time_no_upstream_timestamp",
        "limitation": "CoinGecko 公司財庫單一聚合來源，可能落後官方揭露；差額也可能包含供應商修訂，只作雷達背景",
    }
    return result, [source(f"coingecko_{asset.lower()}_dat", "CoinGecko Public Companies Treasury", url, "third_party_treasury_aggregator", result["as_of"], result["limitation"], result["as_of_basis"])]


def cross_source_gap(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None and value > 0]
    if len(clean) < 2:
        return None
    return (max(clean) - min(clean)) / statistics.mean(clean)


def state_from_change(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.03:
        return "strong"
    if value <= -0.03:
        return "weak"
    return "neutral"


def analyze(output: dict[str, Any]) -> dict[str, Any]:
    assets = output["assets"]
    derivatives = output["derivatives"]
    sectors = output["sectors"]
    positive = [symbol for symbol, item in assets.items() if item.get("change_24h") is not None and item["change_24h"] > 0]
    ranked_assets = sorted(
        [(symbol, item.get("change_24h")) for symbol, item in assets.items() if item.get("change_24h") is not None],
        key=lambda item: item[1],
        reverse=True,
    )
    sector_rank = sorted(
        [(name, item.get("change_24h")) for name, item in sectors.items() if item.get("change_24h") is not None],
        key=lambda item: item[1],
        reverse=True,
    )
    result: dict[str, Any] = {
        "breadth": {
            "positive_assets": len(positive),
            "tracked_assets": len([item for item in assets.values() if item.get("change_24h") is not None]),
            "leaders": [{"symbol": symbol, "change_24h": change} for symbol, change in ranked_assets[:3]],
            "laggards": [{"symbol": symbol, "change_24h": change} for symbol, change in ranked_assets[-3:]],
            "plain_read": f"追蹤資產中 {len(positive)}/{len(ranked_assets)} 上漲；領漲 {ranked_assets[0][0] if ranked_assets else '未知'}，只代表 24 小時相對強弱。",
        },
        "sector_rotation": {
            "leaders": [{"sector": name, "change_24h": change} for name, change in sector_rank],
            "plain_read": f"賽道領先為 {sector_rank[0][0] if sector_rank else '未知'}；CoinGecko 分類口徑只作輪動背景。",
        },
        "relative_strength": {
            "eth_btc": assets.get("ETH", {}).get("price_usd") / assets.get("BTC", {}).get("price_usd") if assets.get("ETH", {}).get("price_usd") and assets.get("BTC", {}).get("price_usd") else None,
            "hype_sol": assets.get("HYPE", {}).get("price_usd") / assets.get("SOL", {}).get("price_usd") if assets.get("HYPE", {}).get("price_usd") and assets.get("SOL", {}).get("price_usd") else None,
        },
        "asset_rotation": [
            {
                "symbol": symbol,
                "price_usd": item.get("price_usd"),
                "change_24h": item.get("change_24h"),
                "state": item.get("state_24h"),
                "plain_read": (
                    "24 小時相對強勢；仍需成交量與衍生品確認，不能直接追價。"
                    if item.get("state_24h") == "strong"
                    else "24 小時相對弱勢；先判斷是個別事件或市場去風險。"
                    if item.get("state_24h") == "weak"
                    else "24 小時屬中性波動，尚未形成明顯輪動優勢。"
                    if item.get("state_24h") == "neutral"
                    else "資料不足，不做輪動判斷。"
                ),
            }
            for symbol, item in assets.items()
        ],
    }
    for symbol in ("BTC", "ETH"):
        perp = derivatives[symbol]["perpetual"]
        futures = derivatives[symbol]["dated_future"]
        options = derivatives[symbol]["options"]
        funding = perp.get("funding_annualized_median")
        funding_state = "crowded_long" if funding is not None and funding > 0.15 else "short_bias" if funding is not None and funding < 0 else "balanced" if funding is not None else "unknown"
        basis = futures.get("annualized_basis")
        put_call = options.get("put_call_open_interest_ratio")
        volatility = options.get("volatility_value")
        volatility_metric = options.get("volatility_metric")
        volatility_state = (
            "high_risk"
            if volatility_metric == "deribit_dvol" and volatility is not None and volatility > (75 if symbol == "BTC" else 95)
            else "normal"
            if volatility_metric == "deribit_dvol" and volatility is not None
            else "provider_specific_context"
            if volatility is not None
            else "unknown"
        )
        leverage_temperature = (
            "偏多擁擠"
            if funding is not None and basis is not None and funding > 0.15 and basis > 0.10
            else "去槓桿／避險"
            if funding is not None and basis is not None and funding < 0 and basis < 0
            else "中性偏多"
            if funding is not None and funding > 0 and basis is not None and basis > 0
            else "訊號分歧"
        )
        result[symbol] = {
            "funding_state": funding_state,
            "funding_annualized_median": funding,
            "dated_future_basis_annualized": basis,
            "volatility_value": volatility,
            "volatility_metric": volatility_metric,
            "put_call_open_interest_ratio": put_call,
            "leverage_temperature": leverage_temperature,
            "lenses": [
                {"name": "永續資金費率", "value": funding, "state": "hot" if funding is not None and funding > 0.15 else "risk_off" if funding is not None and funding < 0 else "neutral"},
                {"name": "約三個月到期期貨年化基差", "value": basis, "state": "hot" if basis is not None and basis > 0.10 else "risk_off" if basis is not None and basis < 0 else "neutral"},
                {"name": options.get("volatility_label") or "期權隱含波動率", "value": volatility, "state": volatility_state},
                {"name": "Put／Call 未平倉比", "value": put_call, "state": "put_heavy" if put_call is not None and put_call > 1 else "call_heavy" if put_call is not None and put_call < 0.7 else "balanced" if put_call is not None else "unknown"},
            ],
            "plain_read": f"槓桿溫度為「{leverage_temperature}」；期貨基差與期權部位只作擁擠度及風險定價，不直接產生方向交易。",
        }
    return result


def build_btc_thesis(output: dict[str, Any], snapshot: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    btc = output.get("assets", {}).get("BTC", {})
    btc_market_cap = finite(btc.get("market_cap_usd"))
    radar = snapshot.get("metrics", {}).get("market_radar", {})
    btc_supply = finite(radar.get("btc_supply_current"))
    btc_supply_basis = "coin_metrics_snapshot"
    if btc_supply is None and btc_market_cap is not None and finite(btc.get("price_usd")) not in (None, 0):
        btc_supply = btc_market_cap / finite(btc.get("price_usd"))
        btc_supply_basis = "derived_market_cap_divided_by_price"

    gold = inputs.get("gold", {})
    credit = inputs.get("credit", {})
    hashrate = inputs.get("hashrate", {})
    sovereign = inputs.get("sovereign", {})
    gold_market_value = finite(gold.get("estimated_gold_market_value_usd"))
    stablecoin_supply = finite(credit.get("stablecoin_supply_usd"))
    public_company_holdings = finite(output.get("dat", {}).get("BTC", {}).get("total_holdings"))
    observed_companies = output.get("dat", {}).get("BTC", {}).get("companies", [])
    top_company = max(observed_companies, key=lambda item: finite(item.get("holdings")) or 0, default={})
    top_company_holdings = finite(top_company.get("holdings"))

    btc_to_gold = btc_market_cap / gold_market_value if btc_market_cap is not None and gold_market_value not in (None, 0) else None
    btc_to_stablecoin = btc_market_cap / stablecoin_supply if btc_market_cap is not None and stablecoin_supply not in (None, 0) else None
    digital_anchor_share = btc_market_cap / (btc_market_cap + stablecoin_supply) if btc_market_cap is not None and stablecoin_supply is not None and btc_market_cap + stablecoin_supply else None
    public_company_supply_share = public_company_holdings / btc_supply if public_company_holdings is not None and btc_supply not in (None, 0) else None
    top_company_concentration = top_company_holdings / public_company_holdings if top_company_holdings is not None and public_company_holdings not in (None, 0) else None

    scenario_prices = {
        label: gold_market_value * share / btc_supply if gold_market_value is not None and btc_supply not in (None, 0) else None
        for label, share in {"gold_25pct": 0.25, "gold_50pct": 0.50, "gold_100pct": 1.0}.items()
    }
    monetization_stage = (
        "仍屬早期貨幣化"
        if btc_to_gold is not None and btc_to_gold < 0.10
        else "進入規模化貨幣化"
        if btc_to_gold is not None and btc_to_gold < 0.50
        else "接近成熟儲備資產規模"
        if btc_to_gold is not None
        else "未知"
    )
    security_state = (
        "未知"
        if finite(hashrate.get("hashrate_vs_90d_high")) is None or finite(hashrate.get("hashrate_30d_change")) is None
        else
        "安全共識穩固"
        if finite(hashrate.get("hashrate_vs_90d_high")) is not None
        and hashrate["hashrate_vs_90d_high"] >= 0.85
        and finite(hashrate.get("hashrate_30d_change")) is not None
        and hashrate["hashrate_30d_change"] >= -0.10
        else "算力明顯回落"
        if finite(hashrate.get("hashrate_vs_90d_high")) is not None and hashrate["hashrate_vs_90d_high"] < 0.70
        else "安全共識待觀察"
    )
    debt = finite(sovereign.get("us_federal_debt_to_gdp_pct"))
    real_yield = finite(sovereign.get("us_10y_real_yield_pct"))
    sovereign_state = (
        "未知"
        if debt is None or real_yield is None
        else
        "結構壓力高、週期逆風高"
        if debt is not None and debt >= 100 and real_yield is not None and real_yield >= 2
        else "結構壓力高、週期逆風較低"
        if debt is not None and debt >= 100
        else "主權信用壓力中性"
    )

    missing = []
    for name, value in {
        "btc_market_cap": btc_market_cap,
        "btc_supply": btc_supply,
        "gold_market_value": gold_market_value,
        "btc_to_gold_market_value_ratio": btc_to_gold,
        "stablecoin_supply": stablecoin_supply,
        "stablecoin_supply_30d_change": finite(credit.get("stablecoin_supply_30d_change")),
        "rwa_protocol_tvl": finite(credit.get("rwa_protocol_tvl_usd")),
        "public_company_holdings": public_company_holdings,
        "public_company_supply_share": public_company_supply_share,
        "top_company_concentration": top_company_concentration,
        "hashrate_30d_change": finite(hashrate.get("hashrate_30d_change")),
        "hashrate_vs_90d_high": finite(hashrate.get("hashrate_vs_90d_high")),
        "debt_to_gdp": debt,
        "real_yield": real_yield,
    }.items():
        if value is None:
            missing.append(name)

    structural_degradations = []
    structural_failures = list(inputs.get("collector_errors", []))
    if btc_supply_basis != "coin_metrics_snapshot":
        structural_degradations.append("BTC supply uses market-cap/price fallback instead of the primary on-chain snapshot")
    if credit.get("as_of_basis") == "retrieval_time_no_upstream_timestamp":
        structural_degradations.append("Stablecoin and RWA providers expose no uniform upstream observation timestamp")
    if output.get("dat", {}).get("BTC", {}).get("as_of_basis") == "retrieval_time_no_upstream_timestamp":
        structural_degradations.append("Public-company treasury aggregation exposes retrieval time rather than upstream filing time")
    structural_status = "fail" if missing or structural_failures else "degraded" if structural_degradations else "pass"

    return {
        "framework": "BTC non-sovereign monetary anchor and neutral-collateral thesis",
        "model_policy": "Structural adoption evidence only; never enters the short-horizon BTC bottom score or directly releases a trade gate",
        "quality": {
            "status": structural_status,
            "scope": "structural_context_only",
            "coverage_status": "complete" if not missing else "incomplete",
            "missing": missing,
            "failures": structural_failures,
            "degradations": structural_degradations,
            "execution_gate_eligible": False,
        },
        "gold_monetization": {
            "btc_market_cap_usd": btc_market_cap,
            "btc_supply_used": btc_supply,
            "btc_supply_basis": btc_supply_basis,
            "estimated_gold_market_value_usd": gold_market_value,
            "gold_price_proxy_usd_per_troy_oz": finite(gold.get("gold_price_proxy_usd_per_troy_oz")),
            "gold_price_as_of": gold.get("gold_price_as_of"),
            "above_ground_gold_tonnes": finite(gold.get("above_ground_gold_tonnes")),
            "above_ground_stock_year": gold.get("above_ground_stock_year"),
            "btc_to_gold_market_value_ratio": btc_to_gold,
            "stage": monetization_stage,
            "scenario_btc_price_usd": scenario_prices,
            "plain_read": f"BTC 目前約為黃金代理總值的 {btc_to_gold:.1%}；代表貨幣化空間仍大，但情境價不是預測，也不代表需要等額資金流入。" if btc_to_gold is not None else "黃金貨幣化資料不足。",
            "limits": [gold.get("limitation")],
        },
        "digital_dollar_competition": {
            "stablecoin_supply_usd": stablecoin_supply,
            "stablecoin_supply_matched_cohort_usd": finite(credit.get("stablecoin_supply_matched_cohort_usd")),
            "stablecoin_supply_matched_cohort_30d_ago_usd": finite(credit.get("stablecoin_supply_matched_cohort_30d_ago_usd")),
            "stablecoin_supply_30d_change": finite(credit.get("stablecoin_supply_30d_change")),
            "usd_stablecoin_count": credit.get("usd_stablecoin_count"),
            "stablecoin_30d_matched_count": credit.get("stablecoin_30d_matched_count"),
            "stablecoin_30d_unmatched_count": credit.get("stablecoin_30d_unmatched_count"),
            "rwa_protocol_tvl_usd": finite(credit.get("rwa_protocol_tvl_usd")),
            "rwa_protocol_count": credit.get("rwa_protocol_count"),
            "as_of": credit.get("as_of"),
            "as_of_basis": credit.get("as_of_basis"),
            "btc_to_stablecoin_market_scale_ratio": btc_to_stablecoin,
            "btc_share_of_btc_plus_stablecoins": digital_anchor_share,
            "plain_read": (
                f"可比美元穩定幣供給近月增加 {credit['stablecoin_supply_30d_change']:.1%}；這提供鏈上美元交易層擴張的旁證，但不直接否定 BTC 的非主權價值錨假說。"
                if finite(credit.get("stablecoin_supply_30d_change")) is not None and credit["stablecoin_supply_30d_change"] >= 0
                else f"可比美元穩定幣供給近月減少 {abs(credit['stablecoin_supply_30d_change']):.1%}；RWA 只有當期規模，不能據此宣稱整體鏈上信用正在擴張。"
                if finite(credit.get("stablecoin_supply_30d_change")) is not None
                else "穩定幣趨勢不足；RWA 當期規模只能描述可觀測信用層，不能證明成長。"
            ),
            "limits": credit.get("limitations", []),
        },
        "public_company_adoption": {
            "observed_public_company_btc": public_company_holdings,
            "btc_supply_used": btc_supply,
            "share_of_btc_supply": public_company_supply_share,
            "top_company": top_company.get("name"),
            "top_company_btc": top_company_holdings,
            "top_company_share_of_observed_holdings": top_company_concentration,
            "as_of": output.get("dat", {}).get("BTC", {}).get("as_of"),
            "as_of_basis": output.get("dat", {}).get("BTC", {}).get("as_of_basis"),
            "plain_read": f"CoinGecko 公開公司樣本持有約 {public_company_supply_share:.1%} BTC 供給；其中最大公司占樣本 {top_company_concentration:.1%}，採用已有規模但集中度仍高。" if public_company_supply_share is not None and top_company_concentration is not None else "公開公司財庫資料不足。",
            "limitation": "Only CoinGecko's public-company treasury set; excludes private companies, ETFs, governments, custodians and collateral reuse",
        },
        "security_consensus": {
            **hashrate,
            "state": security_state,
            "plain_read": f"算力較 30 日前 {hashrate['hashrate_30d_change']:+.1%}，仍為 90 日高點的 {hashrate['hashrate_vs_90d_high']:.1%}；這只是網路安全投入的代理，不預測價格。" if finite(hashrate.get("hashrate_30d_change")) is not None and finite(hashrate.get("hashrate_vs_90d_high")) is not None else "算力歷史不足。",
        },
        "sovereign_credit_competition": {
            **sovereign,
            "state": sovereign_state,
            "plain_read": f"美國聯邦債務占 GDP {debt:.1f}%，只提供主權信用壓力背景；10 年實質利率 {real_yield:.2f}% 則提高持有無現金流 BTC 的機會成本。" if debt is not None and real_yield is not None else "主權信用資料不足。",
        },
        "unmeasured_falsifier": {
            "name": "全球金融機構以 BTC 作抵押品的存量",
            "status": "unknown_no_complete_public_dataset",
            "plain_read": "ETF、DAT 與衍生品只能證明持有和金融化，不能證明銀行或全球信用市場已把 BTC 當中立抵押品。",
        },
    }


def quality_checks(output: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    failures: list[str] = []
    degradations = list(errors)
    for symbol, asset in output.get("assets", {}).items():
        price = finite(asset.get("price_usd"))
        gap = finite(asset.get("cross_source_gap"))
        if price is None or price <= 0:
            failures.append(f"{symbol} spot price missing")
        if int(asset.get("source_count") or 0) < 2 or gap is None:
            failures.append(f"{symbol} 缺少兩個獨立現貨來源")
        elif gap > 0.02:
            failures.append(f"{symbol} cross-source spot gap {gap:.2%} > 2%")
        for provider, observation in asset.get("source_observations", {}).items():
            provider_age = age_hours(observation.get("as_of"))
            if provider_age is None:
                failures.append(f"{symbol} {provider} 現貨來源時間未知")
            elif provider_age > 2:
                failures.append(f"{symbol} {provider} 現貨來源逾時 {provider_age:.1f} 小時")
            if provider in {"Binance", "OKX"}:
                price_usdt = finite(observation.get("price_usdt"))
                usdt_usd = finite(observation.get("usdt_usd"))
                normalized_price = finite(observation.get("price_usd"))
                usdt_age = age_hours(observation.get("usdt_usd_as_of"))
                if price_usdt is None or usdt_usd is None or normalized_price is None:
                    failures.append(f"{symbol} {provider} USDT/USD 正規化輸入缺失")
                elif abs(price_usdt * usdt_usd - normalized_price) > max(1e-8, normalized_price * 1e-9):
                    failures.append(f"{symbol} {provider} USDT/USD 正規化重算不一致")
                if usdt_age is None or usdt_age > 2:
                    failures.append(f"{symbol} {provider} USDT/USD 匯率逾時或時間未知")
    for symbol in ("BTC", "ETH"):
        derivative = output.get("derivatives", {}).get(symbol, {})
        if int(derivative.get("perpetual", {}).get("funding_source_count") or 0) < 2:
            failures.append(f"{symbol} 缺少兩個場域的可比資金費率")
        perpetual = derivative.get("perpetual", {})
        if perpetual.get("funding_annualized_median") is None:
            failures.append(f"{symbol} cross-venue perpetual funding missing")
        for venue_error in perpetual.get("venue_errors", []):
            degradations.append(f"{symbol} 永續備援來源失敗：{venue_error}")
        for venue in perpetual.get("venues_used", []):
            venue_age = age_hours(perpetual.get(venue, {}).get("as_of"))
            if venue_age is None or venue_age > 2:
                failures.append(f"{symbol} {venue} 永續來源逾時或時間未知")
        dated_future = derivative.get("dated_future", {})
        for fallback_error in dated_future.get("fallback_errors", []):
            degradations.append(f"{symbol} 到期期貨備援：{fallback_error}")
        if dated_future.get("annualized_basis") is None:
            failures.append(f"{symbol} dated-futures basis missing")
        dated_future_age = age_hours(dated_future.get("as_of"))
        if dated_future_age is None or dated_future_age > 2:
            failures.append(f"{symbol} dated-futures source stale or timestamp missing")
        options = derivative.get("options", {})
        for fallback_error in options.get("fallback_errors", []):
            degradations.append(f"{symbol} 期權備援：{fallback_error}")
        if options.get("volatility_value") is None:
            failures.append(f"{symbol} options volatility proxy missing")
        if options.get("put_call_open_interest_ratio") is None:
            failures.append(f"{symbol} options put/call OI missing")
        options_age = age_hours(options.get("as_of"))
        volatility_age = age_hours(options.get("volatility_as_of"))
        if options_age is None or options_age > 2:
            failures.append(f"{symbol} options source stale or timestamp missing")
        if volatility_age is None or volatility_age > 3:
            failures.append(f"{symbol} options volatility source stale or timestamp missing")
        if options.get("open_interest_observed_contracts") != options.get("contracts_observed"):
            failures.append(f"{symbol} options OI coverage incomplete")
        if options.get("volume_observed_contracts") != options.get("contracts_observed"):
            failures.append(f"{symbol} options volume coverage incomplete")
    for sector, item in output.get("sectors", {}).items():
        if finite(item.get("market_cap_usd")) is None or finite(item.get("change_24h")) is None:
            degradations.append(f"{sector} sector data incomplete")
    for asset, item in output.get("dat", {}).items():
        if finite(item.get("total_holdings")) is None:
            degradations.append(f"{asset} DAT aggregate missing")
    if output.get("etf", {}).get("BTC", {}).get("status") != "cross_source_verified":
        degradations.append("BTC ETF 流向僅有第三方單一來源，不作硬觸發")
    btc_etf_age = age_hours(output.get("etf", {}).get("BTC", {}).get("as_of"))
    if btc_etf_age is None or btc_etf_age > 36:
        degradations.append("BTC ETF 流向底層每日快照逾時或時間未知，因此不顯示數值")
    if output.get("etf", {}).get("ETH", {}).get("status") != "cross_source_verified":
        degradations.append("ETH ETF 流向沒有穩定的免金鑰交叉來源，因此顯示未知")
    score = max(0, 100 - len(failures) * 25 - len(degradations) * 4)
    return {
        "status": "fail" if failures else "degraded" if degradations else "pass",
        "score_0_100": score,
        "failures": failures,
        "degradations": degradations,
        "policy": "缺失或分歧資料維持未知；場域觀測不得外推為全球市場。",
    }


def compact_history(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": output["date"],
        "generated_at": output["generated_at"],
        "assets": {symbol: {key: item.get(key) for key in ("price_usd", "change_24h", "market_cap_usd")} for symbol, item in output["assets"].items()},
        "derivatives": {symbol: {
            "funding_annualized_median": output["derivatives"][symbol]["perpetual"].get("funding_annualized_median"),
            "quarterly_basis_annualized": output["derivatives"][symbol]["dated_future"].get("annualized_basis"),
            "dated_future_provider": output["derivatives"][symbol]["dated_future"].get("provider"),
            "dvol": output["derivatives"][symbol]["options"].get("dvol"),
            "volatility_value": output["derivatives"][symbol]["options"].get("volatility_value"),
            "volatility_metric": output["derivatives"][symbol]["options"].get("volatility_metric"),
            "options_provider": output["derivatives"][symbol]["options"].get("provider"),
            "put_call_open_interest_ratio": output["derivatives"][symbol]["options"].get("put_call_open_interest_ratio"),
        } for symbol in ("BTC", "ETH")},
        "sectors": {name: item.get("change_24h") for name, item in output["sectors"].items()},
        "dat": {asset: {
            "total_holdings": item.get("total_holdings"),
            "companies": {company.get("symbol"): company.get("holdings") for company in item.get("companies", []) if company.get("symbol")},
        } for asset, item in output["dat"].items()},
        "btc_thesis": {
            "btc_to_gold_market_value_ratio": output.get("btc_thesis", {}).get("gold_monetization", {}).get("btc_to_gold_market_value_ratio"),
            "gold_price_as_of": output.get("btc_thesis", {}).get("gold_monetization", {}).get("gold_price_as_of"),
            "stablecoin_supply_usd": output.get("btc_thesis", {}).get("digital_dollar_competition", {}).get("stablecoin_supply_usd"),
            "stablecoin_supply_30d_change": output.get("btc_thesis", {}).get("digital_dollar_competition", {}).get("stablecoin_supply_30d_change"),
            "stablecoin_as_of": output.get("btc_thesis", {}).get("digital_dollar_competition", {}).get("as_of"),
            "public_company_share_of_supply": output.get("btc_thesis", {}).get("public_company_adoption", {}).get("share_of_btc_supply"),
            "public_company_as_of": output.get("btc_thesis", {}).get("public_company_adoption", {}).get("as_of"),
            "hashrate_vs_90d_high": output.get("btc_thesis", {}).get("security_consensus", {}).get("hashrate_vs_90d_high"),
            "hashrate_as_of": output.get("btc_thesis", {}).get("security_consensus", {}).get("as_of"),
            "us_10y_real_yield_pct": output.get("btc_thesis", {}).get("sovereign_credit_competition", {}).get("us_10y_real_yield_pct"),
            "us_10y_real_yield_as_of": output.get("btc_thesis", {}).get("sovereign_credit_competition", {}).get("us_10y_real_yield_as_of"),
            "us_federal_debt_to_gdp_as_of": output.get("btc_thesis", {}).get("sovereign_credit_competition", {}).get("us_federal_debt_to_gdp_as_of"),
            "quality_status": output.get("btc_thesis", {}).get("quality", {}).get("status"),
        },
        "quality_status": output["quality"]["status"],
    }


def main() -> int:
    collectors: dict[str, Callable[[], tuple[Any, list[dict[str, Any]]]]] = {
        "coingecko": collect_coingecko,
        "binance_spot": collect_binance_spot,
        "okx_spot": collect_okx_spot,
        "coinbase_spot": collect_coinbase_spot,
        "hyperliquid": collect_hyperliquid,
        "categories": collect_categories,
        "btc_perpetual": lambda: collect_perpetual("BTC"),
        "eth_perpetual": lambda: collect_perpetual("ETH"),
        "btc_future": lambda: collect_dated_future("BTC"),
        "eth_future": lambda: collect_dated_future("ETH"),
        "btc_cme": lambda: collect_cme_proxy("BTC"),
        "eth_cme": lambda: collect_cme_proxy("ETH"),
        "btc_options": lambda: collect_options("BTC"),
        "eth_options": lambda: collect_options("ETH"),
        "btc_dat": lambda: collect_dat_treasuries("BTC"),
        "eth_dat": lambda: collect_dat_treasuries("ETH"),
        "thesis_credit": collect_stablecoin_and_rwa_credit,
        "thesis_gold": collect_gold_reference,
        "thesis_hashrate": collect_hashrate_consensus,
        "thesis_sovereign": collect_sovereign_credit,
    }
    results: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    execution_errors: list[str] = []
    structural_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(collector): name for name, collector in collectors.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name], result_sources = future.result()
                sources.extend(result_sources)
            except Exception as exc:
                provider = {
                    "binance_spot": "Binance 現貨",
                    "okx_spot": "OKX 現貨",
                    "coinbase_spot": "Coinbase 現貨",
                    "coingecko": "CoinGecko",
                    "categories": "CoinGecko 賽道分類",
                }.get(name, name)
                target_errors = structural_errors if name in STRUCTURAL_COLLECTOR_NAMES else execution_errors
                target_errors.append(readable_source_error(provider, exc))
                results[name] = {}

    coingecko = results.get("coingecko", {})
    binance = results.get("binance_spot", {})
    okx = results.get("okx_spot", {})
    coinbase = results.get("coinbase_spot", {})
    hyperliquid = results.get("hyperliquid", {})
    assets: dict[str, Any] = {}
    for symbol in ASSETS:
        provider_prices = {
            "CoinGecko": finite(coingecko.get(symbol, {}).get("price_usd")),
            "Binance": finite(binance.get(symbol, {}).get("price_usd")),
            "OKX": finite(okx.get(symbol, {}).get("price_usd")),
            "Coinbase": finite(coinbase.get(symbol, {}).get("price_usd")),
        }
        source_observations = {
            "CoinGecko": {"price_usd": finite(coingecko.get(symbol, {}).get("price_usd")), "as_of": coingecko.get(symbol, {}).get("as_of"), "quote_asset": "USD aggregate"},
            "Binance": {
                "price_usd": finite(binance.get(symbol, {}).get("price_usd")),
                "price_usdt": finite(binance.get(symbol, {}).get("price_usdt")),
                "usdt_usd": finite(binance.get(symbol, {}).get("usdt_usd")),
                "usdt_usd_as_of": binance.get(symbol, {}).get("usdt_usd_as_of"),
                "as_of": binance.get(symbol, {}).get("as_of"),
                "quote_asset": "USDT normalized to USD",
            },
            "OKX": {
                "price_usd": finite(okx.get(symbol, {}).get("price_usd")),
                "price_usdt": finite(okx.get(symbol, {}).get("price_usdt")),
                "usdt_usd": finite(okx.get(symbol, {}).get("usdt_usd")),
                "usdt_usd_as_of": okx.get(symbol, {}).get("usdt_usd_as_of"),
                "as_of": okx.get(symbol, {}).get("as_of"),
                "quote_asset": "USDT normalized to USD",
            },
            "Coinbase": {"price_usd": finite(coinbase.get(symbol, {}).get("price_usd")), "as_of": coinbase.get(symbol, {}).get("as_of"), "quote_asset": "USD"},
        }
        clean_prices = [value for value in provider_prices.values() if value is not None]
        base = dict(coingecko.get(symbol, {}))
        base.update({
            "price_usd": statistics.median(clean_prices) if len(clean_prices) >= 2 else None,
            "unverified_reference_price_usd": statistics.median(clean_prices) if clean_prices else None,
            "source_prices": {name: value for name, value in provider_prices.items() if value is not None},
            "source_observations": {name: item for name, item in source_observations.items() if item["price_usd"] is not None},
            "cross_source_gap": cross_source_gap(clean_prices),
            "source_count": len(clean_prices),
            "state_24h": state_from_change(base.get("change_24h")),
        })
        assets[symbol] = base

    snapshot = load_json(SNAPSHOT_PATH, {})
    radar = snapshot.get("metrics", {}).get("market_radar", {})
    btc_etf_status = radar.get("etf_flow_status", "unavailable")
    btc_etf_as_of = snapshot.get("generated_at")
    btc_etf_fresh = age_hours(btc_etf_as_of) is not None and age_hours(btc_etf_as_of) <= 36
    history = load_json(HISTORY_PATH, {"schema": 1, "updated_at": None, "items": []})
    previous_items = sorted([item for item in history.get("items", []) if item.get("date") != today_utc()], key=lambda item: item.get("date", ""))
    previous = previous_items[-1] if previous_items else {}
    dat = {"BTC": results.get("btc_dat", {}), "ETH": results.get("eth_dat", {})}
    for asset, item in dat.items():
        prior = previous.get("dat", {}).get(asset, {})
        prior_total = finite(prior.get("total_holdings")) if isinstance(prior, dict) else finite(prior)
        current_total = finite(item.get("total_holdings"))
        item["total_holdings_change"] = current_total - prior_total if current_total is not None and prior_total is not None else None
        prior_companies = prior.get("companies", {}) if isinstance(prior, dict) else {}
        for company in item.get("companies", []):
            prior_holding = finite(prior_companies.get(company.get("symbol")))
            current_holding = finite(company.get("holdings"))
            company["holdings_change"] = current_holding - prior_holding if current_holding is not None and prior_holding is not None else None

    output: dict[str, Any] = {
        "schema": 1,
        "date": today_utc(),
        "generated_at": now_iso(),
        "update_target": "every_4_hours",
        "units": {
            "*_usd": "US dollars",
            "*_ratio|basis|change|distance|gap": "decimal fraction; 0.01 means 1%",
            "funding_*": "decimal fraction for the named interval; annualized fields use simple annualization",
            "atm_implied_volatility|dvol|volatility_value": "percentage points; 34.5 means 34.5%",
            "open_interest_base": "base-asset units for the named venue",
        },
        "assets": assets,
        "derivatives": {
            "BTC": {"perpetual": results.get("btc_perpetual", {}), "dated_future": results.get("btc_future", {}), "cme_proxy": results.get("btc_cme", {}), "options": results.get("btc_options", {})},
            "ETH": {"perpetual": results.get("eth_perpetual", {}), "dated_future": results.get("eth_future", {}), "cme_proxy": results.get("eth_cme", {}), "options": results.get("eth_options", {})},
            "HYPE": {"perpetual": hyperliquid.get("HYPE", {})},
        },
        "etf": {
            "BTC": {
                "status": btc_etf_status,
                "flow_1d_usd": finite(radar.get("etf_flow_1d_usd")) if btc_etf_fresh else None,
                "flow_7d_usd": finite(radar.get("etf_flow_7d_usd")) if btc_etf_fresh else None,
                "flow_30d_usd": finite(radar.get("etf_flow_30d_usd")) if btc_etf_fresh else None,
                "as_of": btc_etf_as_of,
                "update_frequency": "daily",
                "source": "WalletPilot via latest_snapshot",
                "hard_trigger": False,
                "limitation": "第三方單一來源，尚未獨立交叉驗證",
            },
            "ETH": {
                "status": "unavailable_no_stable_keyless_cross_source_feed",
                "flow_1d_usd": None,
                "flow_7d_usd": None,
                "flow_30d_usd": None,
                "hard_trigger": False,
                "manual_refs": ["https://farside.co.uk/eth/", "https://www.coinglass.com/etf/ethereum"],
                "limitation": "沒有穩定的免金鑰交叉來源，因此維持未知，不以不穩定網頁或未驗證單一數字補值",
            },
        },
        "dat": dat,
        "sectors": results.get("categories", {}),
        "sources": sorted(sources, key=lambda item: item["source_id"]),
        "collector_errors": execution_errors,
    }
    thesis_inputs = {
        "credit": results.get("thesis_credit", {}),
        "gold": results.get("thesis_gold", {}),
        "hashrate": results.get("thesis_hashrate", {}),
        "sovereign": results.get("thesis_sovereign", {}),
        "collector_errors": structural_errors,
    }
    output["btc_thesis"] = build_btc_thesis(output, snapshot, thesis_inputs)
    output["analysis"] = analyze(output)
    output["quality"] = quality_checks(output, execution_errors)
    write_json(OUTPUT_PATH, output)

    items = [item for item in history.get("items", []) if item.get("date") != output["date"]]
    items.append(compact_history(output))
    items.sort(key=lambda item: item.get("date", ""))
    history.update({"schema": 1, "updated_at": now_iso(), "items": items[-730:]})
    write_json(HISTORY_PATH, history)
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "quality": output["quality"]["status"],
        "sources": len(output["sources"]),
        "execution_errors": len(execution_errors),
        "structural_errors": len(structural_errors),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
