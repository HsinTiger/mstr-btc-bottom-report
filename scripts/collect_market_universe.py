#!/usr/bin/env python3
"""Collect cross-asset spot, derivatives, sector, ETF, and DAT market data.

All sources are public and require no API key. Exchange observations remain
venue-specific; partial exchange coverage is never labeled as the whole market.
"""

from __future__ import annotations

import json
import math
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

ASSETS = {
    "BTC": {"coingecko": "bitcoin", "binance": "BTCUSDT", "coinbase": "BTC-USD"},
    "ETH": {"coingecko": "ethereum", "binance": "ETHUSDT", "coinbase": "ETH-USD"},
    "HYPE": {"coingecko": "hyperliquid", "coinbase": "HYPE-USD", "hyperliquid": "HYPE"},
    "SOL": {"coingecko": "solana", "binance": "SOLUSDT", "coinbase": "SOL-USD"},
    "BNB": {"coingecko": "binancecoin", "binance": "BNBUSDT"},
    "XRP": {"coingecko": "ripple", "binance": "XRPUSDT", "coinbase": "XRP-USD"},
    "DOGE": {"coingecko": "dogecoin", "binance": "DOGEUSDT", "coinbase": "DOGE-USD"},
}

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
                "quote_volume_24h_usd": finite(row.get("quoteVolume")),
                "as_of": datetime.fromtimestamp(row_close_time / 1000, timezone.utc).isoformat() if row_close_time else None,
            }
    return result, [
        source("binance_spot", "Binance Spot", url, "primary_market", timestamp, "交易所 USDT 現貨報價；先以 Coinbase USDT/USD 換算美元"),
        source("coinbase_usdt_usd", "Coinbase Exchange", usdt_url, "primary_market", usdt_row.get("time"), "Binance USDT 報價的美元正規化匯率"),
    ]


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


def collect_perpetual(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pair = f"{symbol}USDT"
    binance_premium_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={pair}"
    binance_oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={pair}"
    binance_ticker_url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={pair}"
    bybit_url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={pair}"
    bybit_instrument_url = f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={pair}"
    premium = fetch_json(binance_premium_url)
    oi = fetch_json(binance_oi_url)
    ticker = fetch_json(binance_ticker_url)
    bybit_payload = fetch_json(bybit_url)
    bybit = (bybit_payload.get("result", {}).get("list") or [{}])[0]
    bybit_instrument_payload = fetch_json(bybit_instrument_url)
    bybit_instrument = (bybit_instrument_payload.get("result", {}).get("list") or [{}])[0]
    binance_mark = finite(premium.get("markPrice"))
    binance_oi_base = finite(oi.get("openInterest"))
    binance_funding = finite(premium.get("lastFundingRate"))
    bybit_funding = finite(bybit.get("fundingRate"))
    binance_interval_hours = 8.0
    bybit_interval_minutes = finite(bybit_instrument.get("fundingInterval"))
    bybit_interval_hours = bybit_interval_minutes / 60 if bybit_interval_minutes not in (None, 0) else None
    binance_funding_annualized = binance_funding * 24 / binance_interval_hours * 365 if binance_funding is not None else None
    bybit_funding_annualized = bybit_funding * 24 / bybit_interval_hours * 365 if bybit_funding is not None and bybit_interval_hours else None
    funding_values = [value for value in (binance_funding, bybit_funding) if value is not None]
    annualized_funding_values = [value for value in (binance_funding_annualized, bybit_funding_annualized) if value is not None]
    funding_intervals = [value for value in (binance_interval_hours, bybit_interval_hours) if value is not None]
    funding_interval_consistent = len(funding_intervals) == 2 and len(set(funding_intervals)) == 1
    result = {
        "coverage": "Binance USD-M + Bybit linear; partial observable market",
        "binance": {
            "mark_price_usd": binance_mark,
            "index_price_usd": finite(premium.get("indexPrice")),
            "funding_8h": binance_funding,
            "funding_rate": binance_funding,
            "funding_interval_hours": binance_interval_hours,
            "funding_annualized": binance_funding_annualized,
            "open_interest_base": binance_oi_base,
            "open_interest_usd": binance_oi_base * binance_mark if binance_oi_base is not None and binance_mark is not None else None,
            "volume_24h_usd": finite(ticker.get("quoteVolume")),
            "as_of": datetime.fromtimestamp((premium.get("time") or 0) / 1000, timezone.utc).isoformat() if premium.get("time") else None,
        },
        "bybit": {
            "mark_price_usd": finite(bybit.get("markPrice")),
            "index_price_usd": finite(bybit.get("indexPrice")),
            "funding_8h": bybit_funding if bybit_interval_hours == 8 else None,
            "funding_rate": bybit_funding,
            "funding_interval_hours": bybit_interval_hours,
            "funding_annualized": bybit_funding_annualized,
            "open_interest_base": finite(bybit.get("openInterest")),
            "open_interest_usd": finite(bybit.get("openInterestValue")),
            "volume_24h_usd": finite(bybit.get("turnover24h")),
            "as_of": datetime.fromtimestamp((bybit_payload.get("time") or 0) / 1000, timezone.utc).isoformat() if bybit_payload.get("time") else None,
        },
        "funding_8h_median": statistics.median(funding_values) if funding_interval_consistent and funding_intervals[0] == 8 else None,
        "funding_annualized_median": statistics.median(annualized_funding_values) if annualized_funding_values else None,
        "funding_source_count": len(annualized_funding_values),
        "funding_interval_hours_consistent": funding_interval_consistent,
        "funding_cross_venue_gap_bps": abs(binance_funding - bybit_funding) * 10_000 if funding_interval_consistent and binance_funding is not None and bybit_funding is not None else None,
        "funding_annualized_cross_venue_gap_bps": abs(binance_funding_annualized - bybit_funding_annualized) * 10_000 if binance_funding_annualized is not None and bybit_funding_annualized is not None else None,
        "observed_open_interest_usd": sum(value for value in [binance_oi_base * binance_mark if binance_oi_base is not None and binance_mark is not None else None, finite(bybit.get("openInterestValue"))] if value is not None) or None,
    }
    sources = [
        source(f"binance_{symbol.lower()}_perp", "Binance USD-M Futures", binance_premium_url, "primary_derivatives_market", result["binance"]["as_of"], "永續標記價、指數價、8 小時資金費率、未平倉量與 24 小時成交額"),
        source(f"bybit_{symbol.lower()}_perp", "Bybit Linear", bybit_url, "primary_derivatives_market", result["bybit"]["as_of"], f"永續標記價、指數價、{bybit_interval_hours:g} 小時資金費率、未平倉量與 24 小時成交額" if bybit_interval_hours else "永續資料；資金費率週期未知"),
    ]
    return result, sources


def collect_dated_future(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    exchange_url = "https://dapi.binance.com/dapi/v1/exchangeInfo"
    exchange = fetch_json(exchange_url)
    pair = f"{symbol}USD"
    contracts = [row for row in exchange.get("symbols", []) if row.get("pair") == pair and row.get("contractType") == "CURRENT_QUARTER" and row.get("contractStatus") == "TRADING"]
    if not contracts:
        raise ValueError(f"No current quarterly contract for {pair}")
    contract = min(contracts, key=lambda row: row.get("deliveryDate") or 9e18)
    premium_url = f"https://dapi.binance.com/dapi/v1/premiumIndex?symbol={contract['symbol']}"
    premium = fetch_json(premium_url)
    if isinstance(premium, list):
        premium = next((row for row in premium if row.get("symbol") == contract["symbol"]), {})
    mark = finite(premium.get("markPrice"))
    index = finite(premium.get("indexPrice"))
    delivery = datetime.fromtimestamp(contract["deliveryDate"] / 1000, timezone.utc)
    days = max((delivery - datetime.now(timezone.utc)).total_seconds() / 86400, 0)
    basis = mark / index - 1 if mark is not None and index not in (None, 0) else None
    result = {
        "provider": "Binance COIN-M",
        "contract": contract["symbol"],
        "delivery_date": delivery.date().isoformat(),
        "days_to_delivery": days,
        "mark_price_usd": mark,
        "index_price_usd": index,
        "basis": basis,
        "annualized_basis": basis * 365 / days if basis is not None and days > 0 else None,
        "as_of": datetime.fromtimestamp((premium.get("time") or 0) / 1000, timezone.utc).isoformat() if premium.get("time") else None,
    }
    return result, [source(f"binance_{symbol.lower()}_quarterly", "Binance COIN-M Futures", premium_url, "primary_derivatives_market", result["as_of"], "當季期貨標記價相對指數價的簡單年化基差")]


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


def collect_options(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        "max_pain_usd": max_pain,
        "max_pain_distance": max_pain / underlying - 1 if max_pain is not None and underlying else None,
        "as_of": options_as_of,
        "limits": ["Max pain is a descriptive OI concentration, not a price target", "Put/call OI does not identify trade direction or buyer/seller intent"],
    }
    return result, [
        source(f"deribit_{symbol.lower()}_options", "Deribit", summary_url, "primary_derivatives_market", result["as_of"], "Deribit 期權未平倉量、隱含波動率、成交額與自算最大痛點集中價"),
        source(f"deribit_{symbol.lower()}_dvol", "Deribit DVOL", dvol_url, "primary_derivatives_market", dvol_as_of, "最近一個完整小時的 Deribit 隱含波動率指數收盤值"),
    ]


def collect_dat_treasuries(asset: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coin = "bitcoin" if asset == "BTC" else "ethereum"
    url = f"https://api.coingecko.com/api/v3/companies/public_treasury/{coin}"
    payload = fetch_json(url)
    companies = []
    for row in payload.get("companies", [])[:8]:
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
        dvol = options.get("dvol")
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
            "quarterly_basis_annualized": basis,
            "dvol": dvol,
            "put_call_open_interest_ratio": put_call,
            "leverage_temperature": leverage_temperature,
            "lenses": [
                {"name": "永續資金費率", "value": funding, "state": "hot" if funding is not None and funding > 0.15 else "risk_off" if funding is not None and funding < 0 else "neutral"},
                {"name": "季度期貨年化基差", "value": basis, "state": "hot" if basis is not None and basis > 0.10 else "risk_off" if basis is not None and basis < 0 else "neutral"},
                {"name": "期權隱含波動 DVOL", "value": dvol, "state": "high_risk" if dvol is not None and dvol > (75 if symbol == "BTC" else 95) else "normal" if dvol is not None else "unknown"},
                {"name": "Put／Call 未平倉比", "value": put_call, "state": "put_heavy" if put_call is not None and put_call > 1 else "call_heavy" if put_call is not None and put_call < 0.7 else "balanced" if put_call is not None else "unknown"},
            ],
            "plain_read": f"槓桿溫度為「{leverage_temperature}」；期貨基差與期權部位只作擁擠度及風險定價，不直接產生方向交易。",
        }
    return result


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
            if provider == "Binance":
                price_usdt = finite(observation.get("price_usdt"))
                usdt_usd = finite(observation.get("usdt_usd"))
                normalized_price = finite(observation.get("price_usd"))
                usdt_age = age_hours(observation.get("usdt_usd_as_of"))
                if price_usdt is None or usdt_usd is None or normalized_price is None:
                    failures.append(f"{symbol} Binance USDT/USD 正規化輸入缺失")
                elif abs(price_usdt * usdt_usd - normalized_price) > max(1e-8, normalized_price * 1e-9):
                    failures.append(f"{symbol} Binance USDT/USD 正規化重算不一致")
                if usdt_age is None or usdt_age > 2:
                    failures.append(f"{symbol} Binance USDT/USD 匯率逾時或時間未知")
    for symbol in ("BTC", "ETH"):
        derivative = output.get("derivatives", {}).get(symbol, {})
        if int(derivative.get("perpetual", {}).get("funding_source_count") or 0) < 2:
            failures.append(f"{symbol} 缺少兩個場域的可比資金費率")
        if derivative.get("perpetual", {}).get("funding_interval_hours_consistent") is not True:
            degradations.append(f"{symbol} 場域資金費率週期不同；只比較正規化年化值")
        if derivative.get("perpetual", {}).get("funding_annualized_median") is None:
            failures.append(f"{symbol} cross-venue perpetual funding missing")
        for venue in ("binance", "bybit"):
            venue_age = age_hours(derivative.get("perpetual", {}).get(venue, {}).get("as_of"))
            if venue_age is None or venue_age > 2:
                failures.append(f"{symbol} {venue} 永續來源逾時或時間未知")
        dated_future = derivative.get("dated_future", {})
        if dated_future.get("annualized_basis") is None:
            failures.append(f"{symbol} dated-futures basis missing")
        dated_future_age = age_hours(dated_future.get("as_of"))
        if dated_future_age is None or dated_future_age > 2:
            failures.append(f"{symbol} dated-futures source stale or timestamp missing")
        options = derivative.get("options", {})
        if options.get("dvol") is None:
            failures.append(f"{symbol} options DVOL missing")
        if options.get("put_call_open_interest_ratio") is None:
            failures.append(f"{symbol} options put/call OI missing")
        options_age = age_hours(options.get("as_of"))
        dvol_age = age_hours(options.get("dvol_as_of"))
        if options_age is None or options_age > 2:
            failures.append(f"{symbol} options source stale or timestamp missing")
        if dvol_age is None or dvol_age > 3:
            failures.append(f"{symbol} DVOL source stale or timestamp missing")
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
            "dvol": output["derivatives"][symbol]["options"].get("dvol"),
            "put_call_open_interest_ratio": output["derivatives"][symbol]["options"].get("put_call_open_interest_ratio"),
        } for symbol in ("BTC", "ETH")},
        "sectors": {name: item.get("change_24h") for name, item in output["sectors"].items()},
        "dat": {asset: {
            "total_holdings": item.get("total_holdings"),
            "companies": {company.get("symbol"): company.get("holdings") for company in item.get("companies", []) if company.get("symbol")},
        } for asset, item in output["dat"].items()},
        "quality_status": output["quality"]["status"],
    }


def main() -> int:
    collectors: dict[str, Callable[[], tuple[Any, list[dict[str, Any]]]]] = {
        "coingecko": collect_coingecko,
        "binance_spot": collect_binance_spot,
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
    }
    results: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(collector): name for name, collector in collectors.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name], result_sources = future.result()
                sources.extend(result_sources)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                results[name] = {}

    coingecko = results.get("coingecko", {})
    binance = results.get("binance_spot", {})
    coinbase = results.get("coinbase_spot", {})
    hyperliquid = results.get("hyperliquid", {})
    assets: dict[str, Any] = {}
    for symbol in ASSETS:
        provider_prices = {
            "CoinGecko": finite(coingecko.get(symbol, {}).get("price_usd")),
            "Binance": finite(binance.get(symbol, {}).get("price_usd")),
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
            "atm_implied_volatility|dvol": "percentage points; 34.5 means 34.5%",
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
        "collector_errors": errors,
    }
    output["analysis"] = analyze(output)
    output["quality"] = quality_checks(output, errors)
    write_json(OUTPUT_PATH, output)

    items = [item for item in history.get("items", []) if item.get("date") != output["date"]]
    items.append(compact_history(output))
    items.sort(key=lambda item: item.get("date", ""))
    history.update({"schema": 1, "updated_at": now_iso(), "items": items[-730:]})
    write_json(HISTORY_PATH, history)
    print(json.dumps({"output": str(OUTPUT_PATH), "quality": output["quality"]["status"], "sources": len(output["sources"]), "errors": len(errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
