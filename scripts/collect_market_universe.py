#!/usr/bin/env python3
"""Collect cross-asset spot, derivatives, sector, ETF, and DAT market data.

All sources are public and require no API key. Exchange observations remain
venue-specific; partial exchange coverage is never labeled as the whole market.
"""

from __future__ import annotations

import json
import math
import csv
import html
import io
import re
import statistics
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
OUTPUT_PATH = DATA_DIR / "market_universe.json"
HISTORY_PATH = DATA_DIR / "market_universe_history.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
RAW_PATH = DATA_DIR / "raw_observations.json"
USER_AGENT = "mstr-btc-bottom-report/market-universe hsin73@realtek.com"
TROY_OZ_PER_METRIC_TONNE = 32_150.746568627
FRESHNESS_CONTRACT = {
    "artifact_max_age_hours": 3,
    "spot_source_max_lag_hours": 2,
    "perpetual_source_max_lag_hours": 2,
    "dated_future_source_max_lag_hours": 2,
    "options_source_max_lag_hours": 2,
    "volatility_source_max_lag_hours": 3,
    "etf_source_max_lag_days": 5,
    "thesis_gold_max_lag_hours": 72,
    "thesis_credit_max_lag_hours": 36,
    "thesis_company_max_lag_hours": 8,
    "thesis_hashrate_max_lag_hours": 72,
    "thesis_debt_max_lag_hours": 24 * 240,
    "thesis_real_yield_max_lag_hours": 24 * 10,
    "timestamp_semantics": "source lag is validated against this artifact's generated_at; artifact age is validated separately against current time",
}

ASSETS = {
    "BTC": {"coingecko": "bitcoin", "binance": "BTCUSDT", "coinbase": "BTC-USD", "kraken": "XBTUSD"},
    "ETH": {"coingecko": "ethereum", "binance": "ETHUSDT", "coinbase": "ETH-USD", "kraken": "ETHUSD"},
    "HYPE": {"coingecko": "hyperliquid", "coinbase": "HYPE-USD", "hyperliquid": "HYPE"},
    "SOL": {"coingecko": "solana", "binance": "SOLUSDT", "coinbase": "SOL-USD", "kraken": "SOLUSD"},
    "BNB": {"coingecko": "binancecoin", "binance": "BNBUSDT"},
    "XRP": {"coingecko": "ripple", "binance": "XRPUSDT", "coinbase": "XRP-USD", "kraken": "XRPUSD"},
    "DOGE": {"coingecko": "dogecoin", "binance": "DOGEUSDT", "coinbase": "DOGE-USD", "kraken": "XDGUSD"},
}
STRUCTURAL_COLLECTOR_NAMES = {"thesis_credit", "thesis_gold", "thesis_hashrate", "thesis_sovereign"}

SECTOR_BASKETS = {
    "RWA": ["ONDO", "LINK", "XLM", "PAXG", "XAUT"],
    "Layer 1": ["BTC", "ETH", "BNB", "SOL", "XRP"],
    "DeFi": ["UNI", "AAVE", "LDO", "ENA", "PENDLE"],
    "Meme": ["DOGE", "SHIB", "PEPE", "BONK", "WIF"],
}
SECTOR_BASKET_VERSION = "fixed-basket-v1"
SECTOR_COINGECKO_IDS = {
    "ONDO": "ondo-finance", "LINK": "chainlink", "XLM": "stellar", "PAXG": "pax-gold", "XAUT": "tether-gold",
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana", "XRP": "ripple",
    "UNI": "uniswap", "AAVE": "aave", "LDO": "lido-dao", "ENA": "ethena", "PENDLE": "pendle",
    "DOGE": "dogecoin", "SHIB": "shiba-inu", "PEPE": "pepe", "BONK": "bonk", "WIF": "dogwifcoin",
}
SECTOR_SOURCE_MAX_LAG_HOURS = 0.25
SECTOR_MAX_RETURN_GAP = 0.01


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


def calendar_day_lag(reference: Any, value: Any) -> int | None:
    if not reference or not value:
        return None
    try:
        reference_date = datetime.fromisoformat(str(reference).replace("Z", "+00:00")).date()
        observed_date = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        return (reference_date - observed_date).days
    except ValueError:
        return None


def lag_hours_at(reference: Any, value: Any) -> float | None:
    if not reference or not value:
        return None
    try:
        reference_time = datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
        observed_time = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        if observed_time.tzinfo is None:
            observed_time = observed_time.replace(tzinfo=timezone.utc)
        return (reference_time - observed_time).total_seconds() / 3600
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


class TreasuryTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_holders = False
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.in_company_link = False
        self.in_badge = False
        self.cells: list[str] = []
        self.cell_parts: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self.row_slug: str | None = None
        self.row_name_parts: list[str] = []
        self.row_badges: list[str] = []
        self.company_cell_index: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == "holders":
            self.in_holders = True
        if self.in_holders and tag == "table" and attributes.get("data-slot") == "table" and not self.in_table:
            self.in_table = True
        if not self.in_table:
            return
        if tag == "tr":
            self.in_row = True
            self.cells = []
            self.row_slug = None
            self.row_name_parts = []
            self.row_badges = []
            self.company_cell_index = None
        elif self.in_row and tag == "td":
            self.in_cell = True
            self.cell_parts = []
        elif self.in_cell and tag == "a" and str(attributes.get("href") or "").startswith("/public-companies/"):
            if self.row_slug is None:
                self.row_slug = str(attributes["href"])
                self.company_cell_index = len(self.cells)
                self.in_company_link = True
        elif self.in_cell and tag == "span" and attributes.get("data-slot") == "badge":
            self.in_badge = True

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag == "a":
            self.in_company_link = False
        elif self.in_table and tag == "span":
            self.in_badge = False
        elif self.in_table and self.in_row and tag == "td":
            self.cells.append(re.sub(r"\s+", " ", " ".join(self.cell_parts)).strip())
            self.cell_parts = []
            self.in_cell = False
        elif self.in_table and self.in_row and tag == "tr":
            if self.row_slug and self.company_cell_index is not None:
                company_cell = self.cells[self.company_cell_index] if self.company_cell_index < len(self.cells) else ""
                ticker = next((item for item in self.row_badges if re.fullmatch(r"[A-Z0-9.]{1,10}", item)), None)
                if ticker is None:
                    ticker = next(
                        (item for item in reversed(company_cell.split()) if re.fullmatch(r"[A-Z0-9.]{1,10}", item)),
                        None,
                    )
                holdings_cell_index = self.company_cell_index + 1
                holdings_text = self.cells[holdings_cell_index] if holdings_cell_index < len(self.cells) else ""
                match = re.search(r"([\d,]+(?:\.\d+)?)", holdings_text)
                holdings = finite(match.group(1).replace(",", "")) if match else None
                if ticker and holdings is not None:
                    self.rows.append({
                        "name": re.sub(r"\s+", " ", " ".join(self.row_name_parts)).strip(),
                        "symbol": ticker,
                        "holdings": holdings,
                        "detail_path": self.row_slug,
                    })
            self.in_row = False
        elif self.in_table and tag == "table":
            self.in_table = False
        elif self.in_holders and tag == "section":
            self.in_holders = False

    def handle_data(self, data: str) -> None:
        if not self.in_cell:
            return
        clean = data.strip()
        if clean:
            self.cell_parts.append(clean)
            if self.in_company_link:
                self.row_name_parts.append(clean)
            if self.in_badge:
                self.row_badges.append(clean)


class BitboTreasuryTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cell_class = ""
        self.cell_parts: list[str] = []
        self.cells: dict[str, str] = {}
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.cells = {}
        elif self.in_row and tag == "td":
            self.in_cell = True
            classes = str(attributes.get("class") or "").split()
            self.cell_class = classes[0] if classes else f"unnamed-{len(self.cells)}"
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if self.in_cell and clean:
            self.cell_parts.append(clean)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag == "td":
            self.cells[self.cell_class] = re.sub(r"\s+", " ", " ".join(self.cell_parts)).strip()
            self.in_cell = False
            self.cell_class = ""
            self.cell_parts = []
        elif self.in_row and tag == "tr":
            symbol = self.cells.get("td-symbol", "").split(":")[0].strip().upper()
            holdings_match = re.search(r"[\d,]+(?:\.\d+)?", self.cells.get("td-company_btc", ""))
            holdings = finite(holdings_match.group(0).replace(",", "")) if holdings_match else None
            if symbol and holdings is not None:
                self.rows.append({
                    "name": self.cells.get("td-company", ""),
                    "symbol": symbol,
                    "holdings": holdings,
                })
            self.in_row = False
            self.cells = {}


class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if clean:
            self.parts.append(clean)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


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


def collect_kraken_spot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for symbol, config in ASSETS.items():
        pair = config.get("kraken")
        if not pair:
            continue
        url = f"https://api.kraken.com/0/public/Ticker?{urllib.parse.urlencode({'pair': pair})}"
        payload = fetch_json(url)
        if payload.get("error"):
            raise ValueError(f"Kraken {pair}: {payload['error']}")
        rows = payload.get("result") or {}
        row = next(iter(rows.values()), {})
        price = finite((row.get("c") or [None])[0])
        open_24h = finite(row.get("o"))
        timestamp = now_iso()
        result[symbol] = {
            "price_usd": price,
            "change_24h": price / open_24h - 1 if price is not None and open_24h not in (None, 0) else None,
            "as_of": timestamp,
            "as_of_basis": "retrieval_time_no_upstream_timestamp",
        }
        sources.append(source(
            f"kraken_{symbol.lower()}_spot",
            "Kraken Spot",
            url,
            "primary_market",
            timestamp,
            "交易所 USD 現貨報價；Ticker 未提供上游時間戳，明確使用擷取時間",
            "retrieval_time_no_upstream_timestamp",
        ))
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


def compute_sector_baskets(provider_assets: dict[str, dict[str, dict[str, Any]]], provider_errors: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, constituents in SECTOR_BASKETS.items():
        observations: dict[str, dict[str, Any]] = {}
        for provider, assets in provider_assets.items():
            rows = [assets.get(symbol) for symbol in constituents]
            if any(not row or finite(row.get("change_24h")) is None or not row.get("as_of") for row in rows):
                continue
            changes = [finite(row.get("change_24h")) for row in rows]
            market_caps = [finite(row.get("market_cap_usd")) for row in rows]
            volumes = [finite(row.get("volume_24h_usd")) for row in rows]
            observation = {
                "change_24h": statistics.median(value for value in changes if value is not None),
                "as_of": min(str(row["as_of"]) for row in rows),
                "constituent_count": len(rows),
            }
            if all(value is not None for value in market_caps):
                observation["market_cap_usd"] = sum(value for value in market_caps if value is not None)
            if all(value is not None for value in volumes):
                observation["volume_24h_usd"] = sum(value for value in volumes if value is not None)
            observations[provider] = observation
        changes = [item["change_24h"] for item in observations.values()]
        gap = max(changes) - min(changes) if len(changes) >= 2 else None
        market_caps = [item["market_cap_usd"] for item in observations.values() if finite(item.get("market_cap_usd")) is not None]
        volumes = [item["volume_24h_usd"] for item in observations.values() if finite(item.get("volume_24h_usd")) is not None]
        verified = len(changes) >= 2 and gap is not None and gap <= SECTOR_MAX_RETURN_GAP and len(market_caps) >= 2 and len(volumes) >= 2
        result[label] = {
            "basket_version": SECTOR_BASKET_VERSION,
            "constituents": constituents,
            "status": "cross_source_verified" if verified else "unavailable",
            "change_24h": statistics.median(changes) if verified else None,
            "market_cap_usd": statistics.median(market_caps) if verified else None,
            "volume_24h_usd": statistics.median(volumes) if verified else None,
            "source_count": len(observations),
            "required_source_count": 2,
            "cross_source_gap": gap,
            "max_cross_source_gap": SECTOR_MAX_RETURN_GAP,
            "source_observations": observations,
            "source_incidents": provider_errors or [],
            "as_of": min((item["as_of"] for item in observations.values()), default=None),
        }
    return result


def collect_categories() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    symbols = sorted({symbol for constituents in SECTOR_BASKETS.values() for symbol in constituents})
    provider_assets: dict[str, dict[str, dict[str, Any]]] = {}
    provider_errors: list[str] = []
    sources: list[dict[str, Any]] = []

    coingecko_url = "https://api.coingecko.com/api/v3/coins/markets?" + urllib.parse.urlencode({
        "vs_currency": "usd",
        "ids": ",".join(SECTOR_COINGECKO_IDS[symbol] for symbol in symbols),
        "price_change_percentage": "24h",
        "per_page": "250",
        "page": "1",
    })
    try:
        rows = fetch_json(coingecko_url)
        id_to_symbol = {identifier: symbol for symbol, identifier in SECTOR_COINGECKO_IDS.items()}
        assets = {}
        for row in rows:
            symbol = id_to_symbol.get(row.get("id"))
            if not symbol or age_hours(row.get("last_updated")) is None or age_hours(row.get("last_updated")) > SECTOR_SOURCE_MAX_LAG_HOURS:
                continue
            assets[symbol] = {
                "change_24h": (finite(row.get("price_change_percentage_24h")) or 0) / 100 if row.get("price_change_percentage_24h") is not None else None,
                "market_cap_usd": finite(row.get("market_cap")),
                "volume_24h_usd": finite(row.get("total_volume")),
                "as_of": row.get("last_updated"),
            }
        provider_assets["CoinGecko"] = assets
        sources.append(source("sector_basket_coingecko", "CoinGecko Markets", coingecko_url, "independent_market_aggregator", min((item["as_of"] for item in assets.values()), default=None), f"{SECTOR_BASKET_VERSION} 成分幣 24 小時報酬、市值與成交量", "provider_timestamp"))
    except Exception as exc:
        provider_errors.append(readable_source_error("CoinGecko sector basket", exc))

    paprika_url = "https://api.coinpaprika.com/v1/tickers?quotes=USD"
    try:
        rows = fetch_json(paprika_url)
        by_symbol = {str(row.get("symbol") or "").upper(): row for row in sorted(rows, key=lambda item: finite(item.get("rank")) or 999999, reverse=True)}
        assets = {}
        for symbol in symbols:
            row = by_symbol.get(symbol, {})
            quote = row.get("quotes", {}).get("USD", {})
            as_of = row.get("last_updated")
            if age_hours(as_of) is None or age_hours(as_of) > SECTOR_SOURCE_MAX_LAG_HOURS:
                continue
            assets[symbol] = {
                "change_24h": (finite(quote.get("percent_change_24h")) or 0) / 100 if quote.get("percent_change_24h") is not None else None,
                "market_cap_usd": finite(quote.get("market_cap")),
                "volume_24h_usd": finite(quote.get("volume_24h")),
                "as_of": as_of,
            }
        provider_assets["CoinPaprika"] = assets
        sources.append(source("sector_basket_coinpaprika", "CoinPaprika Tickers", paprika_url, "independent_market_aggregator", min((item["as_of"] for item in assets.values()), default=None), f"{SECTOR_BASKET_VERSION} 成分幣 24 小時報酬、市值與成交量", "provider_timestamp"))
    except Exception as exc:
        provider_errors.append(readable_source_error("CoinPaprika sector basket", exc))

    coinlore_urls = [
        "https://api.coinlore.net/api/tickers/?start=0&limit=100",
        "https://api.coinlore.net/api/tickers/?start=100&limit=100",
    ]
    try:
        payloads = [fetch_json(url) for url in coinlore_urls]
        rows = [row for payload in payloads for row in payload.get("data", [])]
        timestamps = [finite(payload.get("info", {}).get("time")) for payload in payloads]
        timestamp = min(value for value in timestamps if value is not None)
        as_of = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        by_symbol = {str(row.get("symbol") or "").upper(): row for row in rows}
        assets = {}
        for symbol in symbols:
            row = by_symbol.get(symbol, {})
            if age_hours(as_of) is None or age_hours(as_of) > SECTOR_SOURCE_MAX_LAG_HOURS:
                continue
            assets[symbol] = {
                "change_24h": (finite(row.get("percent_change_24h")) or 0) / 100 if row.get("percent_change_24h") is not None else None,
                "market_cap_usd": finite(row.get("market_cap_usd")),
                "volume_24h_usd": finite(row.get("volume24")),
                "as_of": as_of,
            }
        provider_assets["CoinLore"] = assets
        sources.append(source("sector_basket_coinlore", "CoinLore Tickers", coinlore_urls[0], "independent_market_aggregator", as_of, f"{SECTOR_BASKET_VERSION} 成分幣 24 小時報酬、市值與成交量；兩頁固定 roster", "provider_timestamp"))
    except Exception as exc:
        provider_errors.append(readable_source_error("CoinLore sector basket", exc))

    binance_symbols = [f"{symbol}USDT" for symbol in symbols]
    binance_url = "https://data-api.binance.vision/api/v3/ticker/24hr?" + urllib.parse.urlencode({"symbols": json.dumps(binance_symbols, separators=(",", ":"))})
    try:
        rows = fetch_json(binance_url)
        assets = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").removesuffix("USDT")
            close_time = finite(row.get("closeTime"))
            as_of = datetime.fromtimestamp(close_time / 1000, timezone.utc).isoformat() if close_time is not None else None
            if symbol not in symbols or age_hours(as_of) is None or age_hours(as_of) > SECTOR_SOURCE_MAX_LAG_HOURS:
                continue
            assets[symbol] = {
                "change_24h": (finite(row.get("priceChangePercent")) or 0) / 100 if row.get("priceChangePercent") is not None else None,
                "market_cap_usd": None,
                "volume_24h_usd": None,
                "as_of": as_of,
            }
        provider_assets["Binance"] = assets
        sources.append(source("sector_basket_binance", "Binance Data API", binance_url, "primary_spot_market", min((item["as_of"] for item in assets.values()), default=None), f"{SECTOR_BASKET_VERSION} 成分幣 USDT 現貨 24 小時報酬交叉驗證；不拿單一交易所成交量當全市場", "provider_timestamp"))
    except Exception as exc:
        provider_errors.append(readable_source_error("Binance sector basket", exc))

    return compute_sector_baskets(provider_assets, provider_errors), sources


def collect_stablecoin_and_rwa_credit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stablecoin_url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
    stablecoin_chart_url = "https://stablecoins.llama.fi/stablecoincharts/all"
    protocols_url = "https://api.llama.fi/protocols"
    stablecoin_payload = fetch_json(stablecoin_url)
    stablecoin_chart = fetch_json(stablecoin_chart_url)
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
    chart_rows = sorted(
        [row for row in stablecoin_chart if finite(row.get("date")) is not None and finite(row.get("totalCirculatingUSD", {}).get("peggedUSD")) is not None],
        key=lambda row: finite(row.get("date")) or 0,
    )
    if not chart_rows:
        raise ValueError("DefiLlama stablecoin timestamped history is empty")
    latest_chart = chart_rows[-1]
    latest_timestamp = int(float(latest_chart["date"]))
    prior_target = latest_timestamp - 30 * 86400
    prior_chart = min(chart_rows, key=lambda row: abs(int(float(row["date"])) - prior_target))
    chart_current = finite(latest_chart.get("totalCirculatingUSD", {}).get("peggedUSD"))
    chart_prior = finite(prior_chart.get("totalCirculatingUSD", {}).get("peggedUSD"))
    stablecoin_as_of = datetime.fromtimestamp(latest_timestamp, timezone.utc).isoformat()

    rwa_protocols = [item for item in protocols if item.get("category") == "RWA" and finite(item.get("tvl")) is not None]
    rwa_protocols.sort(key=lambda item: finite(item.get("tvl")) or 0, reverse=True)
    rwa_tvl = sum(finite(item.get("tvl")) or 0 for item in rwa_protocols)
    timestamped_rwa_tvl = 0.0
    rwa_timestamps: list[str] = []
    for protocol in rwa_protocols[:5]:
        slug = protocol.get("slug")
        if not slug:
            continue
        try:
            detail = fetch_json(f"https://api.llama.fi/protocol/{urllib.parse.quote(str(slug))}")
            history = detail.get("tvl", [])
            latest = history[-1] if history else {}
            timestamp = finite(latest.get("date"))
            if timestamp is None:
                continue
            rwa_timestamps.append(datetime.fromtimestamp(timestamp, timezone.utc).isoformat())
            timestamped_rwa_tvl += finite(protocol.get("tvl")) or 0
        except Exception:
            continue
    if not rwa_timestamps:
        raise ValueError("DefiLlama top RWA protocol timestamps are unavailable")
    rwa_as_of = min(rwa_timestamps)
    btcfi_categories = {"Anchor BTC", "Restaked BTC", "Decentralized BTC"}
    btcfi_protocols = [
        {
            "name": item.get("name"),
            "category": item.get("category"),
            "tvl_usd": finite(item.get("tvl")),
        }
        for item in protocols
        if item.get("category") in btcfi_categories and finite(item.get("tvl")) is not None
    ]
    btcfi_protocols.sort(key=lambda item: item["tvl_usd"] or 0, reverse=True)
    btcfi_tvl = sum(item["tvl_usd"] or 0 for item in btcfi_protocols)
    if not btcfi_protocols or btcfi_tvl <= 0:
        raise ValueError("DefiLlama BTCFi collateral/productive-BTC proxy is unavailable")
    result = {
        "stablecoin_supply_usd": chart_current,
        "stablecoin_supply_30d_ago_usd": chart_prior,
        "stablecoin_supply_asset_sum_usd": current or None,
        "stablecoin_supply_matched_cohort_usd": matched_current or None,
        "stablecoin_supply_matched_cohort_30d_ago_usd": matched_prior_month or None,
        "stablecoin_supply_30d_change": chart_current / chart_prior - 1 if chart_current and chart_prior else None,
        "stablecoin_supply_asset_sum_gap": cross_source_gap([value for value in (current, chart_current) if value is not None]),
        "usd_stablecoin_count": len(usd_assets),
        "stablecoin_30d_matched_count": len(matched_assets),
        "stablecoin_30d_unmatched_count": len(usd_assets) - len(matched_assets),
        "rwa_protocol_tvl_usd": rwa_tvl or None,
        "rwa_protocol_count": len(rwa_protocols),
        "rwa_timestamp_coverage": timestamped_rwa_tvl / rwa_tvl if rwa_tvl else None,
        "rwa_top_protocols": [
            {"name": item.get("name"), "tvl_usd": finite(item.get("tvl"))}
            for item in rwa_protocols[:5]
        ],
        "btcfi_observable_tvl_usd": btcfi_tvl,
        "btcfi_protocol_count": len(btcfi_protocols),
        "btcfi_categories": sorted(btcfi_categories),
        "btcfi_protocols": btcfi_protocols,
        "as_of": min(stablecoin_as_of, rwa_as_of),
        "stablecoin_as_of": stablecoin_as_of,
        "rwa_top_protocols_as_of": rwa_as_of,
        "as_of_basis": "provider_timestamped_stablecoin_history_and_top_rwa_protocol_history",
        "limitations": [
            "Stablecoin supply is DefiLlama's timestamped peggedUSD aggregation, not bank deposits or transaction volume; current asset-sum remains an independent same-provider reconciliation",
            "RWA TVL is the sum of protocols classified as RWA by DefiLlama and may contain provider taxonomy or double-counting risk",
            "RWA freshness is evidenced by timestamped histories for the five largest protocols; the aggregate endpoint itself remains a current snapshot",
            "Stablecoin and RWA scale are reported separately and are never added together",
            "BTCFi proxy includes only DefiLlama Anchor BTC, Restaked BTC and Decentralized BTC protocol TVL; it excludes centralized lenders, bank collateral, derivatives margin and rehypothecation",
        ],
    }
    return result, [
        source("defillama_usd_stablecoins", "DefiLlama Stablecoins", stablecoin_chart_url, "independent_market_aggregator", stablecoin_as_of, "時間戳化 peggedUSD 總供給與 30 日變化；另以逐資產快照重算同源差異", "provider_timestamp"),
        source("defillama_rwa_protocols", "DefiLlama Protocols", protocols_url, "independent_market_aggregator", rwa_as_of, f"供應商分類為 RWA 的協議 TVL 加總；前五大歷史時間戳覆蓋 {result['rwa_timestamp_coverage']:.1%}；另重算三類 BTCFi 可觀測 TVL", "provider_timestamp_top_five_coverage"),
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


def normalize_treasury_symbol(value: Any) -> str:
    symbol = str(value or "").split(".")[0].upper().strip()
    return {"MPJPY": "3350"}.get(symbol, symbol)


def collect_bitcoin_treasuries(asset: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = "https://bitcointreasuries.net/" if asset == "BTC" else "https://bitcointreasuries.net/ethereum"
    page = fetch_text(url, timeout=40)
    parser = TreasuryTableParser()
    parser.feed(page)
    rows = parser.rows
    minimum_rows = 20 if asset == "BTC" else 2
    if len(rows) < minimum_rows:
        raise ValueError(f"BitcoinTreasuries {asset} table schema changed: rows={len(rows)}")
    as_of = now_iso()
    companies = {
        normalize_treasury_symbol(row["symbol"]): {
            **row,
            "symbol": normalize_treasury_symbol(row["symbol"]),
            "as_of": as_of,
            "as_of_basis": "retrieval_time_table_without_global_holdings_timestamp",
        }
        for row in rows
        if normalize_treasury_symbol(row.get("symbol")) and finite(row.get("holdings")) is not None
    }
    detail = (
        f"{len(companies)} 家公開公司 SSR 表；與其他來源只比較公司交集，"
        "不直接比較不同 universe 的全站總量；表格沒有全域持倉日期"
    )
    result = {
        "provider": "BitcoinTreasuries.net",
        "asset": asset,
        "total_holdings": sum(finite(item.get("holdings")) or 0 for item in companies.values()),
        "companies": companies,
        "as_of": as_of,
        "as_of_basis": "retrieval_time_table_without_global_holdings_timestamp",
        "universe_company_count": len(companies),
    }
    return result, [source(
        f"bitcoin_treasuries_{asset.lower()}_dat",
        "BitcoinTreasuries.net public-company table",
        url,
        "independent_treasury_aggregator",
        as_of,
        detail,
        result["as_of_basis"],
    )]


def collect_bitbo_btc_treasuries() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = "https://bitcointreasuries.com/"
    parser = BitboTreasuryTableParser()
    parser.feed(fetch_text(url, timeout=40))
    if len(parser.rows) < 20:
        raise ValueError(f"Bitbo BTC treasury table schema changed: rows={len(parser.rows)}")
    as_of = now_iso()
    companies = {
        normalize_treasury_symbol(row["symbol"]): {
            **row,
            "symbol": normalize_treasury_symbol(row["symbol"]),
            "as_of": as_of,
            "as_of_basis": "retrieval_time_table_without_global_holdings_timestamp",
        }
        for row in parser.rows
        if normalize_treasury_symbol(row.get("symbol")) and finite(row.get("holdings")) is not None
    }
    result = {
        "provider": "Bitbo Bitcoin Treasuries",
        "asset": "BTC",
        "total_holdings": sum(finite(item.get("holdings")) or 0 for item in companies.values()),
        "companies": companies,
        "as_of": as_of,
        "as_of_basis": "retrieval_time_table_without_global_holdings_timestamp",
        "universe_company_count": len(companies),
    }
    return result, [source(
        "bitbo_btc_dat",
        "Bitbo Bitcoin Treasuries public-company table",
        url,
        "independent_treasury_aggregator",
        as_of,
        f"{len(companies)} 家具交易代號的公司列；只在公司交集上比較，不平均不同 universe 的總量",
        result["as_of_basis"],
    )]


def official_dat_observations(asset: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    raw = load_json(RAW_PATH, {"observations": []})
    observations = {item.get("name"): item for item in raw.get("observations", []) if item.get("ok")}
    requested = {
        "BTC": {
            "MSTR": ["mstr_sec_btc_holdings_latest"],
        },
        "ETH": {
            "BMNR": ["bmnr_eth_holdings"],
            "SBET": ["sbet_eth_holdings_equivalent"],
        },
    }[asset]
    companies: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    incidents: list[str] = []
    for symbol, names in requested.items():
        observation = next((observations.get(name) for name in names if observations.get(name)), None)
        if not observation or finite(observation.get("value")) is None:
            incidents.append(f"{symbol} SEC 官方持倉尚未取得")
            continue
        observation_age = age_hours(observation.get("as_of"))
        if observation_age is None or observation_age < -24 or observation_age > 24 * 45:
            incidents.append(f"{symbol} SEC 官方持倉日期未知或超過 45 天，已排除於 DAT quorum")
            continue
        companies[symbol] = {
            "name": symbol,
            "symbol": symbol,
            "holdings": finite(observation.get("value")),
            "as_of": observation.get("as_of"),
            "as_of_basis": observation.get("basis") or "official_filing",
            "detail": observation.get("detail"),
        }
        sources.append(source(
            f"sec_{symbol.lower()}_{asset.lower()}_holdings",
            str(observation.get("source") or f"{symbol} SEC filing"),
            str(observation.get("url") or "https://www.sec.gov/edgar/search/"),
            "official_filing",
            observation.get("as_of"),
            str(observation.get("detail") or "官方公司持倉揭露"),
            str(observation.get("basis") or "official_filing"),
        ))
    return companies, sources, incidents


def dat_cross_source_validation(
    asset: str,
    provider_companies: dict[str, dict[str, dict[str, Any]]],
    base_provider: str,
    base_total: float | None,
    *,
    provider_totals: dict[str, float | None] | None = None,
    assess_resilience: bool = True,
) -> dict[str, Any]:
    ranked_base = sorted(
        (
            (symbol, value)
            for symbol, item in provider_companies.get(base_provider, {}).items()
            if (value := finite(item.get("holdings"))) is not None and value >= 0
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    validation_cohort: set[str] = set()
    cohort_holdings = 0.0
    cohort_target = (base_total or 0) * 0.75
    minimum_cohort_companies = 4 if asset == "BTC" else 2
    for symbol, holdings in ranked_base[:8]:
        validation_cohort.add(symbol)
        cohort_holdings += holdings
        if len(validation_cohort) >= minimum_cohort_companies and cohort_holdings >= cohort_target:
            break
    symbols = sorted(validation_cohort & set(provider_companies.get(base_provider, {})))
    comparisons: list[dict[str, Any]] = []
    weighted_difference = 0.0
    weighted_reference = 0.0
    matched_base_holdings = 0.0
    maximum_company_gap = 0.0
    for symbol in symbols:
        values = {
            provider: finite(companies.get(symbol, {}).get("holdings"))
            for provider, companies in provider_companies.items()
        }
        values = {provider: value for provider, value in values.items() if value is not None and value >= 0}
        if base_provider not in values or len(values) < 2:
            continue
        base_value = values[base_provider]
        consensus_values = {
            provider: value
            for provider, value in values.items()
            if provider == base_provider
            or abs(value - base_value) / max(statistics.median([value, base_value]), 1) <= 0.05
        }
        if len(consensus_values) < 2:
            continue
        outlier_values = {provider: value for provider, value in values.items() if provider not in consensus_values}
        reference = statistics.median(consensus_values.values())
        gap = (max(consensus_values.values()) - min(consensus_values.values())) / reference if reference else 0.0
        maximum_company_gap = max(maximum_company_gap, gap)
        weighted_difference += max(consensus_values.values()) - min(consensus_values.values())
        weighted_reference += reference
        matched_base_holdings += base_value
        comparisons.append({
            "symbol": symbol,
            "provider_values": values,
            "consensus_provider_values": consensus_values,
            "excluded_outlier_provider_values": outlier_values,
            "median_holdings": reference,
            "max_relative_gap": gap,
        })
    weighted_gap = weighted_difference / weighted_reference if weighted_reference else None
    coverage_ratio = matched_base_holdings / base_total if base_total else None
    participating_providers = sorted({provider for comparison in comparisons for provider in comparison["consensus_provider_values"]})
    source_count = len(participating_providers)
    passed = bool(
        source_count >= 2
        and len(comparisons) >= 2
        and coverage_ratio is not None
        and coverage_ratio >= 0.60
        and weighted_gap is not None
        and weighted_gap <= 0.01
        and maximum_company_gap <= 0.05
    )
    result = {
        "status": "representative_cross_source_verified" if passed else "quorum_failed",
        "method": "company_intersection_weighted_gap_with_official_major_holder_overlay",
        "validation_cohort": sorted(validation_cohort),
        "cohort_policy": "dynamic largest base-universe holders, at least four BTC or two ETH candidates, targeting 75% before cross-source matching; verified matched coverage requires at least two companies and 60%",
        "provider_count": source_count,
        "providers": participating_providers,
        "matched_company_count": len(comparisons),
        "matched_base_holdings": matched_base_holdings,
        "representative_coverage_ratio": coverage_ratio,
        "weighted_cross_source_gap": weighted_gap,
        "maximum_company_gap": maximum_company_gap,
        "excluded_outlier_count": sum(len(comparison["excluded_outlier_provider_values"]) for comparison in comparisons),
        "thresholds": {
            "minimum_provider_count": 2,
            "minimum_matched_company_count": 2,
            "minimum_representative_coverage_ratio": 0.60,
            "maximum_weighted_cross_source_gap": 0.01,
            "maximum_individual_company_gap": 0.05,
        },
        "comparisons": comparisons,
        "universe_total_comparison_permitted": False,
    }
    if assess_resilience:
        non_base_results = {
            provider: dat_cross_source_validation(
                asset,
                {name: companies for name, companies in provider_companies.items() if name != provider},
                base_provider,
                base_total,
                provider_totals=provider_totals,
                assess_resilience=False,
            )["status"]
            for provider in participating_providers
            if provider != base_provider
        }
        result["non_base_provider_failure_results"] = non_base_results
        result["non_base_provider_failure_tolerant"] = bool(non_base_results) and all(
            status == "representative_cross_source_verified" for status in non_base_results.values()
        )
        totals = provider_totals or {
            provider: sum(finite(item.get("holdings")) or 0 for item in companies.values())
            for provider, companies in provider_companies.items()
        }
        aggregate_providers = [provider for provider in ("CoinGecko", "BitcoinTreasuries.net") if provider in provider_companies]
        base_failure_results = {
            alternate: dat_cross_source_validation(
                asset,
                {name: companies for name, companies in provider_companies.items() if name != base_provider},
                alternate,
                finite(totals.get(alternate)),
                provider_totals=totals,
                assess_resilience=False,
            )["status"]
            for alternate in aggregate_providers
            if alternate != base_provider
        }
        result["base_provider_failure_results"] = base_failure_results
        result["base_provider_failure_tolerant"] = bool(base_failure_results) and all(
            status == "representative_cross_source_verified" for status in base_failure_results.values()
        )
    return result


def apply_official_dat_overlays(
    base_provider: str,
    base_companies: dict[str, dict[str, Any]],
    official_companies: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], float, list[str]]:
    canonical_companies = {symbol: dict(item) for symbol, item in base_companies.items()}
    overlay_adjustment = 0.0
    incidents: list[str] = []
    for symbol, official in official_companies.items():
        previous = finite(canonical_companies.get(symbol, {}).get("holdings"))
        official_value = finite(official.get("holdings"))
        if official_value is None:
            continue
        if previous is None:
            incidents.append(f"{symbol} 官方持倉無法唯一映射到 {base_provider} universe，未加入總量或前列公司")
            continue
        overlay_adjustment += official_value - previous
        canonical_companies[symbol] = {
            **canonical_companies[symbol],
            **official,
            "name": canonical_companies[symbol].get("name") or official.get("name"),
            "source_basis": "SEC official overlay",
        }
    return canonical_companies, overlay_adjustment, incidents


def enforce_official_overlay_contract(
    validation: dict[str, Any],
    base_companies: dict[str, dict[str, Any]],
    official_companies: dict[str, dict[str, Any]],
    required_symbols: set[str] | None = None,
) -> list[str]:
    unmapped_symbols = sorted(set(official_companies) - set(base_companies))
    missing_symbols = sorted((required_symbols or set()) - set(official_companies))
    validation["official_overlay_complete"] = not unmapped_symbols and not missing_symbols
    validation["official_overlay_unmapped_symbols"] = unmapped_symbols
    validation["official_observation_missing_symbols"] = missing_symbols
    if unmapped_symbols or missing_symbols:
        validation["status"] = "quorum_failed"
        validation["status_reason"] = "required official company evidence is missing or absent from the selected aggregate universe"
    return unmapped_symbols


def select_dat_base_provider(provider_payloads: dict[str, dict[str, Any]]) -> str | None:
    return next((
        provider
        for provider in ("CoinGecko", "BitcoinTreasuries.net")
        if provider in provider_payloads
        and finite(provider_payloads[provider].get("total_holdings")) is not None
        and provider_payloads[provider].get("companies")
    ), None)


def select_dat_validated_base(
    asset: str,
    provider_payloads: dict[str, dict[str, Any]],
    official_companies: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    provider_companies = {
        provider: payload.get("companies", {})
        for provider, payload in provider_payloads.items()
        if payload.get("companies")
    }
    provider_totals = {provider: finite(payload.get("total_holdings")) for provider, payload in provider_payloads.items()}
    required_official_symbols = {"MSTR"} if asset == "BTC" else {"BMNR", "SBET"}
    evaluations: list[tuple[str, dict[str, Any]]] = []
    for provider in ("CoinGecko", "BitcoinTreasuries.net"):
        payload = provider_payloads.get(provider, {})
        base_total = finite(payload.get("total_holdings"))
        if base_total is None or not payload.get("companies"):
            continue
        validation = dat_cross_source_validation(
            asset,
            provider_companies,
            provider,
            base_total,
            provider_totals=provider_totals,
        )
        enforce_official_overlay_contract(validation, payload["companies"], official_companies, required_official_symbols)
        evaluations.append((provider, validation))
    if not evaluations:
        return None, {}
    passing = [item for item in evaluations if item[1].get("status") == "representative_cross_source_verified"]
    selected_provider, selected_validation = min(
        passing or evaluations,
        key=lambda item: (
            0 if item[0] == "CoinGecko" else 1,
            -(finite(item[1].get("representative_coverage_ratio")) or 0),
            finite(item[1].get("weighted_cross_source_gap")) if finite(item[1].get("weighted_cross_source_gap")) is not None else math.inf,
        ),
    )
    selected_validation["base_candidate_results"] = {
        provider: {
            "status": validation.get("status"),
            "representative_coverage_ratio": validation.get("representative_coverage_ratio"),
            "weighted_cross_source_gap": validation.get("weighted_cross_source_gap"),
        }
        for provider, validation in evaluations
    }
    return selected_provider, selected_validation


def collect_dat_treasuries(asset: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coin = "bitcoin" if asset == "BTC" else "ethereum"
    coingecko_url = f"https://api.coingecko.com/api/v3/companies/public_treasury/{coin}"
    provider_payloads: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    incidents: list[str] = []

    try:
        payload = fetch_json(coingecko_url)
        fetched_at = now_iso()
        coingecko_companies = {
            normalize_treasury_symbol(row.get("symbol")): {
                "name": row.get("name"),
                "symbol": normalize_treasury_symbol(row.get("symbol")),
                "holdings": finite(row.get("total_holdings")),
                "current_value_usd": finite(row.get("total_current_value_usd")),
                "supply_share": (finite(row.get("percentage_of_total_supply")) or 0) / 100 if row.get("percentage_of_total_supply") is not None else None,
                "as_of": fetched_at,
                "as_of_basis": "retrieval_time_no_company_holdings_timestamp",
            }
            for row in payload.get("companies", [])
            if normalize_treasury_symbol(row.get("symbol")) and finite(row.get("total_holdings")) is not None
        }
        provider_payloads["CoinGecko"] = {
            "companies": coingecko_companies,
            "total_holdings": finite(payload.get("total_holdings")),
            "total_value_usd": finite(payload.get("total_value_usd")),
            "supply_share": (finite(payload.get("market_cap_dominance")) or 0) / 100 if payload.get("market_cap_dominance") is not None else None,
            "as_of": fetched_at,
            "as_of_basis": "retrieval_time_no_company_holdings_timestamp",
        }
        sources.append(source(
            f"coingecko_{asset.lower()}_dat",
            "CoinGecko Public Companies Treasury",
            coingecko_url,
            "third_party_treasury_aggregator",
            fetched_at,
            "聚合 universe 與長尾 roster；前列公司另以獨立聚合站及 SEC 官方揭露交叉驗證",
            "retrieval_time_no_company_holdings_timestamp",
        ))
    except Exception as exc:
        incidents.append(readable_source_error(f"CoinGecko {asset} DAT", exc))

    try:
        bitcoin_treasuries, provider_sources = collect_bitcoin_treasuries(asset)
        provider_payloads["BitcoinTreasuries.net"] = bitcoin_treasuries
        sources.extend(provider_sources)
    except Exception as exc:
        incidents.append(readable_source_error(f"BitcoinTreasuries.net {asset} DAT", exc))

    if asset == "BTC":
        try:
            bitbo_treasuries, provider_sources = collect_bitbo_btc_treasuries()
            provider_payloads["Bitbo Bitcoin Treasuries"] = bitbo_treasuries
            sources.extend(provider_sources)
        except Exception as exc:
            incidents.append(readable_source_error("Bitbo BTC DAT", exc))

    official_companies, official_sources, official_incidents = official_dat_observations(asset)
    if official_companies:
        provider_payloads["SEC official filings"] = {
            "companies": official_companies,
            "total_holdings": sum(finite(item.get("holdings")) or 0 for item in official_companies.values()),
            "as_of": max((item.get("as_of") for item in official_companies.values() if item.get("as_of")), default=None),
            "as_of_basis": "company_specific_official_holdings_dates",
        }
    sources.extend(official_sources)
    incidents.extend(official_incidents)

    base_provider, validation = select_dat_validated_base(asset, provider_payloads, official_companies)
    if not base_provider:
        raise ValueError(f"{asset} DAT has no aggregate provider")
    base = provider_payloads[base_provider]
    base_companies = base.get("companies", {})
    base_total = finite(base.get("total_holdings"))
    canonical_companies, overlay_adjustment, overlay_incidents = apply_official_dat_overlays(
        base_provider,
        base_companies,
        official_companies,
    )
    incidents.extend(overlay_incidents)
    verified_total = base_total + overlay_adjustment if base_total is not None else None
    total_value_usd = finite(base.get("total_value_usd"))
    if total_value_usd is not None and base_total and verified_total is not None:
        total_value_usd *= verified_total / base_total
    supply_share = finite(base.get("supply_share"))
    if supply_share is not None and base_total and verified_total is not None:
        supply_share *= verified_total / base_total
    companies = sorted(canonical_companies.values(), key=lambda row: finite(row.get("holdings")) or 0, reverse=True)[:8]
    as_of = max((payload.get("as_of") for payload in provider_payloads.values() if payload.get("as_of")), default=now_iso())
    coverage = finite(validation.get("representative_coverage_ratio"))
    weighted_gap = finite(validation.get("weighted_cross_source_gap"))
    result = {
        "asset": asset,
        "status": validation["status"],
        "total_holdings": verified_total,
        "total_holdings_base": base_total,
        "total_holdings_base_provider": base_provider,
        "official_overlay_adjustment": overlay_adjustment,
        "total_value_usd": total_value_usd,
        "supply_share": supply_share,
        "companies": companies,
        "as_of": as_of,
        "as_of_basis": "latest_provider_retrieval_with_company_specific_official_overlay_dates",
        "source_count": validation["provider_count"],
        "source_observations": {
            provider: {
                "total_holdings": finite(payload.get("total_holdings")),
                "company_count": len(payload.get("companies", {})),
                "as_of": payload.get("as_of"),
                "as_of_basis": payload.get("as_of_basis"),
            }
            for provider, payload in provider_payloads.items()
        },
        "validation": validation,
        "source_incidents": incidents,
        "limitation": (
            f"{base_provider} 定義聚合 universe，SEC 官方值覆蓋前列公司；交集覆蓋 {coverage:.1%}、"
            f"加權差異 {weighted_gap:.2%}。不同聚合 universe 的全站總量不硬平均。"
            if coverage is not None and weighted_gap is not None
            else "來源 quorum 或可比公司覆蓋不足；不得把不同聚合 universe 的全站總量硬平均。"
        ),
    }
    return result, sources


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
            "plain_read": f"賽道領先為 {sector_rank[0][0] if sector_rank else '未知'}；固定代表籃子採至少雙來源同成分重算，只作輪動背景。",
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
    btc_dat = output.get("dat", {}).get("BTC", {})
    btc_dat_validation = btc_dat.get("validation", {})
    public_company_holdings = finite(btc_dat.get("total_holdings"))
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
        "btcfi_observable_tvl": finite(credit.get("btcfi_observable_tvl_usd")),
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
            "stablecoin_supply_30d_ago_usd": finite(credit.get("stablecoin_supply_30d_ago_usd")),
            "stablecoin_supply_asset_sum_usd": finite(credit.get("stablecoin_supply_asset_sum_usd")),
            "stablecoin_supply_asset_sum_gap": finite(credit.get("stablecoin_supply_asset_sum_gap")),
            "stablecoin_supply_matched_cohort_usd": finite(credit.get("stablecoin_supply_matched_cohort_usd")),
            "stablecoin_supply_matched_cohort_30d_ago_usd": finite(credit.get("stablecoin_supply_matched_cohort_30d_ago_usd")),
            "stablecoin_supply_30d_change": finite(credit.get("stablecoin_supply_30d_change")),
            "usd_stablecoin_count": credit.get("usd_stablecoin_count"),
            "stablecoin_30d_matched_count": credit.get("stablecoin_30d_matched_count"),
            "stablecoin_30d_unmatched_count": credit.get("stablecoin_30d_unmatched_count"),
            "rwa_protocol_tvl_usd": finite(credit.get("rwa_protocol_tvl_usd")),
            "rwa_protocol_count": credit.get("rwa_protocol_count"),
            "btcfi_observable_tvl_usd": finite(credit.get("btcfi_observable_tvl_usd")),
            "btcfi_protocol_count": credit.get("btcfi_protocol_count"),
            "btcfi_categories": credit.get("btcfi_categories", []),
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
            "as_of": btc_dat.get("as_of"),
            "as_of_basis": btc_dat.get("as_of_basis"),
            "source_count": btc_dat.get("source_count"),
            "representative_coverage_ratio": btc_dat_validation.get("representative_coverage_ratio"),
            "weighted_cross_source_gap": btc_dat_validation.get("weighted_cross_source_gap"),
            "plain_read": (
                f"公開公司聚合樣本持有約 {public_company_supply_share:.1%} BTC 供給；"
                f"{btc_dat_validation.get('provider_count', 0)} 個來源以代表性公司交叉驗證，覆蓋聚合值 "
                f"{btc_dat_validation.get('representative_coverage_ratio', 0):.1%}、加權差異 "
                f"{btc_dat_validation.get('weighted_cross_source_gap', 0):.2%}；最大公司仍占樣本 "
                f"{top_company_concentration:.1%}。"
                if public_company_supply_share is not None
                and top_company_concentration is not None
                and btc_dat_validation.get("status") == "representative_cross_source_verified"
                else "公開公司財庫資料不足或交叉驗證未通過。"
            ),
            "limitation": "The selected public-company universe excludes private companies, ETFs, governments, custodians and collateral reuse; totals from different universes are never averaged",
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
            "status": "measured_onchain_proxy_global_total_unknown",
            "global_total_status": "unknown_no_complete_public_dataset",
            "observable_btcfi_tvl_usd": finite(credit.get("btcfi_observable_tvl_usd")),
            "observable_protocol_count": credit.get("btcfi_protocol_count"),
            "included_categories": credit.get("btcfi_categories", []),
            "plain_read": (
                f"可觀測鏈上 BTCFi 三類協議 TVL 約 ${credit['btcfi_observable_tvl_usd'] / 1e9:.1f}B、"
                f"涵蓋 {credit.get('btcfi_protocol_count', 0)} 個協議；這是抵押／生息採用代理，"
                "仍不含中心化借貸、銀行抵押、衍生品保證金與再質押重複計算。"
                if finite(credit.get("btcfi_observable_tvl_usd")) is not None
                else "BTCFi 可觀測代理缺漏；不得用 ETF、DAT 或衍生品未平倉量冒充全球抵押採用。"
            ),
        },
    }


def quality_checks(output: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    source_incidents = list(errors)
    checks: list[dict[str, Any]] = []
    generated_at = output.get("generated_at")

    def add_check(check_id: str, label: str, status: str, detail: str, *, core: bool, observed: int | None = None, required: int | None = None) -> None:
        checks.append({
            "check_id": check_id,
            "label": label,
            "status": status,
            "core": core,
            "observed_source_count": observed,
            "required_source_count": required,
            "detail": detail,
        })

    source_batch_id = output.get("source_batch_id")
    snapshot_generated_at = output.get("snapshot_generated_at")
    raw_generated_at = output.get("raw_generated_at")
    raw_batch_id = output.get("raw_batch_id")
    lineage_ok = bool(
        source_batch_id
        and source_batch_id == raw_batch_id
        and snapshot_generated_at
        and raw_generated_at
        and snapshot_generated_at == raw_generated_at
    )
    if not lineage_ok:
        failures.append("每日快照、原始觀測與市場層批次血緣不一致")
    add_check(
        "daily_snapshot_lineage",
        "每日快照批次血緣",
        "pass" if lineage_ok else "fail",
        "snapshot、raw 與 market universe 共用同一批次" if lineage_ok else "批次 ID 或生成時間缺漏／不一致",
        core=True,
        observed=1 if lineage_ok else 0,
        required=1,
    )

    for symbol, asset in output.get("assets", {}).items():
        check_failures: list[str] = []
        price = finite(asset.get("price_usd"))
        gap = finite(asset.get("cross_source_gap"))
        if price is None or price <= 0:
            check_failures.append(f"{symbol} spot price missing")
        if int(asset.get("source_count") or 0) < 2 or gap is None:
            check_failures.append(f"{symbol} 缺少兩個獨立現貨來源")
        elif gap > 0.02:
            check_failures.append(f"{symbol} cross-source spot gap {gap:.2%} > 2%")
        for provider, observation in asset.get("source_observations", {}).items():
            provider_lag = lag_hours_at(generated_at, observation.get("as_of"))
            if provider_lag is None:
                check_failures.append(f"{symbol} {provider} 現貨來源時間未知")
            elif provider_lag < -0.25 or provider_lag > FRESHNESS_CONTRACT["spot_source_max_lag_hours"]:
                check_failures.append(f"{symbol} {provider} 現貨來源相對批次時間不可信或逾時 {provider_lag:.1f} 小時")
            if provider in {"Binance", "OKX"}:
                price_usdt = finite(observation.get("price_usdt"))
                usdt_usd = finite(observation.get("usdt_usd"))
                normalized_price = finite(observation.get("price_usd"))
                usdt_lag = lag_hours_at(generated_at, observation.get("usdt_usd_as_of"))
                if price_usdt is None or usdt_usd is None or normalized_price is None:
                    check_failures.append(f"{symbol} {provider} USDT/USD 正規化輸入缺失")
                elif abs(price_usdt * usdt_usd - normalized_price) > max(1e-8, normalized_price * 1e-9):
                    check_failures.append(f"{symbol} {provider} USDT/USD 正規化重算不一致")
                if usdt_lag is None or usdt_lag < -0.25 or usdt_lag > FRESHNESS_CONTRACT["spot_source_max_lag_hours"]:
                    check_failures.append(f"{symbol} {provider} USDT/USD 匯率逾時或時間未知")
        failures.extend(check_failures)
        source_count = int(asset.get("source_count") or 0)
        add_check(
            f"spot_{symbol.lower()}",
            f"{symbol} 現貨價格",
            "fail" if check_failures else "pass",
            "；".join(check_failures) if check_failures else f"{source_count} 個來源中位數；最大跨源價差 {gap:.2%}",
            core=True,
            observed=source_count,
            required=2,
        )
    for symbol in ("BTC", "ETH"):
        derivative = output.get("derivatives", {}).get(symbol, {})
        perpetual = derivative.get("perpetual", {})
        perpetual_failures: list[str] = []
        funding_count = int(perpetual.get("funding_source_count") or 0)
        if funding_count < 2:
            perpetual_failures.append(f"{symbol} 缺少兩個場域的可比資金費率")
        if perpetual.get("funding_annualized_median") is None:
            perpetual_failures.append(f"{symbol} cross-venue perpetual funding missing")
        for venue_error in perpetual.get("venue_errors", []):
            source_incidents.append(f"{symbol} 永續來源事件：{venue_error}")
        for venue in perpetual.get("venues_used", []):
            venue_lag = lag_hours_at(generated_at, perpetual.get(venue, {}).get("as_of"))
            if venue_lag is None or venue_lag < -0.25 or venue_lag > FRESHNESS_CONTRACT["perpetual_source_max_lag_hours"]:
                perpetual_failures.append(f"{symbol} {venue} 永續來源逾時或時間未知")
        failures.extend(perpetual_failures)
        add_check(
            f"perpetual_{symbol.lower()}",
            f"{symbol} 永續合約資金費率",
            "fail" if perpetual_failures else "pass",
            "；".join(perpetual_failures) if perpetual_failures else f"{funding_count} 個場域交叉驗證",
            core=True,
            observed=funding_count,
            required=2,
        )

        dated_future = derivative.get("dated_future", {})
        for fallback_error in dated_future.get("fallback_errors", []):
            source_incidents.append(f"{symbol} 到期期貨來源事件：{fallback_error}")
        dated_failures: list[str] = []
        if dated_future.get("annualized_basis") is None:
            dated_failures.append(f"{symbol} dated-futures basis missing")
        dated_future_lag = lag_hours_at(generated_at, dated_future.get("as_of"))
        if dated_future_lag is None or dated_future_lag < -0.25 or dated_future_lag > FRESHNESS_CONTRACT["dated_future_source_max_lag_hours"]:
            dated_failures.append(f"{symbol} dated-futures source stale or timestamp missing")
        failures.extend(dated_failures)
        add_check(
            f"dated_future_{symbol.lower()}",
            f"{symbol} 到期期貨基差",
            "fail" if dated_failures else "pass",
            "；".join(dated_failures) if dated_failures else f"{dated_future.get('provider')} 主來源或備援契約通過",
            core=True,
            observed=1 if not dated_failures else 0,
            required=1,
        )

        options = derivative.get("options", {})
        for fallback_error in options.get("fallback_errors", []):
            source_incidents.append(f"{symbol} 期權來源事件：{fallback_error}")
        options_failures: list[str] = []
        if options.get("volatility_value") is None:
            options_failures.append(f"{symbol} options volatility proxy missing")
        if options.get("put_call_open_interest_ratio") is None:
            options_failures.append(f"{symbol} options put/call OI missing")
        options_lag = lag_hours_at(generated_at, options.get("as_of"))
        volatility_lag = lag_hours_at(generated_at, options.get("volatility_as_of"))
        if options_lag is None or options_lag < -0.25 or options_lag > FRESHNESS_CONTRACT["options_source_max_lag_hours"]:
            options_failures.append(f"{symbol} options source stale or timestamp missing")
        if volatility_lag is None or volatility_lag < -0.25 or volatility_lag > FRESHNESS_CONTRACT["volatility_source_max_lag_hours"]:
            options_failures.append(f"{symbol} options volatility source stale or timestamp missing")
        if options.get("open_interest_observed_contracts") != options.get("contracts_observed"):
            options_failures.append(f"{symbol} options OI coverage incomplete")
        if options.get("volume_observed_contracts") != options.get("contracts_observed"):
            options_failures.append(f"{symbol} options volume coverage incomplete")
        failures.extend(options_failures)
        add_check(
            f"options_{symbol.lower()}",
            f"{symbol} 期權波動與籌碼",
            "fail" if options_failures else "pass",
            "；".join(options_failures) if options_failures else f"{options.get('provider')} 契約集合與計算完整",
            core=True,
            observed=1 if not options_failures else 0,
            required=1,
        )

    incomplete_sectors = [sector for sector, item in output.get("sectors", {}).items() if item.get("status") != "cross_source_verified" or finite(item.get("market_cap_usd")) is None or finite(item.get("change_24h")) is None]
    sector_status = "degraded" if incomplete_sectors else "pass"
    sector_detail = f"缺漏或分歧：{', '.join(incomplete_sectors)}" if incomplete_sectors else f"{SECTOR_BASKET_VERSION} 至少雙來源同籃子重算通過"
    if incomplete_sectors:
        degradations.append(f"賽道固定籃子資料不完整或跨源分歧：{', '.join(incomplete_sectors)}")
    sector_source_count = min((item.get("source_count", 0) for item in output.get("sectors", {}).values()), default=0)
    add_check("sector_rotation", "熱門賽道輪動", sector_status, sector_detail, core=False, observed=sector_source_count, required=2)

    for asset, item in output.get("dat", {}).items():
        for incident in item.get("source_incidents", []):
            source_incidents.append(f"{asset} DAT 來源事件：{incident}")
        validation = item.get("validation", {})
        dat_verified = (
            finite(item.get("total_holdings")) is not None
            and item.get("status") == "representative_cross_source_verified"
            and validation.get("provider_count", 0) >= 2
            and validation.get("matched_company_count", 0) >= 2
            and validation.get("official_overlay_complete") is True
            and finite(validation.get("representative_coverage_ratio")) is not None
            and validation["representative_coverage_ratio"] >= 0.60
            and finite(validation.get("weighted_cross_source_gap")) is not None
            and validation["weighted_cross_source_gap"] <= 0.01
            and finite(validation.get("maximum_company_gap")) is not None
            and validation["maximum_company_gap"] <= 0.05
        )
        if finite(item.get("total_holdings")) is None:
            degradations.append(f"{asset} DAT aggregate missing")
            add_check(f"dat_{asset.lower()}", f"{asset} 財庫公司聚合", "degraded", "聚合值缺失，前端必須顯示未知", core=False, observed=0, required=1)
        elif dat_verified:
            add_check(
                f"dat_{asset.lower()}",
                f"{asset} 財庫公司聚合",
                "pass",
                f"{validation['provider_count']} 個來源；可比公司覆蓋 {validation['representative_coverage_ratio']:.1%}；加權差異 {validation['weighted_cross_source_gap']:.2%}；排除並明示 {validation.get('excluded_outlier_count', 0)} 筆 outlier",
                core=False,
                observed=validation["provider_count"],
                required=2,
            )
        else:
            degradations.append(f"{asset} DAT 來源 quorum、代表性覆蓋或差異門檻未通過")
            add_check(
                f"dat_{asset.lower()}",
                f"{asset} 財庫公司聚合",
                "degraded",
                "至少需要 2 個來源、2 家可比公司、60% 代表性覆蓋、加權差異不高於 1%、單公司差異不高於 5%，且官方公司必須可映射",
                core=False,
                observed=validation.get("provider_count", 0),
                required=2,
            )

    for asset in ("BTC", "ETH"):
        item = output.get("etf", {}).get(asset, {})
        lag_days = calendar_day_lag(generated_at, item.get("as_of"))
        source_count = int(item.get("source_count") or 0)
        component_completeness = finite(item.get("component_completeness"))
        official_gap = finite(item.get("official_major_fund_gap"))
        official_coverage = finite(item.get("official_major_fund_coverage"))
        backup_gap = finite(item.get("backup_component_gap"))
        backup_coverage = finite(item.get("backup_component_coverage"))
        required_values = [finite(item.get(key)) for key in ("flow_1d_usd", "flow_7d_usd", "flow_30d_usd")]
        verified = bool(
            item.get("status") == "sample_cross_source_verified"
            and lag_days is not None
            and 0 <= lag_days <= FRESHNESS_CONTRACT["etf_source_max_lag_days"]
            and source_count >= 3
            and component_completeness is not None
            and component_completeness >= 0.95
            and official_gap is not None
            and official_gap <= 0.05
            and official_coverage is not None
            and official_coverage >= 0.30
            and backup_gap is not None
            and backup_gap <= 0.05
            and backup_coverage is not None
            and backup_coverage >= 0.30
            and all(value is not None for value in required_values)
        )
        if not verified:
            degradations.append(f"{asset} ETF 流向來源 quorum、官方主要基金差異或交易日新鮮度未通過")
        add_check(
            f"etf_{asset.lower()}",
            f"{asset} 現貨 ETF 流向",
            "pass" if verified else "degraded",
            f"{source_count} 個驗證來源；基金 roster {component_completeness:.1%}；官方樣本差異 {official_gap:.2%}、覆蓋 {official_coverage:.1%}；同日備援基金／總量差異 {backup_gap:.2%}、覆蓋 {backup_coverage:.1%}；市場日落後 {lag_days} 天" if verified else "至少 3 個驗證來源、基金 roster 95%、官方與同日備援基金／總量差異不高於 5% 或 500 萬美元、各覆蓋 gross flow 30%、市場日不超過 5 天",
            core=False,
            observed=source_count,
            required=3,
        )

    thesis_quality = output.get("btc_thesis", {}).get("quality", {})
    thesis_status = thesis_quality.get("status") if thesis_quality.get("status") in {"pass", "degraded", "fail"} else "fail"
    if thesis_status == "fail":
        failures.append("BTC 長期結構證據層未通過資料契約")
    elif thesis_status == "degraded":
        degradations.append("BTC 長期結構證據層有限可用；不參與交易放行")
    add_check("btc_structural_thesis", "BTC 長期結構證據", thesis_status, "僅作結構背景，永不控制交易閘門", core=False)

    failures = list(dict.fromkeys(failures))
    degradations = list(dict.fromkeys(degradations))
    source_incidents = list(dict.fromkeys(source_incidents))
    core_checks = [item for item in checks if item["core"]]
    summary = {
        "total": len(checks),
        "passed": sum(item["status"] == "pass" for item in checks),
        "degraded": sum(item["status"] == "degraded" for item in checks),
        "failed": sum(item["status"] == "fail" for item in checks),
        "core_total": len(core_checks),
        "core_passed": sum(item["status"] == "pass" for item in core_checks),
        "core_failed": sum(item["status"] == "fail" for item in core_checks),
        "source_incident_count": len(source_incidents),
        "fallback_quorum_preserved": not any(item["status"] == "fail" for item in core_checks),
    }
    score = max(0, 100 - summary["failed"] * 15 - summary["degraded"] * 3)
    return {
        "status": "fail" if failures else "degraded" if degradations else "pass",
        "score_0_100": score,
        "failures": failures,
        "degradations": degradations,
        "source_incidents": source_incidents,
        "checks": checks,
        "validation_summary": summary,
        "freshness_contract": FRESHNESS_CONTRACT,
        "policy": "資料欄位是契約、供應商可替換；來源失敗但備援後仍滿足來源數、新鮮度與價差門檻時，不降低該欄位品質。缺失或分歧資料維持未知。",
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
        "etf": {asset: {
            "status": item.get("status"),
            "flow_1d_usd": item.get("flow_1d_usd"),
            "flow_7d_usd": item.get("flow_7d_usd"),
            "flow_30d_usd": item.get("flow_30d_usd"),
            "as_of": item.get("as_of"),
            "source_count": item.get("source_count"),
            "component_completeness": item.get("component_completeness"),
            "official_major_fund_gap": item.get("official_major_fund_gap"),
            "official_major_fund_coverage": item.get("official_major_fund_coverage"),
            "backup_component_gap": item.get("backup_component_gap"),
            "backup_component_coverage": item.get("backup_component_coverage"),
        } for asset, item in output["etf"].items()},
        "dat": {asset: {
            "total_holdings": item.get("total_holdings"),
            "status": item.get("status"),
            "source_count": item.get("source_count"),
            "representative_coverage_ratio": item.get("validation", {}).get("representative_coverage_ratio"),
            "weighted_cross_source_gap": item.get("validation", {}).get("weighted_cross_source_gap"),
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
        "kraken_spot": collect_kraken_spot,
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
                    "kraken_spot": "Kraken 現貨",
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
    kraken = results.get("kraken_spot", {})
    hyperliquid = results.get("hyperliquid", {})
    assets: dict[str, Any] = {}
    for symbol in ASSETS:
        provider_prices = {
            "CoinGecko": finite(coingecko.get(symbol, {}).get("price_usd")),
            "Binance": finite(binance.get(symbol, {}).get("price_usd")),
            "OKX": finite(okx.get(symbol, {}).get("price_usd")),
            "Coinbase": finite(coinbase.get(symbol, {}).get("price_usd")),
            "Kraken": finite(kraken.get(symbol, {}).get("price_usd")),
        }
        provider_changes = {
            "CoinGecko": finite(coingecko.get(symbol, {}).get("change_24h")),
            "Binance": finite(binance.get(symbol, {}).get("change_24h")),
            "OKX": finite(okx.get(symbol, {}).get("change_24h")),
            "Kraken": finite(kraken.get(symbol, {}).get("change_24h")),
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
            "Kraken": {"price_usd": finite(kraken.get(symbol, {}).get("price_usd")), "as_of": kraken.get(symbol, {}).get("as_of"), "quote_asset": "USD", "as_of_basis": kraken.get(symbol, {}).get("as_of_basis")},
        }
        clean_prices = [value for value in provider_prices.values() if value is not None]
        clean_changes = [value for value in provider_changes.values() if value is not None]
        base = dict(coingecko.get(symbol, {}))
        valid_observations = {name: item for name, item in source_observations.items() if item["price_usd"] is not None}
        observation_times = [item.get("as_of") for item in valid_observations.values() if item.get("as_of")]
        base.update({
            "price_usd": statistics.median(clean_prices) if len(clean_prices) >= 2 else None,
            "change_24h": statistics.median(clean_changes) if clean_changes else None,
            "change_24h_sources": {name: value for name, value in provider_changes.items() if value is not None},
            "unverified_reference_price_usd": statistics.median(clean_prices) if clean_prices else None,
            "source_prices": {name: value for name, value in provider_prices.items() if value is not None},
            "source_observations": valid_observations,
            "cross_source_gap": cross_source_gap(clean_prices),
            "source_count": len(clean_prices),
            "as_of": max(observation_times) if observation_times else None,
            "price_validation": {
                "method": "median_of_available_independent_sources",
                "required_source_count": 2,
                "observed_source_count": len(clean_prices),
                "max_cross_source_gap": 0.02,
                "providers_used": list(valid_observations),
            },
            "state_24h": state_from_change(base.get("change_24h")),
        })
        assets[symbol] = base

    snapshot = load_json(SNAPSHOT_PATH, {})
    radar = snapshot.get("metrics", {}).get("market_radar", {})
    def etf_validation_inputs(prefix: str) -> dict[str, Any]:
        try:
            parsed = json.loads(str(radar.get(f"{prefix}_validation_inputs_json") or ""))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    btc_etf_inputs = etf_validation_inputs("etf_flow")
    eth_etf_inputs = etf_validation_inputs("eth_etf_flow")
    btc_etf_status = radar.get("etf_flow_status", "unavailable")
    btc_etf_as_of = radar.get("etf_flow_as_of")
    btc_etf_day_lag = calendar_day_lag(now_iso(), btc_etf_as_of)
    btc_etf_fresh = btc_etf_day_lag is not None and 0 <= btc_etf_day_lag <= FRESHNESS_CONTRACT["etf_source_max_lag_days"]
    eth_etf_status = radar.get("eth_etf_flow_status", "unavailable")
    eth_etf_as_of = radar.get("eth_etf_flow_as_of")
    eth_etf_day_lag = calendar_day_lag(now_iso(), eth_etf_as_of)
    eth_etf_fresh = eth_etf_day_lag is not None and 0 <= eth_etf_day_lag <= FRESHNESS_CONTRACT["etf_source_max_lag_days"]
    raw_daily = load_json(RAW_PATH, {"observations": []})
    etf_provider_observations: dict[str, dict[str, Any]] = {"BTC": {}, "ETH": {}}
    for item in raw_daily.get("observations", []):
        match = re.fullmatch(r"(btc|eth)_etf_provider_(.+)_1d_usd", str(item.get("name") or ""))
        official_match = re.fullmatch(r"(btc|eth)_etf_official_major_fund_gap", str(item.get("name") or ""))
        if (not match and not official_match) or not item.get("ok"):
            continue
        asset = (match or official_match).group(1).upper()
        provider = str(item.get("source") or (match.group(2) if match else "official issuer"))
        etf_provider_observations[asset][provider] = {
            "flow_1d_usd": finite(item.get("value")) if match else None,
            "official_major_fund_gap": finite(item.get("value")) if official_match else None,
            "as_of": item.get("as_of"),
            "basis": item.get("basis"),
            "url": item.get("url"),
        }
        sources.append(source(
            f"{asset.lower()}_etf_{re.sub(r'[^a-z0-9]+', '_', provider.lower()).strip('_')}",
            provider,
            str(item.get("url") or ""),
            str(item.get("source_tier") or "independent_ETF_data_provider"),
            item.get("as_of"),
            str(item.get("detail") or "ETF provider observation"),
            str(item.get("basis") or "provider_reported"),
        ))
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
        "schema": 2,
        "date": today_utc(),
        "generated_at": now_iso(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "raw_generated_at": raw_daily.get("generated_at"),
        "source_batch_id": snapshot.get("batch_id"),
        "raw_batch_id": raw_daily.get("batch_id"),
        "update_target": "hourly",
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
                "source": f"{btc_etf_inputs.get('canonical_provider') or 'fund-component provider'} with iShares official and same-date backup validation",
                "source_count": int(finite(radar.get("etf_flow_source_count")) or 0),
                "component_completeness": finite(radar.get("etf_flow_component_completeness")),
                "official_major_fund_gap": finite(radar.get("etf_flow_official_major_fund_gap")),
                "official_major_fund_coverage": finite(radar.get("etf_flow_official_major_fund_coverage")),
                "backup_component_gap": finite(radar.get("etf_flow_backup_component_gap")),
                "backup_component_coverage": finite(radar.get("etf_flow_backup_component_coverage")),
                "validation_inputs_json": radar.get("etf_flow_validation_inputs_json"),
                "latest_published_as_of": btc_etf_inputs.get("latest_published_as_of"),
                "selection_policy": btc_etf_inputs.get("selection_policy"),
                "backup_validation_type": btc_etf_inputs.get("backup_sample", {}).get("validation_type"),
                "validation_scope": radar.get("etf_flow_validation_scope"),
                "source_observations": etf_provider_observations["BTC"],
                "hard_trigger": False,
                "limitation": "多源與官方主要基金持倉變化已交叉驗證；ETF 資金流仍不得單獨放行交易",
            },
            "ETH": {
                "status": eth_etf_status,
                "flow_1d_usd": finite(radar.get("eth_etf_flow_1d_usd")) if eth_etf_fresh else None,
                "flow_7d_usd": finite(radar.get("eth_etf_flow_7d_usd")) if eth_etf_fresh else None,
                "flow_30d_usd": finite(radar.get("eth_etf_flow_30d_usd")) if eth_etf_fresh else None,
                "as_of": eth_etf_as_of,
                "update_frequency": "daily",
                "source": f"{eth_etf_inputs.get('canonical_provider') or 'fund-component provider'} with iShares official and same-date backup validation",
                "source_count": int(finite(radar.get("eth_etf_flow_source_count")) or 0),
                "component_completeness": finite(radar.get("eth_etf_flow_component_completeness")),
                "official_major_fund_gap": finite(radar.get("eth_etf_flow_official_major_fund_gap")),
                "official_major_fund_coverage": finite(radar.get("eth_etf_flow_official_major_fund_coverage")),
                "backup_component_gap": finite(radar.get("eth_etf_flow_backup_component_gap")),
                "backup_component_coverage": finite(radar.get("eth_etf_flow_backup_component_coverage")),
                "validation_inputs_json": radar.get("eth_etf_flow_validation_inputs_json"),
                "latest_published_as_of": eth_etf_inputs.get("latest_published_as_of"),
                "selection_policy": eth_etf_inputs.get("selection_policy"),
                "backup_validation_type": eth_etf_inputs.get("backup_sample", {}).get("validation_type"),
                "validation_scope": radar.get("eth_etf_flow_validation_scope"),
                "source_observations": etf_provider_observations["ETH"],
                "hard_trigger": False,
                "limitation": "多源與官方主要基金持倉變化已交叉驗證；ETF 資金流仍不得單獨放行交易",
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
    history.update({"schema": 2, "updated_at": now_iso(), "items": items[-730:]})
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
