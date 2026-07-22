#!/usr/bin/env python3
"""Daily market data collector for the MSTR/BTC dashboard.

No paid API keys are required. The collector writes raw observations and a compact
snapshot that the verifier and static pages can consume.
"""

from __future__ import annotations

import copy
import csv
import html
import json
import math
import os
import re
import statistics
import sys
import time
import gzip
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
RAW_PATH = DATA_DIR / "raw_observations.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
DATABASE_PATH = DATA_DIR / "database.json"
PROVENANCE_PATH = ROOT / "data" / "inputs" / "mstr_capital_structure_provenance.json"

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "mstr-btc-bottom-report/1.0 hsin73@realtek.com",
)

ETF_COMPONENT_PROVIDERS = {"The Block", "Blockworks / Trackinsights", "Bitbo"}
ETF_PROVIDER_PRIORITY = {"The Block": 0, "Blockworks / Trackinsights": 1, "Bitbo": 2}
ETF_EXPECTED_US_SPOT_TICKERS = {
    "BTC": {"ARKB", "BITB", "BRRR", "BTC", "BTCO", "BTCW", "DEFI", "EZBC", "FBTC", "GBTC", "HODL", "IBIT", "MSBT"},
    "ETH": {"ETH", "ETHA", "ETHE", "ETHV", "ETHW", "EZET", "FETH", "QETH"},
}
ETF_MAX_ABS_DAILY_FUND_FLOW_USD = 50_000_000_000
ETF_MAX_GROSS_DAILY_FLOW_USD = 100_000_000_000
ETF_COMPONENT_SUM_ABSOLUTE_TOLERANCE_USD = 500_000
ETF_COMPONENT_SUM_RELATIVE_TOLERANCE = 0.001

MANUAL_INPUTS = {
    "mstr_btc_holdings": 843_775,
    "usd_reserve_musd": 2_550,
    "cash_other_musd": 0,
    "deferred_tax_liability_musd": 0,  # fallback only; SEC companyfacts overrides when available
    "debt_face_musd": 8_214,
    "annual_interest_musd": 34,
    "other_debt_annual_service_musd": 3.6,
    "preferred": {
        "STRF": {"notional_musd": 3_700, "rate": 0.10},
        "STRC": {"notional_musd": 7_800, "rate": 0.12},
        "STRK": {"notional_musd": 2_100, "rate": 0.08},
        "STRD": {"notional_musd": 4_200, "rate": 0.10},
    },
    "preferred_aggregate_musd": 17_800,
    "common_shares_outstanding_m": 350.448,
    "diluted_shares_m": 285.0,
    "weekly_btc_sales_musd": 216.0,
    "prev_pref_notional_musd": 17_800,
    "prev_mnav_equity": 0.62,
}


@dataclass
class Observation:
    name: str
    value: float | str | None
    source: str
    url: str
    fetched_at: str
    ok: bool
    detail: str = ""
    as_of: str | None = None
    basis: str | None = None
    source_tier: str = "secondary"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def fetch_url(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            return gzip.decompress(data)
        return data


def fetch_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    return json.loads(fetch_url(url, headers=headers).decode("utf-8"))


def fetch_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    return fetch_url(url, headers=headers).decode("utf-8", errors="replace")


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", "N/D"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def obs(
    name: str,
    value: float | str | None,
    source: str,
    url: str,
    ok: bool = True,
    detail: str = "",
    *,
    as_of: str | None = None,
    basis: str | None = None,
    source_tier: str = "secondary",
) -> Observation:
    return Observation(
        name=name,
        value=value,
        source=source,
        url=url,
        fetched_at=now_iso(),
        ok=ok,
        detail=detail,
        as_of=as_of,
        basis=basis,
        source_tier=source_tier,
    )


def collect_coingecko_btc() -> list[Observation]:
    url = (
        "https://api.coingecko.com/api/v3/simple/price?"
        "ids=bitcoin,ethereum&vs_currencies=usd&include_market_cap=true&"
        "include_24hr_vol=true&include_24hr_change=true&include_last_updated_at=true"
    )
    payload = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    data = payload["bitcoin"]
    eth = payload.get("ethereum", {})
    btc_as_of = datetime.fromtimestamp(data["last_updated_at"], timezone.utc).isoformat() if data.get("last_updated_at") else None
    eth_as_of = datetime.fromtimestamp(eth["last_updated_at"], timezone.utc).isoformat() if eth.get("last_updated_at") else None
    return [
        obs("btc_usd_coingecko", data.get("usd"), "CoinGecko simple price", url, as_of=btc_as_of, basis="spot", source_tier="independent_market"),
        obs("btc_market_cap_usd", data.get("usd_market_cap"), "CoinGecko simple price", url),
        obs("btc_24h_volume_usd", data.get("usd_24h_vol"), "CoinGecko simple price", url),
        obs("btc_24h_change_pct", data.get("usd_24h_change"), "CoinGecko simple price", url),
        obs("btc_last_updated_unix", data.get("last_updated_at"), "CoinGecko simple price", url),
        obs("eth_usd_coingecko", eth.get("usd"), "CoinGecko simple price", url, ok=eth.get("usd") is not None, as_of=eth_as_of, basis="spot", source_tier="independent_market"),
    ]


def collect_coinbase_btc() -> Observation:
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    return obs("btc_usd_coinbase", safe_float(data.get("price")), "Coinbase Exchange ticker", url, as_of=data.get("time"), basis="spot", source_tier="primary_market")


def collect_coinbase_eth() -> Observation:
    url = "https://api.exchange.coinbase.com/products/ETH-USD/ticker"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    return obs("eth_usd_coinbase", safe_float(data.get("price")), "Coinbase Exchange ticker", url, as_of=data.get("time"), basis="spot", source_tier="primary_market")


def collect_kraken_spot(symbol: str, pair: str) -> Observation:
    url = f"https://api.kraken.com/0/public/Ticker?{urllib.parse.urlencode({'pair': pair})}"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    if data.get("error"):
        raise ValueError(f"Kraken {pair}: {data['error']}")
    row = next(iter((data.get("result") or {}).values()), {})
    value = safe_float((row.get("c") or [None])[0])
    return obs(
        f"{symbol.lower()}_usd_kraken",
        value,
        "Kraken Spot ticker",
        url,
        ok=value is not None,
        detail="Kraken Ticker 沒有上游時間戳；以擷取時間作新鮮度依據",
        as_of=now_iso(),
        basis="spot_retrieval_time",
        source_tier="primary_market",
    )


def yahoo_daily_closes(ticker: str, range_: str = "1y", interval: str = "1d") -> tuple[list[dict[str, Any]], str]:
    encoded = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_}&interval={interval}"
    data = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    result = data["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quotes.get("close", [])
    lows = quotes.get("low", [])
    highs = quotes.get("high", [])
    rows: list[dict[str, Any]] = []
    for idx, timestamp in enumerate(timestamps):
        close = safe_float(closes[idx] if idx < len(closes) else None)
        if close is None:
            continue
        rows.append({
            "timestamp": timestamp,
            "close": close,
            "low": safe_float(lows[idx] if idx < len(lows) else None),
            "high": safe_float(highs[idx] if idx < len(highs) else None),
        })
    return rows, url


def collect_yahoo_btc_technicals() -> list[Observation]:
    rows, url = yahoo_daily_closes("BTC-USD", "1y")
    weekly_rows, weekly_url = yahoo_daily_closes("BTC-USD", "5y", "1wk")
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    rows = [row for row in rows if datetime.fromtimestamp(row["timestamp"], timezone.utc).date() < today]
    weekly_rows = [row for row in weekly_rows if datetime.fromtimestamp(row["timestamp"], timezone.utc).date() < week_start]
    closes = [row["close"] for row in rows]
    weekly_closes = [row["close"] for row in weekly_rows]
    if not closes:
        return [obs("btc_technical_error", None, "Yahoo Finance chart", url, ok=False, detail="no closes")]
    latest = closes[-1]
    ma200 = sum(closes[-200:]) / min(200, len(closes)) if len(closes) >= 30 else None
    ma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 30 else None
    wma200 = sum(weekly_closes[-200:]) / 200 if len(weekly_closes) >= 200 else None
    ath_1y = max(closes)
    ath_index = closes.index(ath_1y)
    ath_timestamp = rows[ath_index]["timestamp"]
    ath_date = datetime.fromtimestamp(ath_timestamp, timezone.utc).date()
    latest_date = datetime.fromtimestamp(rows[-1]["timestamp"], timezone.utc).date()
    days_from_ath = (latest_date - ath_date).days
    ret_7d = latest / closes[-8] - 1 if len(closes) > 8 else None
    ret_30d = latest / closes[-31] - 1 if len(closes) > 31 else None
    ret_90d = latest / closes[-91] - 1 if len(closes) > 91 else None
    drawdown_1y = latest / ath_1y - 1 if ath_1y else None
    latest_as_of = latest_date.isoformat()
    weekly_as_of = datetime.fromtimestamp(weekly_rows[-1]["timestamp"], timezone.utc).date().isoformat() if weekly_rows else None
    detail = f"points={len(closes)} latest_timestamp={rows[-1].get('timestamp')} completed_bar_only=true basis=daily_close"
    return [
        obs("btc_yahoo_close", latest, "Yahoo Finance BTC-USD chart", url, ok=True, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="independent_market"),
        obs("btc_200dma", ma200, "Yahoo Finance BTC-USD chart", url, ok=ma200 is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_50dma", ma50, "Yahoo Finance BTC-USD chart", url, ok=ma50 is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_200wma", wma200, "Yahoo Finance BTC-USD weekly chart", weekly_url, ok=wma200 is not None, detail=f"points={len(weekly_closes)} completed_bar_only=true basis=weekly_close", as_of=weekly_as_of, basis="completed_weekly_close", source_tier="derived_market"),
        obs("btc_1y_ath", ath_1y, "Yahoo Finance BTC-USD chart", url, ok=True, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_1y_ath_date", ath_date.isoformat(), "Yahoo Finance BTC-USD chart", url, ok=True, detail=detail, as_of=ath_date.isoformat(), basis="daily_close", source_tier="derived_market"),
        obs("btc_days_from_1y_ath", days_from_ath, "Yahoo Finance BTC-USD chart", url, ok=True, detail=detail, as_of=latest_date.isoformat(), basis="calendar_days", source_tier="derived_market"),
        obs("btc_drawdown_1y_pct", drawdown_1y, "Yahoo Finance BTC-USD chart", url, ok=drawdown_1y is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_return_7d_pct", ret_7d, "Yahoo Finance BTC-USD chart", url, ok=ret_7d is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_return_30d_pct", ret_30d, "Yahoo Finance BTC-USD chart", url, ok=ret_30d is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
        obs("btc_return_90d_pct", ret_90d, "Yahoo Finance BTC-USD chart", url, ok=ret_90d is not None, detail=detail, as_of=latest_as_of, basis="completed_daily_close", source_tier="derived_market"),
    ]


def yahoo_chart(ticker: str) -> tuple[float | None, str, str, str | None]:
    encoded = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    data = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    result = data["chart"]["result"][0]
    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    source_field = "regularMarketPrice"
    if price is None:
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        price = closes[-1] if closes else None
        source_field = "latest_daily_close"
    detail = (
        f"quote_basis=regular_market_close source_field={source_field} "
        f"regularMarketTime={meta.get('regularMarketTime')} timezone={meta.get('timezone')}"
    )
    as_of = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).isoformat() if meta.get("regularMarketTime") else None
    return safe_float(price), url, detail, as_of



def clean_price(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").replace("%", "").strip()
    return safe_float(value)


def nasdaq_quote_basis(primary: dict[str, Any]) -> str:
    timestamp = str(primary.get("lastTradeTimestamp") or "")
    is_realtime = str(primary.get("isRealTime") or "").lower() == "true"
    if not is_realtime:
        return "regular_or_delayed_quote"
    match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)\s*ET", timestamp, re.IGNORECASE)
    if not match:
        return "realtime_unknown_session"
    hour = int(match.group(1))
    minute = int(match.group(2))
    am_pm = match.group(3).upper()
    if am_pm == "PM" and hour != 12:
        hour += 12
    if am_pm == "AM" and hour == 12:
        hour = 0
    minutes = hour * 60 + minute
    if minutes < 9 * 60 + 30 or minutes > 16 * 60:
        return "extended_hours_realtime"
    return "regular_session_realtime"


def collect_nasdaq_equity(ticker: str) -> Observation:
    url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(ticker)}/info?assetclass=stocks"
    data = fetch_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    primary = data.get("data", {}).get("primaryData", {})
    value = clean_price(primary.get("lastSalePrice"))
    quote_basis = nasdaq_quote_basis(primary)
    detail = f"quote_basis={quote_basis} timestamp={primary.get('lastTradeTimestamp')} realTime={primary.get('isRealTime')}"
    return obs(f"{ticker.lower()}_usd_nasdaq", value, "Nasdaq quote API", url, ok=value is not None, detail=detail)

def collect_yahoo_equity(ticker: str) -> Observation:
    value, url, detail, as_of = yahoo_chart(ticker)
    return obs(f"{ticker.lower()}_usd_yahoo", value, "Yahoo Finance chart", url, ok=value is not None, detail=detail, as_of=as_of, basis="regular_market_close", source_tier="independent_market")


def collect_stooq_close(ticker: str, symbol: str) -> Observation:
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(symbol)}&f=sd2t2ohlcv&h&e=csv"
    text = fetch_text(url, headers={"User-Agent": "Mozilla/5.0"})
    rows = list(csv.DictReader(text.splitlines()))
    value = None
    detail = ""
    if rows:
        value = safe_float(rows[0].get("Close"))
        detail = f"date={rows[0].get('Date')} time={rows[0].get('Time')}"
    return obs(f"{ticker.lower()}_usd_stooq", value, "Stooq quote CSV", url, ok=value is not None, detail=detail)



def collect_fear_greed() -> list[Observation]:
    url = "https://api.alternative.me/fng/?limit=1"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    row = (data.get("data") or [{}])[0]
    as_of = datetime.fromtimestamp(int(row["timestamp"]), timezone.utc).isoformat() if row.get("timestamp") else None
    return [
        obs("fear_greed_value", safe_float(row.get("value")), "Alternative.me Fear & Greed", url, ok=row.get("value") is not None, detail=str(row.get("value_classification") or ""), as_of=as_of, basis="daily_index", source_tier="independent_sentiment"),
        obs("fear_greed_timestamp", row.get("timestamp"), "Alternative.me Fear & Greed", url, ok=bool(row.get("timestamp")), as_of=as_of, basis="source_timestamp", source_tier="independent_sentiment"),
    ]


def collect_mempool_fees() -> list[Observation]:
    url = "https://mempool.space/api/v1/fees/recommended"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    return [
        obs("btc_fee_fastest_sat_vb", safe_float(data.get("fastestFee")), "mempool.space fees", url),
        obs("btc_fee_hour_sat_vb", safe_float(data.get("hourFee")), "mempool.space fees", url),
    ]


def collect_blockchain_hashrate() -> Observation:
    url = "https://api.blockchain.info/charts/hash-rate?timespan=7days&format=json&cors=true"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    values = data.get("values") or []
    latest = values[-1] if values else {}
    return obs("btc_hashrate_ths", safe_float(latest.get("y")), "Blockchain.com hash-rate chart", url, ok=latest.get("y") is not None, detail=f"timestamp={latest.get('x')}")


def collect_treasury_average_rate() -> Observation:
    url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates?sort=-record_date&page[size]=1&format=json"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    row = (data.get("data") or [{}])[0]
    return obs("treasury_avg_bill_rate_pct", safe_float(row.get("avg_interest_rate_amt")), "Treasury Fiscal Data avg interest rates", url, ok=row.get("avg_interest_rate_amt") is not None, detail=f"record_date={row.get('record_date')} {row.get('security_desc')}")

def collect_coinmetrics_btc_cycle() -> list[Observation]:
    metrics = "PriceUSD,CapMVRVCur,SplyCur,CapMrktCurUSD"
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?" + urllib.parse.urlencode({
        "assets": "btc",
        "metrics": metrics,
        "frequency": "1d",
        "page_size": "7",
    })
    data = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    rows = data.get("data") or []
    latest = rows[-1] if rows else {}
    detail = f"time={latest.get('time')} metrics={metrics}"
    as_of = str(latest.get("time") or "")[:10] or None
    return [
        obs("btc_price_coinmetrics_usd", safe_float(latest.get("PriceUSD")), "Coin Metrics community API", url, ok=latest.get("PriceUSD") is not None, detail=detail, as_of=as_of, basis="daily_network_metric", source_tier="independent_onchain"),
        obs("btc_mvrv_current", safe_float(latest.get("CapMVRVCur")), "Coin Metrics community API", url, ok=latest.get("CapMVRVCur") is not None, detail=detail, as_of=as_of, basis="daily_network_metric", source_tier="independent_onchain"),
        obs("btc_supply_current", safe_float(latest.get("SplyCur")), "Coin Metrics community API", url, ok=latest.get("SplyCur") is not None, detail=detail, as_of=as_of, basis="daily_network_metric", source_tier="independent_onchain"),
        obs("btc_market_cap_coinmetrics_usd", safe_float(latest.get("CapMrktCurUSD")), "Coin Metrics community API", url, ok=latest.get("CapMrktCurUSD") is not None, detail=detail, as_of=as_of, basis="daily_network_metric", source_tier="independent_onchain"),
    ]


def parse_signed_number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("+", "").strip()
    multiplier = 1.0
    if cleaned.endswith("B"):
        multiplier = 1_000_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("K"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]
    return safe_float(cleaned) * multiplier if safe_float(cleaned) is not None else None


class EtfFlowTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.table_done = False
        self.in_row = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and not self.table_done and "stats-table" in str(attributes.get("class") or ""):
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.cells = []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if self.in_cell and clean:
            self.cell_parts.append(clean)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in {"th", "td"}:
            self.cells.append(" ".join(self.cell_parts))
            self.cell_parts = []
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if self.cells:
                self.rows.append(self.cells)
            self.in_row = False
        elif self.in_table and tag == "table":
            self.in_table = False
            self.table_done = True


def collect_walletpilot_etf_source() -> dict[str, Any]:
    url = "https://www.walletpilot.com/bitcoin-tracker/etfs"
    html = fetch_text(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})

    def extract(label: str) -> tuple[float | None, float | None]:
        pattern = rf"{re.escape(label)}</h3>.*?<p[^>]*>([+-]?[0-9,]+) BTC</p>.*?<p[^>]*>([+-]?\$[0-9,.]+[KMB]?)</p>"
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            return None, None
        btc = parse_signed_number(match.group(1))
        usd = parse_signed_number(match.group(2).replace("$", ""))
        return btc, usd
    one_btc, one_usd = extract("1-Day Net Flows")
    seven_btc, seven_usd = extract("7-Day Net Flows")
    thirty_btc, thirty_usd = extract("30-Day Net Flows")
    flow_dates = sorted(set(re.findall(r'lastFlowDate:"(\d{4}-\d{2}-\d{2})T', html)))
    as_of = flow_dates[-1] if flow_dates else None
    if one_usd is None or as_of is None:
        raise ValueError("WalletPilot ETF summary or lastFlowDate missing")
    return {
        "provider": "WalletPilot",
        "url": url,
        "as_of": as_of,
        "flow_1d_btc": one_btc,
        "flow_1d_usd": one_usd,
        "flow_7d_btc": seven_btc,
        "flow_7d_usd": seven_usd,
        "flow_30d_btc": thirty_btc,
        "flow_30d_usd": thirty_usd,
        "basis": "provider_rolling_windows_with_fund_level_lastFlowDate",
    }


def collect_bitbo_btc_etf_source() -> dict[str, Any]:
    url = "https://bitbo.io/treasuries/etf-flows/"
    retrieved_at = now_iso()
    parser = EtfFlowTableParser()
    parser.feed(fetch_text(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}))
    if len(parser.rows) < 2 or parser.rows[0][0] != "Date" or parser.rows[0][-1] != "Totals":
        raise ValueError("Bitbo ETF flow table schema changed")
    headers = parser.rows[0]
    observed_tickers = set(headers[1:-1])
    expected_tickers = sorted(ETF_EXPECTED_US_SPOT_TICKERS["BTC"] | observed_tickers)
    rows: list[dict[str, Any]] = []
    for cells in parser.rows[1:]:
        if len(cells) != len(headers):
            continue
        try:
            as_of = datetime.strptime(cells[0], "%b %d, %Y").date().isoformat()
        except ValueError:
            continue
        values = {header: safe_float(value) for header, value in zip(headers[1:], cells[1:])}
        if values.get("Totals") is None:
            continue
        rows.append({
            "date": as_of,
            "flow_usd": values["Totals"] * 1_000_000,
            "components_usd": {ticker: value * 1_000_000 for ticker, value in values.items() if ticker != "Totals" and value is not None},
            "component_count": sum(values.get(ticker) is not None for ticker in expected_tickers),
            "component_completeness": sum(values.get(ticker) is not None for ticker in expected_tickers) / len(expected_tickers),
        })
    rows.sort(key=lambda item: item["date"])
    if not rows:
        raise ValueError("Bitbo ETF flow rows missing")
    return {
        "provider": "Bitbo",
        "url": url,
        "series": rows,
        "as_of": rows[-1]["date"],
        "updated_at": retrieved_at,
        "updated_at_basis": "retrieval_time_provider_has_no_update_timestamp",
        "expected_tickers": expected_tickers,
        "basis": "US_spot_ETF_daily_flow_USD_millions_table",
    }


def collect_theblock_etf_source(asset: str) -> dict[str, Any]:
    slug = "btcspotetfflows" if asset == "BTC" else "ethspotetfflows"
    url = f"https://data.tbstat.com/dashboard/markets_structuredproducts_{slug}_daily_other.json"
    payload = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    if payload.get("Frequency") != "Daily" or not isinstance(payload.get("Series"), dict) or not payload["Series"]:
        raise ValueError(f"The Block {asset} ETF schema changed")
    rows: dict[str, dict[str, float]] = {}
    for ticker, series in payload["Series"].items():
        for point in series.get("Data", []):
            timestamp = safe_float(point.get("Timestamp"))
            value = safe_float(point.get("Result"))
            if timestamp is None or value is None:
                continue
            as_of = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            rows.setdefault(as_of, {})[ticker] = value
    if not rows:
        raise ValueError(f"The Block {asset} ETF daily rows missing")
    latest_date = max(rows)
    active_cutoff = (datetime.fromisoformat(latest_date).date() - timedelta(days=7)).isoformat()
    observed_tickers = {
        ticker
        for ticker, series in payload["Series"].items()
        if any(
            safe_float(point.get("Timestamp")) is not None
            and datetime.fromtimestamp(float(point["Timestamp"]), timezone.utc).date().isoformat() >= active_cutoff
            for point in series.get("Data", [])
        )
    }
    expected_tickers = sorted(ETF_EXPECTED_US_SPOT_TICKERS[asset] | observed_tickers)
    daily = [
        {
            "date": as_of,
            "flow_usd": sum(components.values()),
            "components_usd": components,
            "component_count": len(components),
            "component_completeness": len(components) / len(expected_tickers) if expected_tickers else 0,
        }
        for as_of, components in sorted(rows.items())
    ]
    updated_at = datetime.fromtimestamp(float(payload.get("UpdatedAt")), timezone.utc).isoformat() if safe_float(payload.get("UpdatedAt")) is not None else None
    return {
        "provider": "The Block",
        "url": url,
        "series": daily,
        "as_of": daily[-1]["date"],
        "updated_at": updated_at,
        "expected_tickers": expected_tickers,
        "basis": "US_spot_ETF_daily_fund_flows_component_sum",
    }


def collect_blockworks_etf_source(asset: str) -> dict[str, Any]:
    contract = {
        "BTC": ("bitcoin-etfs", "8146", "6528", "date", "type_spot_unitedstates_flow_usd", "crypto_asset"),
        "ETH": ("ethereum-etfs", "6561", "6558", "dt", "country_unitedstates_flow_usd", "ticker"),
    }[asset]
    dashboard, visualization_id, ticker_visualization_id, date_key, flow_key, ticker_key = contract
    url = f"https://blockworks.com/api/studio/dashboard/{dashboard}/visualization/{visualization_id}/execution?limit=50000&page=1"
    payload = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    retrieved_at = now_iso()
    ticker_url = f"https://blockworks.com/api/studio/dashboard/{dashboard}/visualization/{ticker_visualization_id}/execution?limit=50000&page=1"
    ticker_payload = fetch_json(ticker_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    components_by_date: dict[str, dict[str, float]] = {}
    for row in ticker_payload.get("data", []):
        as_of = str(row.get("dt") or "")[:10]
        ticker = str(row.get(ticker_key) or "").strip().upper()
        if asset == "BTC" and ticker == "FBTC (USD)":
            ticker = "FBTC"
        value = safe_float(row.get("type_spot_flow_usd"))
        if as_of and ticker in ETF_EXPECTED_US_SPOT_TICKERS[asset] and value is not None:
            components_by_date.setdefault(as_of, {})[ticker] = value
    latest_component_date = max(components_by_date, default=None)
    active_cutoff = (
        datetime.fromisoformat(latest_component_date).date() - timedelta(days=7)
    ).isoformat() if latest_component_date else None
    observed_tickers = {
        ticker
        for as_of, components in components_by_date.items()
        if active_cutoff is not None and as_of >= active_cutoff
        for ticker in components
    }
    expected_tickers = sorted(ETF_EXPECTED_US_SPOT_TICKERS[asset] | observed_tickers)
    rows = [
        {
            "date": str(row.get(date_key) or "")[:10],
            "flow_usd": safe_float(row.get(flow_key)),
            "components_usd": components_by_date.get(str(row.get(date_key) or "")[:10], {}),
            "component_count": len(components_by_date.get(str(row.get(date_key) or "")[:10], {})),
            "component_completeness": (
                len(components_by_date.get(str(row.get(date_key) or "")[:10], {})) / len(expected_tickers)
                if expected_tickers else 0
            ),
        }
        for row in payload.get("data", [])
        if row.get(date_key) and safe_float(row.get(flow_key)) is not None
    ]
    rows.sort(key=lambda item: item["date"])
    if not rows:
        raise ValueError(f"Blockworks {asset} ETF rows missing")
    return {
        "provider": "Blockworks / Trackinsights",
        "url": url,
        "ticker_url": ticker_url,
        "series": rows,
        "as_of": rows[-1]["date"],
        "updated_at": retrieved_at,
        "updated_at_basis": "retrieval_time_provider_has_no_update_timestamp",
        "expected_tickers": expected_tickers,
        "basis": "provider_labeled_US_spot_ETF_flow_date",
    }


def collect_coinmarketcap_etf_source(asset: str) -> dict[str, Any]:
    category = asset.lower()
    url = f"https://api.coinmarketcap.com/data-api/v3/etf/overview/netflow/chart?category={category}&range=30d&convertId=2781"
    payload = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    points = payload.get("data", {}).get("points", [])
    rows = []
    for point in points:
        timestamp = safe_float(point.get("timestamp"))
        value = safe_float(point.get("value"))
        if timestamp is None or value is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(timestamp / 1000, timezone.utc).date().isoformat(),
            "flow_usd": value,
            "components_usd": {},
        })
    rows.sort(key=lambda item: item["date"])
    if not rows:
        raise ValueError(f"CoinMarketCap {asset} ETF net-flow chart missing")
    return {
        "provider": "CoinMarketCap ETF",
        "url": url,
        "series": rows,
        "as_of": rows[-1]["date"],
        "updated_at": now_iso(),
        "updated_at_basis": "api_response_retrieval_time",
        "basis": "provider_reported_US_spot_ETF_daily_total_flow",
    }


def ishares_holding(asset: str, as_of: str) -> dict[str, Any]:
    portfolio_id = "333011" if asset == "BTC" else "337614"
    date_token = as_of.replace("-", "")
    url = (
        "https://www.ishares.com/varnish-api/blk-one01-product-data/product-data/api/v2/get-product-data?"
        + urllib.parse.urlencode({"portfolioId": portfolio_id, "component": "holdings.all", "asOfDate": date_token})
    )
    payload = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    points = payload.get("componentsByNameMap", {}).get("holdings", {}).get("containersByNameMap", {}).get("all", {}).get("dataPointsByNameMap", {})
    tickers = points.get("ticker", {}).get("value", [])
    units = points.get("unitsHeld", {}).get("value", [])
    market_values = points.get("marketValue", {}).get("value", [])
    if not isinstance(tickers, list) or not isinstance(units, list) or not isinstance(market_values, list):
        raise ValueError(f"iShares {asset} holding not published for {as_of}")
    try:
        index = tickers.index(asset)
        units_held = safe_float(units[index])
        market_value = safe_float(market_values[index])
    except (ValueError, IndexError):
        units_held = market_value = None
    provider_date = str(points.get("asOfDate", {}).get("value") or "")
    if units_held is None or market_value is None or provider_date != date_token:
        raise ValueError(f"iShares {asset} holding missing for {as_of}")
    return {"units": units_held, "market_value_usd": market_value, "as_of": as_of, "url": url}


def prior_ishares_holding(asset: str, as_of: str) -> dict[str, Any]:
    current_date = datetime.fromisoformat(as_of).date()
    for offset in range(1, 8):
        candidate = (current_date - timedelta(days=offset)).isoformat()
        try:
            return ishares_holding(asset, candidate)
        except Exception:
            continue
    raise ValueError(f"iShares {asset} prior holding date missing within 7 calendar days")


def rolling_calendar_flow(rows: list[dict[str, Any]], days: int) -> float | None:
    if not rows:
        return None
    latest = datetime.fromisoformat(rows[-1]["date"]).date()
    cutoff = latest - timedelta(days=days - 1)
    values = [safe_float(row.get("flow_usd")) for row in rows if datetime.fromisoformat(row["date"]).date() >= cutoff]
    clean = [value for value in values if value is not None]
    return sum(clean) if clean else None


def relative_difference(first: float | None, second: float | None, *, scale_floor: float = 0.0) -> float | None:
    if first is None or second is None:
        return None
    denominator = max((abs(first) + abs(second)) / 2, scale_floor)
    return abs(first - second) / denominator if denominator else 0.0


def etf_quorum_passes(
    canonical_provider: str | None,
    component_completeness: float | None,
    official_gap: float | None,
    official_component_coverage: float | None,
    backup_component_gap: float | None,
    backup_component_coverage: float | None,
    backup_same_date: bool,
    canonical_total_reconciled: bool,
    amount_sanity_pass: bool,
    validation_source_count: int,
    updated_age_hours: float | None,
    market_age_days: int,
) -> bool:
    return bool(
        canonical_provider in ETF_COMPONENT_PROVIDERS
        and component_completeness is not None
        and component_completeness >= 0.95
        and official_gap is not None
        and official_gap <= 0.05
        and official_component_coverage is not None
        and official_component_coverage >= 0.30
        and backup_component_gap is not None
        and backup_component_gap <= 0.05
        and backup_component_coverage is not None
        and backup_component_coverage >= 0.30
        and backup_same_date
        and canonical_total_reconciled
        and amount_sanity_pass
        and validation_source_count >= 3
        and updated_age_hours is not None
        and -1 <= updated_age_hours <= 36
        and 0 <= market_age_days <= 5
    )


def etf_backup_sample_validation(
    asset: str,
    canonical_provider: str,
    latest: dict[str, Any],
    providers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    validation_cohort = {"BTC": ["IBIT", "FBTC"], "ETH": ["ETHA"]}[asset]
    canonical_components = latest.get("components_usd", {})
    gross_component_flow = sum(abs(safe_float(value) or 0) for value in canonical_components.values())
    candidates: list[dict[str, Any]] = []
    for provider_name, provider in providers.items():
        if provider_name == canonical_provider or not provider.get("series"):
            continue
        backup_latest = next(
            (row for row in reversed(provider["series"]) if row.get("date") == latest.get("date")),
            None,
        )
        if backup_latest is None:
            continue
        backup_components = backup_latest.get("components_usd", {})
        matched = [
            ticker
            for ticker in validation_cohort
            if safe_float(canonical_components.get(ticker)) is not None and safe_float(backup_components.get(ticker)) is not None
        ]
        if not matched:
            canonical_total = safe_float(latest.get("flow_usd"))
            backup_total = safe_float(backup_latest.get("flow_usd"))
            total_gap = relative_difference(canonical_total, backup_total, scale_floor=100_000_000)
            if canonical_total is None or backup_total is None or total_gap is None:
                continue
            candidates.append({
                "provider": provider_name,
                "validation_type": "same_date_aggregate_total",
                "as_of": backup_latest.get("date"),
                "matched_tickers": ["TOTAL"],
                "canonical_values_usd": {"TOTAL": canonical_total},
                "backup_values_usd": {"TOTAL": backup_total},
                "weighted_difference_usd": abs(canonical_total - backup_total),
                "weighted_reference_usd": (abs(canonical_total) + abs(backup_total)) / 2,
                "normalized_gap": total_gap,
                "component_normalized_gaps": {"TOTAL": total_gap},
                "maximum_component_gap": total_gap,
                "gross_component_coverage": 1.0,
            })
            continue
        component_gaps = {
            ticker: relative_difference(
                float(canonical_components[ticker]),
                float(backup_components[ticker]),
                scale_floor=100_000_000,
            )
            for ticker in matched
        }
        weighted_difference = sum(abs(float(canonical_components[ticker]) - float(backup_components[ticker])) for ticker in matched)
        weighted_reference = sum(
            (abs(float(canonical_components[ticker])) + abs(float(backup_components[ticker]))) / 2
            for ticker in matched
        )
        gap = weighted_difference / max(weighted_reference, 100_000_000)
        coverage = sum(abs(float(canonical_components[ticker])) for ticker in matched) / gross_component_flow if gross_component_flow else None
        candidates.append({
            "provider": provider_name,
            "validation_type": "same_date_named_fund_sample",
            "as_of": backup_latest.get("date"),
            "matched_tickers": matched,
            "canonical_values_usd": {ticker: float(canonical_components[ticker]) for ticker in matched},
            "backup_values_usd": {ticker: float(backup_components[ticker]) for ticker in matched},
            "weighted_difference_usd": weighted_difference,
            "weighted_reference_usd": weighted_reference,
            "normalized_gap": gap,
            "component_normalized_gaps": component_gaps,
            "maximum_component_gap": max(component_gaps.values()) if component_gaps else None,
            "gross_component_coverage": coverage,
        })
    selected = min(
        candidates,
        key=lambda item: (-(item["gross_component_coverage"] or 0), item["maximum_component_gap"], item["normalized_gap"]),
    ) if candidates else {
        "provider": None,
        "validation_type": None,
        "as_of": None,
        "matched_tickers": [],
        "canonical_values_usd": {},
        "backup_values_usd": {},
        "weighted_difference_usd": None,
        "weighted_reference_usd": None,
        "normalized_gap": None,
        "component_normalized_gaps": {},
        "maximum_component_gap": None,
        "gross_component_coverage": None,
    }
    selected["candidate_count"] = len(candidates)
    return selected


def select_etf_canonical_provider(providers: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for provider in providers.values():
        if provider.get("provider") not in ETF_COMPONENT_PROVIDERS or not provider.get("series"):
            continue
        latest = provider["series"][-1]
        if not latest.get("date") or not latest.get("components_usd") or not provider.get("expected_tickers"):
            continue
        candidates.append(provider)
    if not candidates:
        return None
    latest_date = max(provider["series"][-1]["date"] for provider in candidates)
    same_date = [provider for provider in candidates if provider["series"][-1]["date"] == latest_date]
    return min(
        same_date,
        key=lambda provider: (
            -(safe_float(provider["series"][-1].get("component_completeness")) or 0),
            ETF_PROVIDER_PRIORITY.get(str(provider.get("provider")), 99),
        ),
    )


def etf_amount_sanity(latest: dict[str, Any], official_proxy: float | None, backup: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    components = [safe_float(value) for value in latest.get("components_usd", {}).values()]
    backup_components = [safe_float(value) for value in backup.get("backup_values_usd", {}).values()]
    if not components or any(value is None for value in components):
        errors.append("canonical component amount missing")
    else:
        clean_components = [value for value in components if value is not None]
        if any(abs(value) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD for value in clean_components):
            errors.append("canonical single-fund daily flow exceeds sanity bound")
        if sum(abs(value) for value in clean_components) > ETF_MAX_GROSS_DAILY_FLOW_USD:
            errors.append("canonical gross daily flow exceeds sanity bound")
    if official_proxy is None or abs(official_proxy) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD:
        errors.append("official major-fund proxy missing or exceeds sanity bound")
    if any(value is None or abs(value) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD for value in backup_components):
        errors.append("backup sample amount missing or exceeds sanity bound")
    return not errors, errors


def evaluate_etf_candidate(
    asset: str,
    canonical: dict[str, Any],
    rows: list[dict[str, Any]],
    index: int,
    providers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    latest = rows[index]
    previous = rows[index - 1] if index >= 1 else None
    largest_ticker = "IBIT" if asset == "BTC" else "ETHA"
    official_component = safe_float(latest.get("components_usd", {}).get(largest_ticker))
    gross_component_flow = sum(abs(safe_float(value) or 0) for value in latest.get("components_usd", {}).values())
    official_component_coverage = abs(official_component) / gross_component_flow if official_component is not None and gross_component_flow else None
    official_proxy = None
    official_gap = None
    official_url = None
    errors: list[str] = []
    if previous and official_component is not None:
        try:
            current_holding = ishares_holding(asset, latest["date"])
            prior_holding = prior_ishares_holding(asset, latest["date"])
            if prior_holding["as_of"] != previous["date"]:
                raise ValueError(
                    f"canonical prior date {previous['date']} differs from official prior holding date {prior_holding['as_of']}"
                )
            unit_price = current_holding["market_value_usd"] / current_holding["units"] if current_holding["units"] else None
            official_proxy = (current_holding["units"] - prior_holding["units"]) * unit_price if unit_price is not None else None
            official_gap = relative_difference(official_component, official_proxy, scale_floor=100_000_000)
            official_url = current_holding["url"]
        except Exception as exc:
            errors.append(f"iShares {largest_ticker} {latest['date']} 官方持倉驗證失敗：{type(exc).__name__}")
    backup_validation = etf_backup_sample_validation(asset, str(canonical.get("provider")), latest, providers)
    backup_component_gap = safe_float(backup_validation.get("maximum_component_gap"))
    backup_component_coverage = safe_float(backup_validation.get("gross_component_coverage"))
    backup_same_date = backup_validation.get("as_of") == latest.get("date")
    amount_sanity_pass, amount_sanity_errors = etf_amount_sanity(latest, official_proxy, backup_validation)
    errors.extend(f"{asset} ETF 金額合理性檢查失敗：{item}" for item in amount_sanity_errors)
    validation_source_count = 1 + int(official_proxy is not None) + int(backup_validation.get("provider") is not None)
    canonical_total = safe_float(latest.get("flow_usd"))
    canonical_component_sum = sum(safe_float(value) or 0 for value in latest.get("components_usd", {}).values())
    canonical_total_difference = abs(canonical_component_sum - canonical_total) if canonical_total is not None else None
    canonical_total_tolerance = max(
        ETF_COMPONENT_SUM_ABSOLUTE_TOLERANCE_USD,
        ETF_COMPONENT_SUM_RELATIVE_TOLERANCE * max(abs(canonical_component_sum), abs(canonical_total or 0)),
    )
    canonical_total_reconciled = canonical_total_difference is not None and canonical_total_difference <= canonical_total_tolerance
    if not canonical_total_reconciled:
        errors.append("canonical total does not reconcile to fund components within the rounding contract")
    updated_age = None
    if canonical.get("updated_at"):
        try:
            updated_age = (datetime.now(timezone.utc) - datetime.fromisoformat(canonical["updated_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        except ValueError:
            updated_age = None
    market_age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(latest["date"]).date()).days
    component_count = int(latest.get("component_count") or len(latest.get("components_usd", {})))
    component_completeness = safe_float(latest.get("component_completeness"))
    verified = bool(
        official_component is not None
        and official_proxy is not None
        and etf_quorum_passes(
            canonical.get("provider"),
            component_completeness,
            official_gap,
            official_component_coverage,
            backup_component_gap,
            backup_component_coverage,
            backup_same_date,
            canonical_total_reconciled,
            amount_sanity_pass,
            validation_source_count,
            updated_age,
            market_age,
        )
    )
    return {
        "latest": latest,
        "component_count": component_count,
        "component_completeness": component_completeness,
        "largest_ticker": largest_ticker,
        "official_component": official_component,
        "gross_component_flow": gross_component_flow,
        "official_component_coverage": official_component_coverage,
        "official_proxy": official_proxy,
        "official_gap": official_gap,
        "official_url": official_url,
        "backup_validation": backup_validation,
        "backup_component_gap": backup_component_gap,
        "backup_component_coverage": backup_component_coverage,
        "backup_same_date": backup_same_date,
        "amount_sanity_pass": amount_sanity_pass,
        "amount_sanity_errors": amount_sanity_errors,
        "validation_source_count": validation_source_count,
        "canonical_component_sum_usd": canonical_component_sum,
        "canonical_total_difference_usd": canonical_total_difference,
        "canonical_total_tolerance_usd": canonical_total_tolerance,
        "canonical_total_reconciled": canonical_total_reconciled,
        "verified": verified,
        "errors": errors,
    }


def build_etf_flow_observations(asset: str, providers: dict[str, dict[str, Any]], incidents: list[str]) -> list[Observation]:
    canonical_candidates = [
        provider for provider in providers.values()
        if provider.get("provider") in ETF_COMPONENT_PROVIDERS and provider.get("series") and provider.get("expected_tickers")
    ]
    if not canonical_candidates:
        return [obs(f"{asset.lower()}_etf_flow_status", "unavailable", "ETF source pool", "", ok=False, detail="；".join(incidents))]
    evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for candidate in canonical_candidates:
        candidate_rows = candidate["series"]
        for index in range(len(candidate_rows) - 1, max(0, len(candidate_rows) - 6) - 1, -1):
            if index < 1 or (safe_float(candidate_rows[index].get("component_completeness")) or 0) < 0.95:
                continue
            evaluated.append((candidate, evaluate_etf_candidate(asset, candidate, candidate_rows, index, providers)))
    verified_candidates = [item for item in evaluated if item[1]["verified"]]
    if verified_candidates:
        canonical, selected = max(
            verified_candidates,
            key=lambda item: (
                item[1]["latest"]["date"],
                safe_float(item[1].get("component_completeness")) or 0,
                -ETF_PROVIDER_PRIORITY.get(str(item[0].get("provider")), 99),
            ),
        )
    else:
        canonical = select_etf_canonical_provider(providers)
        if not canonical:
            return [obs(f"{asset.lower()}_etf_flow_status", "unavailable", "ETF source pool", "", ok=False, detail="；".join(incidents))]
        rows = canonical["series"]
        selected = evaluate_etf_candidate(asset, canonical, rows, len(rows) - 1, providers) if len(rows) >= 2 else None
    if selected is None:
        return [obs(f"{asset.lower()}_etf_flow_status", "unavailable", "ETF source pool", canonical.get("url", ""), ok=False, detail="ETF history lacks a prior market date")]
    rows = canonical["series"]
    latest_published_date = max(
        (provider["series"][-1]["date"] for provider in providers.values() if provider.get("series") and provider["series"][-1].get("date")),
        default=rows[-1]["date"],
    )
    if selected["latest"]["date"] != latest_published_date:
        incidents.append(
            f"{asset} ETF 最新發布日 {latest_published_date} 尚未完成官方 T+1／完整 roster 驗證；改用最近完整驗證日 {selected['latest']['date']}"
        )
    elif not selected["verified"]:
        incidents.extend(selected["errors"])
    latest = selected["latest"]
    component_count = selected["component_count"]
    expected_ticker_count = len(canonical.get("expected_tickers", []))
    component_completeness = selected["component_completeness"]
    largest_ticker = selected["largest_ticker"]
    official_proxy = selected["official_proxy"]
    official_gap = selected["official_gap"]
    official_component = selected["official_component"]
    gross_component_flow = selected["gross_component_flow"]
    official_component_coverage = selected["official_component_coverage"]
    official_url = selected["official_url"]
    backup_validation = selected["backup_validation"]
    backup_component_gap = selected["backup_component_gap"]
    backup_component_coverage = selected["backup_component_coverage"]
    backup_same_date = selected["backup_same_date"]
    amount_sanity_pass = selected["amount_sanity_pass"]
    amount_sanity_errors = selected["amount_sanity_errors"]
    validation_source_count = selected["validation_source_count"]
    total = safe_float(latest.get("flow_usd"))
    verified = selected["verified"]
    status = "sample_cross_source_verified" if verified else "quorum_failed"
    scope = (
        f"canonical={canonical.get('provider')} component sum; component_completeness={component_completeness:.1%}; "
        f"{largest_ticker} official holdings-change proxy normalized_gap={official_gap:.2%} "
        f"(5% or USD 5m tolerance); official_component_gross_coverage={official_component_coverage:.1%}; "
        f"backup={backup_validation.get('provider')} same_date={backup_same_date} "
        f"sample_gap={backup_component_gap:.2%} sample_gross_coverage={backup_component_coverage:.1%}; "
        f"validation_sources={validation_source_count}; "
        f"market_date={latest['date']}; hard_trigger=false"
        if official_gap is not None
        and official_component_coverage is not None
        and backup_component_gap is not None
        and backup_component_coverage is not None
        and component_completeness is not None
        else f"canonical={canonical.get('provider')}; ETF sample validation incomplete; validation_sources={validation_source_count}; hard_trigger=false"
    )
    source_tier = "sample_cross_source_verified" if verified else "multi_source_unverified"
    url = canonical.get("url") or ""
    verified_rows = [row for row in rows if row.get("date") and row["date"] <= latest["date"]]
    backup_basis = (
        "same_date_aggregate_total_normalized_gap"
        if backup_validation.get("validation_type") == "same_date_aggregate_total"
        else "same_date_named_fund_sample_max_normalized_gap"
    )
    observations = [
        obs(f"{asset.lower()}_etf_flow_status", status, "ETF source pool", url, ok=verified, detail=scope, as_of=latest["date"], basis="cross_source_validation_status", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_flow_1d_usd", total, canonical.get("provider", "ETF source pool"), url, ok=verified and total is not None, detail=scope, as_of=latest["date"], basis="daily_US_spot_ETF_component_sum", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_flow_7d_usd", rolling_calendar_flow(verified_rows, 7), canonical.get("provider", "ETF source pool"), url, ok=verified, detail=scope, as_of=latest["date"], basis="rolling_7_calendar_days_US_spot_ETF_component_sum", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_flow_30d_usd", rolling_calendar_flow(verified_rows, 30), canonical.get("provider", "ETF source pool"), url, ok=verified, detail=scope, as_of=latest["date"], basis="rolling_30_calendar_days_US_spot_ETF_component_sum", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_flow_source_count", validation_source_count, "ETF source pool", url, ok=validation_source_count >= 3, detail=scope, as_of=latest["date"], basis="canonical_plus_official_plus_same_date_sample_source_count", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_component_completeness", component_completeness, canonical.get("provider", "ETF source pool"), url, ok=component_completeness is not None and component_completeness >= 0.95, detail=scope, as_of=latest["date"], basis="latest_date_observed_tickers_divided_by_expected_tickers", source_tier=source_tier),
        obs(f"{asset.lower()}_etf_official_major_fund_gap", official_gap, f"iShares {largest_ticker} official holdings", official_url or url, ok=official_gap is not None and official_gap <= 0.05, detail=scope, as_of=latest["date"], basis="official_holdings_change_vs_reported_fund_flow", source_tier="official_issuer_crosscheck"),
        obs(f"{asset.lower()}_etf_official_major_fund_coverage", official_component_coverage, f"iShares {largest_ticker} official holdings", official_url or url, ok=official_component_coverage is not None and official_component_coverage >= 0.30, detail=scope, as_of=latest["date"], basis="official_major_fund_share_of_gross_absolute_component_flows", source_tier="official_issuer_crosscheck"),
        obs(f"{asset.lower()}_etf_backup_component_gap", backup_component_gap, str(backup_validation.get("provider") or "ETF backup sample"), providers.get(str(backup_validation.get("provider")), {}).get("url", url), ok=backup_component_gap is not None and backup_component_gap <= 0.05, detail=scope, as_of=latest["date"], basis=backup_basis, source_tier="cross_source_sample_validation"),
        obs(f"{asset.lower()}_etf_backup_component_coverage", backup_component_coverage, str(backup_validation.get("provider") or "ETF backup sample"), providers.get(str(backup_validation.get("provider")), {}).get("url", url), ok=backup_component_coverage is not None and backup_component_coverage >= 0.30, detail=scope, as_of=latest["date"], basis="same_date_named_fund_sample_share_of_gross_absolute_component_flows", source_tier="cross_source_sample_validation"),
        obs(f"{asset.lower()}_etf_validation_inputs_json", json.dumps({
            "canonical_provider": canonical.get("provider"),
            "canonical_as_of": latest.get("date"),
            "latest_published_as_of": latest_published_date,
            "selection_policy": "latest_date_passing_canonical_roster_official_issuer_same_date_backup_freshness_and_sanity_gates",
            "canonical_updated_at": canonical.get("updated_at"),
            "canonical_updated_at_basis": canonical.get("updated_at_basis") or "provider_update_timestamp",
            "canonical_total_usd": total,
            "canonical_component_sum_usd": selected["canonical_component_sum_usd"],
            "canonical_total_difference_usd": selected["canonical_total_difference_usd"],
            "canonical_total_tolerance_usd": selected["canonical_total_tolerance_usd"],
            "canonical_total_reconciled": selected["canonical_total_reconciled"],
            "canonical_components_usd": latest.get("components_usd", {}),
            "gross_component_flow_usd": gross_component_flow,
            "component_count": component_count,
            "expected_ticker_count": expected_ticker_count,
            "expected_tickers": canonical.get("expected_tickers", []),
            "component_completeness": component_completeness,
            "official_ticker": largest_ticker,
            "official_component_usd": official_component,
            "official_proxy_usd": official_proxy,
            "official_normalized_gap": official_gap,
            "official_component_gross_coverage": official_component_coverage,
            "backup_sample": backup_validation,
            "amount_sanity_pass": amount_sanity_pass,
            "amount_sanity_errors": amount_sanity_errors,
            "amount_sanity_thresholds": {
                "maximum_absolute_single_fund_daily_flow_usd": ETF_MAX_ABS_DAILY_FUND_FLOW_USD,
                "maximum_gross_daily_flow_usd": ETF_MAX_GROSS_DAILY_FLOW_USD,
            },
            "validation_source_count": validation_source_count,
        }, ensure_ascii=False, sort_keys=True), "ETF validation inputs", url, ok=verified, detail=scope, as_of=latest["date"], basis="offline_reconstructable_validation_inputs", source_tier="internal_validation"),
        obs(f"{asset.lower()}_etf_official_major_fund_flow_proxy_usd", official_proxy, f"iShares {largest_ticker} official holdings", official_url or url, ok=official_proxy is not None, detail=scope, as_of=latest["date"], basis="holdings_unit_change_times_latest_reported_unit_value", source_tier="official_issuer_crosscheck"),
    ]
    for provider_name, provider in providers.items():
        provider_latest = provider.get("series", [])[-1] if provider.get("series") else {}
        observations.append(obs(
            f"{asset.lower()}_etf_provider_{re.sub(r'[^a-z0-9]+', '_', provider_name.lower()).strip('_')}_1d_usd",
            safe_float(provider_latest.get("flow_usd")),
            provider_name,
            provider.get("url", ""),
            ok=safe_float(provider_latest.get("flow_usd")) is not None,
            detail=f"provider_as_of={provider_latest.get('date')} canonical={provider_name == canonical.get('provider')}",
            as_of=provider_latest.get("date"),
            basis=provider.get("basis"),
            source_tier="independent_ETF_data_provider",
        ))
    if incidents:
        observations.append(obs(f"{asset.lower()}_etf_source_incidents", "；".join(incidents), "ETF source pool", url, detail=scope, as_of=latest["date"], basis="source_incident_log", source_tier="internal_validation"))
    return observations


def collect_verified_etf_flows() -> list[Observation]:
    observations: list[Observation] = []
    for asset in ("BTC", "ETH"):
        providers: dict[str, dict[str, Any]] = {}
        incidents: list[str] = []
        collectors = [
            ("The Block", lambda asset=asset: collect_theblock_etf_source(asset)),
            ("Blockworks / Trackinsights", lambda asset=asset: collect_blockworks_etf_source(asset)),
            ("CoinMarketCap ETF", lambda asset=asset: collect_coinmarketcap_etf_source(asset)),
        ]
        if asset == "BTC":
            collectors.extend([
                ("Bitbo", collect_bitbo_btc_etf_source),
                ("WalletPilot", collect_walletpilot_etf_source),
            ])
        for provider_name, collector in collectors:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    provider = collector()
                    if provider.get("series"):
                        providers[provider_name] = provider
                    elif asset == "BTC" and provider_name == "WalletPilot":
                        providers[provider_name] = {
                            **provider,
                            "series": [{"date": provider["as_of"], "flow_usd": provider["flow_1d_usd"], "components_usd": {}}],
                        }
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 3:
                        time.sleep(attempt)
            if last_error is not None:
                incidents.append(f"{provider_name} {asset} ETF 來源失敗：{type(last_error).__name__}")
        observations.extend(build_etf_flow_observations(asset, providers, incidents))
    return observations


def latest_sec_fact(
    facts: dict[str, Any],
    tag: str,
    unit: str = "USD",
    instant: bool | None = None,
    namespace: str = "us-gaap",
) -> tuple[float | None, str, str | None]:
    rows = facts.get(namespace, {}).get(tag, {}).get("units", {}).get(unit, [])
    if instant is True:
        rows = [row for row in rows if not row.get("start")]
    elif instant is False:
        rows = [row for row in rows if row.get("start")]
    if not rows:
        return None, f"namespace={namespace} tag={tag} unit={unit} missing", None
    latest = sorted(rows, key=lambda row: (row.get("end") or "", row.get("filed") or "", row.get("frame") or ""))[-1]
    detail = f"namespace={namespace} tag={tag} unit={unit} form={latest.get('form')} filed={latest.get('filed')} end={latest.get('end')} accn={latest.get('accn')}"
    return safe_float(latest.get("val")), detail, latest.get("end")


def collect_mstr_sec_companyfacts() -> list[Observation]:
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001050446.json"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    facts = data.get("facts", {})
    cash, cash_detail, cash_as_of = latest_sec_fact(facts, "CashAndCashEquivalentsAtCarryingValue", "USD", True)
    diluted, diluted_detail, diluted_as_of = latest_sec_fact(facts, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False)
    stockholders_equity, equity_detail, equity_as_of = latest_sec_fact(facts, "StockholdersEquity", "USD", True)
    pref_div, pref_div_detail, pref_div_as_of = latest_sec_fact(facts, "DividendsPreferredStock", "USD", False)
    pref_cash_div, pref_cash_div_detail, pref_cash_div_as_of = latest_sec_fact(facts, "DividendsPreferredStockCash", "USD", False)
    deferred_tax_liability, dtl_detail, dtl_as_of = latest_sec_fact(facts, "DeferredIncomeTaxLiabilitiesNet", "USD", True)
    return [
        obs("mstr_sec_cash_musd", cash / 1e6 if cash is not None else None, "SEC companyfacts", url, ok=cash is not None, detail=cash_detail, as_of=cash_as_of, basis="quarter_end", source_tier="official_filing"),
        obs("mstr_sec_diluted_shares_m", diluted / 1e6 if diluted is not None else None, "SEC companyfacts", url, ok=diluted is not None, detail=diluted_detail, as_of=diluted_as_of, basis="quarter_weighted_average", source_tier="official_filing"),
        obs("mstr_sec_stockholders_equity_musd", stockholders_equity / 1e6 if stockholders_equity is not None else None, "SEC companyfacts", url, ok=stockholders_equity is not None, detail=equity_detail, as_of=equity_as_of, basis="quarter_end", source_tier="official_filing"),
        obs("mstr_sec_preferred_dividends_musd", pref_div / 1e6 if pref_div is not None else None, "SEC companyfacts", url, ok=pref_div is not None, detail=pref_div_detail, as_of=pref_div_as_of, basis="reported_period", source_tier="official_filing"),
        obs("mstr_sec_preferred_cash_dividends_musd", pref_cash_div / 1e6 if pref_cash_div is not None else None, "SEC companyfacts", url, ok=pref_cash_div is not None, detail=pref_cash_div_detail, as_of=pref_cash_div_as_of, basis="reported_period", source_tier="official_filing"),
        obs("mstr_sec_deferred_tax_liability_musd", deferred_tax_liability / 1e6 if deferred_tax_liability is not None else None, "SEC companyfacts", url, ok=deferred_tax_liability is not None, detail=dtl_detail, as_of=dtl_as_of, basis="quarter_end", source_tier="official_filing"),
    ]


def collect_mstr_cover_shares() -> list[Observation]:
    cik = "0001050446"
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = fetch_json(submissions_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    recent = data.get("filings", {}).get("recent", {})
    filing = None
    for index, form in enumerate(recent.get("form", [])):
        if form not in {"10-Q", "10-K"}:
            continue
        filing = {
            "form": form,
            "filed": recent.get("filingDate", [])[index],
            "accession": recent.get("accessionNumber", [])[index],
            "document": recent.get("primaryDocument", [])[index],
        }
        break
    if not filing:
        return [obs("mstr_sec_common_shares_outstanding_m", None, "SEC filing cover", submissions_url, ok=False, detail="latest 10-Q/10-K missing")]
    accession_compact = filing["accession"].replace("-", "")
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_compact}/{filing['document']}"
    filing_html = fetch_text(filing_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "text/html"})
    matches = re.findall(
        r'<ix:nonfraction(?P<attrs>[^>]*\bname=["\']dei:EntityCommonStockSharesOutstanding["\'][^>]*)>(?P<body>.*?)</ix:nonfraction>',
        filing_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    values: list[float] = []
    context_ids: list[str] = []
    as_of_dates: list[str] = []
    for attrs, body in matches:
        raw_value = re.sub(r"<[^>]+>", "", html.unescape(body)).replace(",", "").strip()
        value = safe_float(raw_value)
        scale_match = re.search(r'\bscale=["\'](-?\d+)["\']', attrs, re.IGNORECASE)
        if value is None:
            continue
        if scale_match:
            value *= 10 ** int(scale_match.group(1))
        values.append(value)
        context_match = re.search(r'\bcontextref=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if context_match:
            context_id = context_match.group(1)
            context_ids.append(context_id)
            context_pattern = rf'<xbrli:context[^>]+id=["\']{re.escape(context_id)}["\'][^>]*>.*?<xbrli:instant>(\d{{4}}-\d{{2}}-\d{{2}})</xbrli:instant>.*?</xbrli:context>'
            context_date = re.search(context_pattern, filing_html, re.IGNORECASE | re.DOTALL)
            if context_date:
                as_of_dates.append(context_date.group(1))
    total_shares = sum(values) if values else None
    as_of = max(as_of_dates) if as_of_dates else filing["filed"]
    detail = (
        f"form={filing['form']} filed={filing['filed']} accn={filing['accession']} "
        f"classes={len(values)} contexts={','.join(context_ids)}"
    )
    return [
        obs(
            "mstr_sec_common_shares_outstanding_m",
            total_shares / 1e6 if total_shares is not None else None,
            "SEC filing cover inline XBRL",
            filing_url,
            ok=total_shares is not None,
            detail=detail,
            as_of=as_of,
            basis="point_in_time_common_shares_outstanding",
            source_tier="official_filing",
        )
    ]


def parse_press_release_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def collect_bmnr_sec_treasury() -> list[Observation]:
    cik = "0001829311"
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    submissions = fetch_json(submissions_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    recent = submissions.get("filings", {}).get("recent", {})
    latest_8k = None
    for index, form in enumerate(recent.get("form", [])):
        if form == "8-K":
            latest_8k = {
                "filing_date": recent.get("filingDate", [])[index],
                "accession": recent.get("accessionNumber", [])[index],
                "primary": recent.get("primaryDocument", [])[index],
            }
            break
    if not latest_8k:
        raise ValueError("BMNR latest 8-K not found")

    accession_compact = latest_8k["accession"].replace("-", "")
    archive_base = f"https://www.sec.gov/Archives/edgar/data/1829311/{accession_compact}"
    index_url = f"{archive_base}/index.json"
    index_data = fetch_json(index_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    files = [item.get("name", "") for item in index_data.get("directory", {}).get("item", [])]
    exhibit_name = next((name for name in files if re.fullmatch(r"ex99[^/]*\.htm", name, re.IGNORECASE)), latest_8k["primary"])
    exhibit_url = f"{archive_base}/{exhibit_name}"
    exhibit_html = fetch_text(exhibit_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "text/html"})
    plain = html.unescape(re.sub(r"<[^>]+>", " ", exhibit_html))
    plain = re.sub(r"\s+", " ", plain)

    def number(pattern: str) -> float | None:
        match = re.search(pattern, plain, flags=re.IGNORECASE)
        return safe_float(match.group(1).replace(",", "")) if match else None

    eth_holdings = number(r"comprised of\s+([\d,]+)\s+ETH")
    btc_holdings = number(r"([\d,]+)\s+Bitcoin\s*\(BTC\)")
    cash_market_musd = number(r"total cash\s*&\s*marketable securities of \$([\d,.]+)\s+million")
    beast_musd = number(r"\$([\d,.]+)\s+million stake in Beast Industries")
    eightco_musd = number(r"\$([\d,.]+)\s+million stake in Eightco")
    staked_eth = number(r"has\s+([\d,]+)\s+staked ETH")
    buyback_shares_m = number(r"repurchased approximately\s+([\d,.]+)\s+million shares")
    total_holdings_busd = number(r"holdings totaling \$([\d,.]+)\s+billion")
    as_of_match = re.search(r"As of ([A-Z][a-z]+ \d{1,2}, \d{4})", plain)
    holdings_as_of = parse_press_release_date(as_of_match.group(1) if as_of_match else None) or latest_8k["filing_date"]
    filing_detail = f"form=8-K filed={latest_8k['filing_date']} accn={latest_8k['accession']} exhibit={exhibit_name}"

    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    facts_payload = fetch_json(facts_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    shares, shares_detail, shares_as_of = latest_sec_fact(
        facts_payload.get("facts", {}),
        "EntityCommonStockSharesOutstanding",
        "shares",
        True,
        "dei",
    )
    return [
        obs("bmnr_eth_holdings", eth_holdings, "BMNR SEC 8-K exhibit", exhibit_url, ok=eth_holdings is not None, detail=filing_detail, as_of=holdings_as_of, basis="official_holdings", source_tier="official_filing"),
        obs("bmnr_btc_holdings", btc_holdings, "BMNR SEC 8-K exhibit", exhibit_url, ok=btc_holdings is not None, detail=filing_detail, as_of=holdings_as_of, basis="official_holdings", source_tier="official_filing"),
        obs("bmnr_cash_marketable_musd", cash_market_musd, "BMNR SEC 8-K exhibit", exhibit_url, ok=cash_market_musd is not None, detail=filing_detail, as_of=holdings_as_of, basis="official_holdings", source_tier="official_filing"),
        obs("bmnr_beast_stake_musd", beast_musd, "BMNR SEC 8-K exhibit", exhibit_url, ok=beast_musd is not None, detail=filing_detail, as_of=holdings_as_of, basis="management_mark", source_tier="official_filing"),
        obs("bmnr_eightco_stake_musd", eightco_musd, "BMNR SEC 8-K exhibit", exhibit_url, ok=eightco_musd is not None, detail=filing_detail, as_of=holdings_as_of, basis="management_mark", source_tier="official_filing"),
        obs("bmnr_staked_eth", staked_eth, "BMNR SEC 8-K exhibit", exhibit_url, ok=staked_eth is not None, detail=filing_detail, as_of=holdings_as_of, basis="official_holdings", source_tier="official_filing"),
        obs("bmnr_weekly_buyback_shares_m", buyback_shares_m, "BMNR SEC 8-K exhibit", exhibit_url, ok=buyback_shares_m is not None, detail=filing_detail, as_of=holdings_as_of, basis="reported_weekly_buyback", source_tier="official_filing"),
        obs("bmnr_reported_total_holdings_musd", total_holdings_busd * 1000 if total_holdings_busd is not None else None, "BMNR SEC 8-K exhibit", exhibit_url, ok=total_holdings_busd is not None, detail=filing_detail, as_of=holdings_as_of, basis="rounded_management_total", source_tier="official_filing"),
        obs("bmnr_sec_common_shares_m", shares / 1e6 if shares is not None else None, "SEC companyfacts", facts_url, ok=shares is not None, detail=shares_detail, as_of=shares_as_of, basis="point_in_time_shares", source_tier="official_filing"),
        obs("bmnr_latest_8k_date", latest_8k["filing_date"], "SEC submissions API", submissions_url, detail=filing_detail, as_of=latest_8k["filing_date"], basis="filing_date", source_tier="official_filing"),
    ]


def collect_sbet_sec_treasury() -> list[Observation]:
    cik = "0001981535"
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    submissions = fetch_json(submissions_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    recent = submissions.get("filings", {}).get("recent", {})
    candidates = []
    for form, filing_date, accession, primary in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form == "8-K" and all([filing_date, accession, primary]):
            candidates.append({"filing_date": filing_date, "accession": accession, "primary": primary})
        if len(candidates) >= 12:
            break

    for filing in candidates:
        accession_compact = filing["accession"].replace("-", "")
        archive_base = f"https://www.sec.gov/Archives/edgar/data/1981535/{accession_compact}"
        index_url = f"{archive_base}/index.json"
        index_data = fetch_json(index_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
        files = [item.get("name", "") for item in index_data.get("directory", {}).get("item", [])]
        exhibits = [name for name in files if re.fullmatch(r"ex99[^/]*\.htm", name, re.IGNORECASE)]
        for exhibit_name in exhibits or [filing["primary"]]:
            exhibit_url = f"{archive_base}/{exhibit_name}"
            exhibit_html = fetch_text(exhibit_url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "text/html"})
            plain = html.unescape(re.sub(r"<[^>]+>", " ", exhibit_html))
            plain = re.sub(r"\s+", " ", plain)
            components = re.search(
                r"Total ETH holdings held as of\s+([A-Z][a-z]+ \d{1,2}, \d{4}),?\s+were comprised of\s+"
                r"([\d,]+)\s+native ETH,\s+([\d,]+)\s+ETH as-if redeemed from LsETH\s+and\s+"
                r"([\d,]+)\s+ETH as-if redeemed from weETH",
                plain,
                flags=re.IGNORECASE,
            )
            if not components:
                continue
            holdings_as_of = parse_press_release_date(components.group(1)) or filing["filing_date"]
            native_eth = safe_float(components.group(2).replace(",", ""))
            lseth_equivalent = safe_float(components.group(3).replace(",", ""))
            weeth_equivalent = safe_float(components.group(4).replace(",", ""))
            if None in (native_eth, lseth_equivalent, weeth_equivalent):
                continue
            total_equivalent = native_eth + lseth_equivalent + weeth_equivalent
            headline = re.search(r"Total ETH holdings\s*\d*\s+increased to\s+([\d,]+)", plain, flags=re.IGNORECASE)
            headline_total = safe_float(headline.group(1).replace(",", "")) if headline else None
            if headline_total is not None and abs(headline_total - total_equivalent) > 1:
                raise ValueError("SBET SEC ETH component sum does not match headline total")
            detail = (
                f"form=8-K filed={filing['filing_date']} accn={filing['accession']} exhibit={exhibit_name} "
                f"native_eth={native_eth:.0f} lseth_eth_equivalent={lseth_equivalent:.0f} "
                f"weeth_eth_equivalent={weeth_equivalent:.0f}"
            )
            return [
                obs("sbet_eth_holdings_equivalent", total_equivalent, "SBET SEC 8-K exhibit", exhibit_url, detail=detail, as_of=holdings_as_of, basis="official_eth_equivalent_components", source_tier="official_filing"),
                obs("sbet_native_eth", native_eth, "SBET SEC 8-K exhibit", exhibit_url, detail=detail, as_of=holdings_as_of, basis="official_native_eth", source_tier="official_filing"),
                obs("sbet_lseth_eth_equivalent", lseth_equivalent, "SBET SEC 8-K exhibit", exhibit_url, detail=detail, as_of=holdings_as_of, basis="official_redemption_value_eth_equivalent", source_tier="official_filing"),
                obs("sbet_weeth_eth_equivalent", weeth_equivalent, "SBET SEC 8-K exhibit", exhibit_url, detail=detail, as_of=holdings_as_of, basis="official_redemption_value_eth_equivalent", source_tier="official_filing"),
            ]
        time.sleep(0.12)
    raise ValueError("No recent SBET 8-K exhibit contained a parseable ETH-equivalent holdings disclosure")

def collect_strategy_purchases() -> list[Observation]:
    url = "https://www.strategy.com/purchases"
    html = fetch_text(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
    if not match:
        raise ValueError("Strategy purchases __NEXT_DATA__ missing")
    data = json.loads(match.group(1))
    rows = data.get("props", {}).get("pageProps", {}).get("bitcoinData", [])
    if not rows:
        raise ValueError("Strategy purchases bitcoinData empty")
    latest = sorted(rows, key=lambda row: row.get("date_of_purchase") or "")[-1]
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=6)
    latest_date = datetime.fromisoformat(str(latest.get("date_of_purchase"))).date()
    window_is_covered = latest_date >= window_start
    rolling_rows = []
    for row in rows:
        try:
            row_date = datetime.fromisoformat(str(row.get("date_of_purchase"))).date()
        except ValueError:
            continue
        if window_start <= row_date <= today:
            rolling_rows.append(row)
    rolling_prices = [safe_float(row.get("total_purchase_price")) for row in rolling_rows]
    rolling_counts = [safe_float(row.get("count")) for row in rolling_rows]
    rolling_fields_complete = bool(
        window_is_covered
        and rolling_rows
        and all(value is not None for value in rolling_prices)
        and all(value is not None for value in rolling_counts)
    )
    rolling_sales_musd = sum(max(-value, 0) for value in rolling_prices if value is not None) / 1e6 if rolling_fields_complete else None
    rolling_purchases_musd = sum(max(value, 0) for value in rolling_prices if value is not None) / 1e6 if rolling_fields_complete else None
    rolling_net_btc = sum(value for value in rolling_counts if value is not None) if rolling_fields_complete else None
    detail = (
        f"date={latest.get('date_of_purchase')} title={latest.get('title')} "
        f"sec_url={(latest.get('sec') or {}).get('url')} source=strategy_purchases_next_data"
    )
    rolling_detail = (
        f"window={window_start.isoformat()}..{today.isoformat()} events={len(rolling_rows)} "
        f"latest_disclosure={latest_date.isoformat()} coverage={'covered_complete' if rolling_fields_complete else 'stale_or_incomplete_unknown_not_zero'} "
        "source=strategy_purchases_next_data"
    )
    return [
        obs("mstr_strategy_btc_holdings", safe_float(latest.get("btc_holdings")), "Strategy purchases page", url, ok=latest.get("btc_holdings") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_official_ledger", source_tier="official_company"),
        obs("mstr_strategy_latest_btc_delta", safe_float(latest.get("count")), "Strategy purchases page", url, ok=latest.get("count") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_event", source_tier="official_company"),
        obs("mstr_strategy_latest_purchase_price", safe_float(latest.get("purchase_price")), "Strategy purchases page", url, ok=latest.get("purchase_price") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_event", source_tier="official_company"),
        obs("mstr_strategy_latest_purchase_usd_m", (safe_float(latest.get("total_purchase_price")) or 0) / 1e6, "Strategy purchases page", url, ok=latest.get("total_purchase_price") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_event", source_tier="official_company"),
        obs("mstr_strategy_average_cost", safe_float(latest.get("average_price")), "Strategy purchases page", url, ok=latest.get("average_price") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_official_ledger", source_tier="official_company"),
        obs("mstr_strategy_latest_purchase_date", latest.get("date_of_purchase"), "Strategy purchases page", url, ok=bool(latest.get("date_of_purchase")), detail=detail, as_of=latest.get("date_of_purchase"), basis="latest_event", source_tier="official_company"),
        obs("mstr_strategy_basic_shares_outstanding_m", (safe_float(latest.get("basic_shares_outstanding")) or 0) / 1e6, "Strategy purchases page", url, ok=latest.get("basic_shares_outstanding") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="point_in_time_basic_shares_outstanding", source_tier="official_company"),
        obs("mstr_strategy_assumed_diluted_shares_m", (safe_float(latest.get("assumed_diluted_shares_outstanding")) or 0) / 1e6, "Strategy purchases page", url, ok=latest.get("assumed_diluted_shares_outstanding") is not None, detail=detail, as_of=latest.get("date_of_purchase"), basis="company_defined_assumed_diluted_shares", source_tier="official_company"),
        obs("mstr_strategy_rolling_7d_sales_musd", rolling_sales_musd, "Strategy purchases page", url, ok=rolling_sales_musd is not None, detail=rolling_detail, as_of=latest_date.isoformat(), basis="rolling_7d_reported_sales", source_tier="official_company_derived"),
        obs("mstr_strategy_rolling_7d_purchases_musd", rolling_purchases_musd, "Strategy purchases page", url, ok=rolling_purchases_musd is not None, detail=rolling_detail, as_of=latest_date.isoformat(), basis="rolling_7d_reported_purchases", source_tier="official_company_derived"),
        obs("mstr_strategy_rolling_7d_net_btc", rolling_net_btc, "Strategy purchases page", url, ok=rolling_net_btc is not None, detail=rolling_detail, as_of=latest_date.isoformat(), basis="rolling_7d_reported_net_change", source_tier="official_company_derived"),
    ]


class SecTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[str]] = []
        self.table_depth = 0
        self.current_table: list[str] = []
        self.cell_depth = 0
        self.current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self.table_depth == 0:
                self.current_table = []
            self.table_depth += 1
        elif tag in {"td", "th"} and self.table_depth:
            if self.cell_depth == 0:
                self.current_cell = []
            self.cell_depth += 1

    def handle_data(self, data: str) -> None:
        if self.cell_depth:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell_depth:
            self.cell_depth -= 1
            if self.cell_depth == 0:
                value = " ".join(" ".join(self.current_cell).split())
                if value:
                    self.current_table.append(value)
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                self.tables.append(self.current_table)


class SecTableMatrixParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.table_depth = 0
        self.row_depth = 0
        self.cell_depth = 0
        self.current_table: list[list[str]] = []
        self.current_row: list[str] = []
        self.current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self.table_depth == 0:
                self.current_table = []
            self.table_depth += 1
        elif tag == "tr" and self.table_depth:
            if self.row_depth == 0:
                self.current_row = []
            self.row_depth += 1
        elif tag in {"td", "th"} and self.table_depth:
            if self.cell_depth == 0:
                self.current_cell = []
            self.cell_depth += 1

    def handle_data(self, data: str) -> None:
        if self.cell_depth:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell_depth:
            self.cell_depth -= 1
            if self.cell_depth == 0:
                self.current_row.append(" ".join(" ".join(self.current_cell).split()))
        elif tag == "tr" and self.row_depth:
            self.row_depth -= 1
            if self.row_depth == 0 and any(self.current_row):
                self.current_table.append(self.current_row)
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                self.tables.append(self.current_table)


def parse_sec_date(value: str) -> str | None:
    match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def sec_table_numbers(row: list[str]) -> list[float]:
    values: list[float] = []
    for cell in row:
        stripped = cell.strip()
        negative = stripped.startswith("(") and stripped.endswith(")")
        match = re.fullmatch(r"[\$€]?\s*\(?\s*([\d,]+(?:\.\d+)?)\s*\)?", stripped)
        if match:
            value = float(match.group(1).replace(",", ""))
            values.append(-value if negative else value)
    return values


def parse_mstr_periodic_capital_structure(html_text: str, filing: dict[str, str]) -> dict[str, Any]:
    parser = SecTableMatrixParser()
    parser.feed(html_text)
    debt_principal_thousand = None
    other_debt_principal_thousand = None
    quarterly_interest_thousand = None
    deferred_tax_liability_thousand = None
    preferred: dict[str, dict[str, float]] = {}
    balance_as_of = None

    for table in parser.tables:
        flattened = " | ".join(cell for row in table for cell in row)
        if "Outstanding Principal Amount" in flattened and "Net Carrying Value" in flattened:
            total_row = next((row for row in table if row and row[0] == "Total"), None)
            numbers = sec_table_numbers(total_row or [])
            if numbers:
                debt_principal_thousand = numbers[0]
        if "Other long- term secured debt" in flattened and "Payments due by period" in flattened:
            total_row = next((row for row in table if row and row[0] == "Total"), None)
            numbers = sec_table_numbers(total_row or [])
            if len(numbers) >= 2:
                other_debt_principal_thousand = numbers[-2]
        if "Contractual Interest Expense" in flattened and "Amortization of Issuance Costs" in flattened:
            total_row = next((row for row in table if row and row[0] == "Total"), None)
            numbers = sec_table_numbers(total_row or [])
            if numbers:
                multiplier = 4 if "Three Months Ended" in flattened else 1
                quarterly_interest_thousand = numbers[0] * multiplier
        if "Deferred tax liabilities" in flattened and "Total liabilities" in flattened:
            dtl_row = next((row for row in table if row and row[0] == "Deferred tax liabilities"), None)
            numbers = sec_table_numbers(dtl_row or [])
            if numbers:
                deferred_tax_liability_thousand = numbers[0]
        if "Aggregate Liquidation Preference as of" in flattened and "Dividend Rate Per Annum" in flattened:
            symbols = [cell.split()[0] for cell in table[0] if re.fullmatch(r"STR[A-Z] Stock", cell)]
            notional_row = next((row for row in table if row and row[0].startswith("Aggregate Liquidation Preference as of")), None)
            rate_row = next((row for row in table if row and row[0].startswith("Dividend Rate Per Annum as of")), None)
            notionals = [value / 1000 for value in sec_table_numbers((notional_row or [])[1:])]
            rates = [float(match.group(1)) / 100 for cell in (rate_row or [])[1:] if (match := re.fullmatch(r"([\d.]+)\s*%", cell.strip()))]
            if len(symbols) == len(notionals) == len(rates):
                preferred = {symbol: {"notional_musd": notional, "rate": rate} for symbol, notional, rate in zip(symbols, notionals, rates)}
            date_match = re.search(r"as of ([A-Z][a-z]+ \d{1,2}, \d{4})", (notional_row or [""])[0])
            balance_as_of = parse_press_release_date(date_match.group(1)) if date_match else None

    plain_text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html_text)).split())
    service_match = re.search(r"\$([\d.]+) million due monthly in principal and interest related to our other long-term secured debt", plain_text, flags=re.IGNORECASE)
    other_debt_annual_service_musd = float(service_match.group(1)) * 12 if service_match else None
    if None in (debt_principal_thousand, other_debt_principal_thousand, quarterly_interest_thousand, deferred_tax_liability_thousand, other_debt_annual_service_musd) or not preferred or not balance_as_of:
        raise ValueError("Latest Strategy 10-Q/10-K capital-structure tables were incomplete")
    return {
        "filing": filing,
        "as_of": balance_as_of,
        "debt_face_musd": (debt_principal_thousand + other_debt_principal_thousand) / 1000,
        "annual_interest_musd": quarterly_interest_thousand / 1000,
        "other_debt_annual_service_musd": other_debt_annual_service_musd,
        "deferred_tax_liability_musd": deferred_tax_liability_thousand / 1000,
        "preferred": preferred,
    }


def parse_strategy_atm_periods(html_text: str) -> list[dict[str, Any]]:
    parser = SecTableMatrixParser()
    parser.feed(html_text)
    periods: list[dict[str, Any]] = []
    for table in parser.tables:
        flattened = " | ".join(cell for row in table for cell in row)
        if "Shares Sold" not in flattened or "During Period" not in flattened:
            continue
        period_match = re.search(r"During Period ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", flattened)
        if not period_match:
            continue
        shares_sold: dict[str, float] = {}
        for row in table:
            if not row or row[0] not in {"STRF Stock", "STRC Stock", "STRE Stock", "STRK Stock", "STRD Stock", "MSTR Stock"}:
                continue
            symbol = row[0].split()[0]
            shares = next((float(cell.replace(",", "")) for cell in row[1:] if re.fullmatch(r"[\d,]+", cell.strip())), 0.0)
            shares_sold[symbol] = shares
        periods.append({
            "start": parse_press_release_date(period_match.group(1)),
            "end": parse_press_release_date(period_match.group(2)),
            "shares_sold": shares_sold,
        })

    plain_text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html_text)).split())
    no_sales = re.search(
        r"during the period between ([A-Z][a-z]+ \d{1,2}, \d{4}) and ([A-Z][a-z]+ \d{1,2}, \d{4}), Strategy did not sell any shares",
        plain_text,
        flags=re.IGNORECASE,
    )
    if no_sales:
        periods.append({
            "start": parse_press_release_date(no_sales.group(1)),
            "end": parse_press_release_date(no_sales.group(2)),
            "shares_sold": {},
        })
    return periods


def parse_strategy_capital_update(html_text: str) -> dict[str, float]:
    plain_text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html_text)).split())
    result: dict[str, float] = {}
    rate_matches = re.findall(r"regular dividend rate per annum.*?(?:to|at)\s+([\d.]+)%", plain_text, flags=re.IGNORECASE)
    if rate_matches:
        result["strc_rate"] = float(rate_matches[-1]) / 100
    repurchase = re.search(r"repurchase of \$([\d.]+) billion aggregate principal amount of (?:its )?0% Convertible Senior Notes due 2029", plain_text, flags=re.IGNORECASE)
    if repurchase:
        result["zero_coupon_debt_repurchase_musd"] = float(repurchase.group(1)) * 1000
    debt_total = re.search(r"has \$([\d.]+) billion aggregate principal amount of convertible notes", plain_text, flags=re.IGNORECASE)
    if debt_total:
        result["official_convertible_debt_total_musd"] = float(debt_total.group(1)) * 1000
    preferred_total = re.search(r"(?:has|and) \$([\d.]+) billion aggregate notional amount of preferred stock outstanding", plain_text, flags=re.IGNORECASE)
    if preferred_total:
        result["official_preferred_total_musd"] = float(preferred_total.group(1)) * 1000
    return result


def collect_mstr_sec_capital_structure() -> list[Observation]:
    submissions_url = "https://data.sec.gov/submissions/CIK0001050446.json"
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    submissions = fetch_json(submissions_url, headers=headers)
    recent = submissions.get("filings", {}).get("recent", {})
    filings = [
        {"form": form, "filing_date": filing_date, "accession": accession, "document": document}
        for form, filing_date, accession, document in zip(
            recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", []), recent.get("primaryDocument", [])
        )
        if all([form, filing_date, accession, document])
    ]
    periodic = next((item for item in filings if item["form"] in {"10-Q", "10-K"}), None)
    if not periodic:
        raise ValueError("Latest Strategy 10-Q/10-K was not found")
    periodic_url = f"https://www.sec.gov/Archives/edgar/data/1050446/{periodic['accession'].replace('-', '')}/{periodic['document']}"
    periodic_html = fetch_text(periodic_url, headers={**headers, "Accept": "text/html"})
    base = parse_mstr_periodic_capital_structure(periodic_html, {**periodic, "url": periodic_url})
    base_date = datetime.fromisoformat(base["as_of"]).date()
    periods: dict[tuple[str, str], dict[str, Any]] = {}
    updates: list[tuple[str, dict[str, float]]] = []

    for filing in filings:
        if filing["form"] != "8-K" or filing["filing_date"] < base["as_of"]:
            continue
        filing_url = f"https://www.sec.gov/Archives/edgar/data/1050446/{filing['accession'].replace('-', '')}/{filing['document']}"
        filing_html = fetch_text(filing_url, headers={**headers, "Accept": "text/html"})
        for period in parse_strategy_atm_periods(filing_html):
            if period.get("start") and period.get("end"):
                periods[(period["start"], period["end"])] = period
        update = parse_strategy_capital_update(filing_html)
        if update:
            updates.append((filing["filing_date"], update))
        if "Capital Structure Update" in filing_html:
            base_url = filing_url.rsplit("/", 1)[0] + "/"
            exhibit_links = re.findall(r'href=["\']([^"\']*ex99[^"\']*)["\']', filing_html, flags=re.IGNORECASE)
            for link in exhibit_links[:2]:
                exhibit_html = fetch_text(urllib.parse.urljoin(base_url, html.unescape(link)), headers={**headers, "Accept": "text/html"})
                exhibit_update = parse_strategy_capital_update(exhibit_html)
                if exhibit_update:
                    updates.append((filing["filing_date"], exhibit_update))
        time.sleep(0.11)

    post_balance_periods = sorted(
        [period for period in periods.values() if period["start"] and datetime.fromisoformat(period["start"]).date() > base_date],
        key=lambda item: item["start"],
    )
    if not post_balance_periods:
        raise ValueError("No post-quarter Strategy ATM periods were available")
    expected = base_date + timedelta(days=1)
    for period in post_balance_periods:
        start = datetime.fromisoformat(period["start"]).date()
        end = datetime.fromisoformat(period["end"]).date()
        if start > expected:
            raise ValueError(f"Strategy ATM period gap {expected.isoformat()}..{(start - timedelta(days=1)).isoformat()}")
        if end >= expected:
            expected = end + timedelta(days=1)
    latest_period_end = max(period["end"] for period in post_balance_periods)
    preferred = copy.deepcopy(base["preferred"])
    for period in post_balance_periods:
        for symbol in preferred:
            shares_sold = safe_float(period.get("shares_sold", {}).get(symbol)) or 0
            if symbol == "STRE" and shares_sold:
                raise ValueError("STRE ATM issuance requires an official EUR/USD conversion contract")
            preferred[symbol]["notional_musd"] += shares_sold * 100 / 1e6

    purchases_url = "https://www.strategy.com/purchases"
    purchases_html = fetch_text(purchases_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    purchases_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', purchases_html)
    if not purchases_match:
        raise ValueError("Strategy purchases __NEXT_DATA__ missing for common-share roll-forward")
    purchase_rows = json.loads(purchases_match.group(1)).get("props", {}).get("pageProps", {}).get("bitcoinData", [])
    latest_purchase = max(purchase_rows, key=lambda row: row.get("date_of_purchase") or "", default={})
    common_baseline = safe_float(latest_purchase.get("basic_shares_outstanding"))
    common_baseline_date = str(latest_purchase.get("date_of_purchase") or "")
    if common_baseline is None or not common_baseline_date:
        raise ValueError("Strategy point-in-time basic shares missing")
    subsequent_common_issuance = sum(
        safe_float(period.get("shares_sold", {}).get("MSTR")) or 0
        for period in post_balance_periods
        if period["start"] >= common_baseline_date
    )
    current_common_shares_m = (common_baseline + subsequent_common_issuance) / 1e6

    latest_rate = next((update["strc_rate"] for _, update in sorted(updates, reverse=True) if update.get("strc_rate") is not None), None)
    if latest_rate is not None and "STRC" in preferred:
        preferred["STRC"]["rate"] = latest_rate
    repurchase = max((update.get("zero_coupon_debt_repurchase_musd", 0) for _, update in updates), default=0)
    debt_update_date = max(
        date for date, update in updates
        if update.get("zero_coupon_debt_repurchase_musd") is not None and update.get("official_convertible_debt_total_musd") is not None
    )
    official_debt = next((update["official_convertible_debt_total_musd"] for _, update in sorted(updates, reverse=True) if update.get("official_convertible_debt_total_musd") is not None), None)
    official_preferred = next((update["official_preferred_total_musd"] for _, update in sorted(updates, reverse=True) if update.get("official_preferred_total_musd") is not None), None)
    if repurchase <= 0 or official_debt is None or official_preferred is None:
        raise ValueError("Strategy capital update did not provide debt repurchase and aggregate cross-checks")
    debt_face_musd = base["debt_face_musd"] - repurchase
    reconstructed_preferred = sum(item["notional_musd"] for item in preferred.values())
    convertible_debt = debt_face_musd - 40.193
    debt_gap = relative_difference(convertible_debt, official_debt)
    preferred_gap = relative_difference(reconstructed_preferred, official_preferred)
    if debt_gap is None or debt_gap > 0.02 or preferred_gap is None or preferred_gap > 0.03:
        raise ValueError(f"Capital-structure reconstruction gap debt={debt_gap} preferred={preferred_gap}")
    detail = (
        f"base_accn={base['filing']['accession']} base_as_of={base['as_of']} atm_periods={len(post_balance_periods)} "
        f"latest_atm_period={latest_period_end} zero_coupon_repurchase_musd={repurchase:.3f} "
        f"official_debt_gap={debt_gap:.4%} class_to_official_preferred_gap={preferred_gap:.4%} "
        f"common_baseline={common_baseline:.0f}@{common_baseline_date} subsequent_common_issuance={subsequent_common_issuance:.0f}"
    )
    observations = [
        obs("mstr_sec_debt_face_musd", debt_face_musd, "SEC 10-Q plus subsequent 8-K capital updates", periodic_url, detail=detail, as_of=base["as_of"], basis="quarterly_face_debt_less_completed_zero_coupon_repurchase", source_tier="official_filing_derived"),
        obs("mstr_sec_annual_interest_musd", base["annual_interest_musd"], "SEC 10-Q contractual interest table", periodic_url, detail=detail, as_of=base["as_of"], basis="annualized_convertible_contractual_cash_interest", source_tier="official_filing_derived"),
        obs("mstr_sec_other_debt_annual_service_musd", base["other_debt_annual_service_musd"], "SEC 10-Q contractual obligations disclosure", periodic_url, detail=detail, as_of=base["as_of"], basis="monthly_principal_and_interest_times_twelve", source_tier="official_filing_derived"),
        obs("mstr_sec_balance_sheet_dtl_musd", base["deferred_tax_liability_musd"], "SEC 10-Q balance sheet", periodic_url, detail=detail, as_of=base["as_of"], basis="deferred_income_tax_liabilities_net", source_tier="official_filing"),
        obs("mstr_sec_atm_adjusted_common_shares_m", current_common_shares_m, "Strategy point-in-time basic shares plus subsequent SEC 8-K ATM ledger", purchases_url, detail=detail, as_of=latest_period_end, basis="official_basic_shares_plus_contiguous_subsequent_atm_issuance", source_tier="official_filing_derived"),
        obs("mstr_sec_preferred_notional_total_musd", reconstructed_preferred, "SEC 10-Q preferred table plus weekly 8-K ATM ledger", periodic_url, detail=detail, as_of=latest_period_end, basis="class_liquidation_preference_plus_contiguous_atm_issuance", source_tier="official_filing_derived"),
        obs("mstr_sec_preferred_official_aggregate_musd", official_preferred, "Strategy SEC 8-K capital-structure update plus subsequent no-issuance ledger", periodic_url, detail=detail, as_of=latest_period_end, basis="rounded_official_aggregate_carried_forward_by_contiguous_atm_ledger", source_tier="official_filing_derived"),
    ]
    for symbol, item in preferred.items():
        observations.extend([
            obs(f"mstr_sec_preferred_{symbol.lower()}_notional_musd", item["notional_musd"], "SEC 10-Q preferred table plus weekly 8-K ATM ledger", periodic_url, detail=detail, as_of=latest_period_end, basis="class_liquidation_preference_plus_contiguous_atm_issuance", source_tier="official_filing_derived"),
            obs(f"mstr_sec_preferred_{symbol.lower()}_rate", item["rate"], "SEC 10-Q and latest 8-K dividend declaration", periodic_url, detail=detail, as_of=latest_period_end, basis="latest_declared_annual_dividend_rate", source_tier="official_filing"),
        ])
    return observations


def integer_cells(cells: list[str]) -> list[float]:
    values: list[float] = []
    for cell in cells:
        match = re.fullmatch(r"([\d]{1,3}(?:,\d{3})+|\d+)(?:\s*\(\d+\))?", cell.strip())
        if match:
            values.append(float(match.group(1).replace(",", "")))
    return values


def parse_strategy_sec_btc_filing(html_text: str, filing: dict[str, str]) -> dict[str, Any] | None:
    parser = SecTableParser()
    parser.feed(html_text)
    holdings_tables = [table for table in parser.tables if any("Aggregate BTC Holdings" in cell for cell in table)]
    if not holdings_tables:
        return None
    holdings_table = holdings_tables[-1]
    holdings_values = integer_cells(holdings_table)
    if not holdings_values:
        return None
    holdings = holdings_values[-1]
    acquired = 0.0
    if any("BTC Acquired" in cell for cell in holdings_table) and len(holdings_values) >= 2:
        acquired = holdings_values[0]
    period_cell = next((cell for cell in holdings_table if cell.startswith("During Period ")), "")
    period_dates = [parse_sec_date(value) for value in re.findall(r"[A-Z][a-z]+ \d{1,2}, \d{4}", period_cell)]
    period_dates = [value for value in period_dates if value]
    period_start = period_dates[0] if len(period_dates) >= 2 else None
    period_end = period_dates[-1] if len(period_dates) >= 2 else None
    period_end = period_end or next((parse_sec_date(cell) for cell in reversed(holdings_table) if cell.startswith("As of ")), None)
    if not period_end:
        period_end = filing["filing_date"]
    direct_sales: list[float] = []
    for table in parser.tables:
        if not any("BTC Sold" in cell for cell in table):
            continue
        sale_price_index = next((index for index, cell in enumerate(table) if "Aggregate Sale Price" in cell and "millions" in cell), None)
        if sale_price_index is None:
            continue
        for cell in table[sale_price_index + 1:]:
            match = re.fullmatch(r"\$([\d,]+(?:\.\d+)?)", cell.strip())
            if match:
                direct_sales.append(float(match.group(1).replace(",", "")))
                break
    plain_text = html.unescape(re.sub(r"<[^>]+>", " ", html_text))
    plain_text = re.sub(r"\s+", " ", plain_text)
    reserve_match = re.search(
        r'balance of the USD Reserve (?:is|was) \$([\d,.]+)\s*(billion|million)',
        plain_text,
        flags=re.IGNORECASE,
    )
    reserve_musd = None
    if reserve_match:
        reserve_musd = float(reserve_match.group(1).replace(",", "")) * (1000 if reserve_match.group(2).lower() == "billion" else 1)
    atm_net_proceeds_musd = None
    for table in parser.tables:
        if not any("Net Proceeds (in millions)" in cell for cell in table) or "Total" not in table:
            continue
        total_index = len(table) - 1 - table[::-1].index("Total")
        for cell in table[total_index + 1:]:
            match = re.fullmatch(r"([\d,]+(?:\.\d+)?)", cell.strip())
            if match:
                atm_net_proceeds_musd = float(match.group(1).replace(",", ""))
                break
        if atm_net_proceeds_musd is not None:
            break
    return {
        **filing,
        "holdings": holdings,
        "acquired": acquired,
        "period_start": period_start,
        "period_end": period_end,
        "direct_sales_musd": sum(direct_sales) if direct_sales else None,
        "usd_reserve_musd": reserve_musd,
        "atm_net_proceeds_musd": atm_net_proceeds_musd,
        "explicit_no_purchases": "No bitcoin purchases were made this week" in plain_text,
    }


def collect_strategy_sec_btc_updates() -> list[Observation]:
    submissions_url = "https://data.sec.gov/submissions/CIK0001050446.json"
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    data = fetch_json(submissions_url, headers=headers)
    recent = data.get("filings", {}).get("recent", {})
    records: list[dict[str, str]] = []
    for form, filing_date, accession, primary in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form != "8-K" or not all([filing_date, accession, primary]):
            continue
        archive_accession = accession.replace("-", "")
        records.append({
            "filing_date": filing_date,
            "accession": accession,
            "url": f"https://www.sec.gov/Archives/edgar/data/1050446/{archive_accession}/{primary}",
        })
        if len(records) >= 8:
            break
    parsed: list[dict[str, Any]] = []
    for record in records:
        filing_html = fetch_text(record["url"], headers=headers)
        btc_update = parse_strategy_sec_btc_filing(filing_html, record)
        if btc_update:
            parsed.append(btc_update)
        if len(parsed) >= 2:
            break
        time.sleep(0.15)
    if len(parsed) < 2:
        raise ValueError("Fewer than two consecutive Strategy SEC BTC updates were parseable")
    current, previous = parsed[0], parsed[1]
    inferred_sold_btc = previous["holdings"] + current["acquired"] - current["holdings"]
    if inferred_sold_btc < 0:
        raise ValueError("Strategy SEC BTC holdings reconciliation produced negative inferred sales")
    sales_musd = current["direct_sales_musd"]
    sale_basis = "direct_reported_sale_proceeds"
    contiguous_period = False
    if current.get("period_start") and previous.get("period_end"):
        contiguous_period = (
            datetime.fromisoformat(current["period_start"]).date()
            - datetime.fromisoformat(previous["period_end"]).date()
        ).days == 1
    complete_week = False
    if current.get("period_start") and current.get("period_end"):
        complete_week = (
            datetime.fromisoformat(current["period_end"]).date()
            - datetime.fromisoformat(current["period_start"]).date()
        ).days == 6
    reconciled_zero = bool(
        sales_musd is None
        and inferred_sold_btc == 0
        and current["acquired"] == 0
        and current.get("explicit_no_purchases")
        and contiguous_period
        and complete_week
    )
    if reconciled_zero:
        sales_musd = 0.0
        sale_basis = "complete_week_two_filing_reported_sales_reconciliation_zero"
    reserve_gross = current.get("usd_reserve_musd")
    atm_net_proceeds = current.get("atm_net_proceeds_musd")
    reserve_settled_floor = (
        max(reserve_gross - atm_net_proceeds, 0)
        if reserve_gross is not None and atm_net_proceeds is not None
        else None
    )
    detail = (
        f"current_accn={current['accession']} previous_accn={previous['accession']} "
        f"period={current.get('period_start')}..{current.get('period_end')} contiguous={contiguous_period} complete_week={complete_week} "
        f"previous_holdings={previous['holdings']:.0f} acquired={current['acquired']:.0f} "
        f"current_holdings={current['holdings']:.0f} inferred_sold_btc={inferred_sold_btc:.0f} "
        f"reported_sales_musd={sales_musd} sale_basis={sale_basis}"
    )
    return [
        obs("mstr_sec_btc_holdings_latest", current["holdings"], "Strategy SEC 8-K BTC update", current["url"], detail=detail, as_of=current["period_end"], basis="official_weekly_holdings", source_tier="official_filing"),
        obs("mstr_sec_rolling_7d_sales_musd", sales_musd, "Strategy SEC 8-K BTC update", current["url"], ok=sales_musd is not None, detail=detail, as_of=current["period_end"], basis=sale_basis, source_tier="official_filing_derived"),
        obs("mstr_sec_rolling_7d_acquired_btc", current["acquired"], "Strategy SEC 8-K BTC update", current["url"], detail=detail, as_of=current["period_end"], basis="official_weekly_acquisition", source_tier="official_filing"),
        obs("mstr_sec_usd_reserve_gross_musd", reserve_gross, "Strategy SEC 8-K USD Reserve update", current["url"], ok=reserve_gross is not None, detail=f"current_accn={current['accession']} includes_expected_unsettled_atm=true", as_of=current["period_end"], basis="official_gross_usd_reserve_including_unsettled_atm", source_tier="official_filing"),
        obs("mstr_sec_usd_reserve_settled_floor_musd", reserve_settled_floor, "Strategy SEC 8-K USD Reserve conservative floor", current["url"], ok=reserve_settled_floor is not None, detail=f"gross_reserve_musd={reserve_gross} less_full_period_atm_net_proceeds_musd={atm_net_proceeds} conservative_floor=true", as_of=current["period_end"], basis="gross_reserve_less_all_period_atm_proceeds", source_tier="official_filing_derived"),
    ]

def collect_sec_submissions() -> list[Observation]:
    cik = "0001050446"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accession = recent.get("accessionNumber", [])
    latest = {
        "form": forms[0] if forms else None,
        "filingDate": dates[0] if dates else None,
        "accessionNumber": accession[0] if accession else None,
    }
    return [
        obs("mstr_sec_latest_form", latest["form"], "SEC submissions API", url, ok=bool(latest["form"])),
        obs("mstr_sec_latest_filing_date", latest["filingDate"], "SEC submissions API", url, ok=bool(latest["filingDate"])),
        obs("mstr_sec_latest_accession", latest["accessionNumber"], "SEC submissions API", url, ok=bool(latest["accessionNumber"])),
    ]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_observation(observations: list[Observation], name: str) -> Observation | None:
    for item in observations:
        if item.name == name and item.ok:
            return item
    return None


def latest_value(observations: list[Observation], name: str) -> float | str | None:
    item = latest_observation(observations, name)
    return item.value if item else None


def selected_price(
    observations: list[Observation],
    primary_name: str,
    fallback_name: str,
    label: str,
) -> tuple[float | None, dict[str, Any]]:
    primary = latest_observation(observations, primary_name)
    fallback = latest_observation(observations, fallback_name)
    selected = primary or fallback
    value = safe_float(selected.value) if selected else None
    return value, {
        "selected_source": selected.source if selected else None,
        "selected_observation": selected.name if selected else None,
        "selected_detail": selected.detail if selected else None,
        "policy": f"{label}: 優先使用 Yahoo regular-market close 作為每日收盤基準；Nasdaq 僅作備援與盤前/盤後新鮮度檢查",
        "fallback_source": fallback.source if fallback else None,
        "fallback_observation": fallback.name if fallback else None,
        "fallback_detail": fallback.detail if fallback else None,
    }


def verified_spot_price(
    observations: list[Observation],
    observation_names: list[str],
    label: str,
) -> tuple[float | None, dict[str, Any]]:
    available: list[tuple[Observation, float]] = []
    for name in observation_names:
        item = latest_observation(observations, name)
        value = safe_float(item.value) if item else None
        if item and value is not None and value > 0:
            available.append((item, value))
    values = [value for _, value in available]
    verified = len(values) >= 2
    gap = (max(values) - min(values)) / statistics.mean(values) if len(values) >= 2 else None
    providers = [item.source for item, _ in available]
    return statistics.median(values) if verified else None, {
        "selected_source": "跨來源中位數" if verified else None,
        "selected_observations": [item.name for item, _ in available],
        "providers_used": providers,
        "source_count": len(values),
        "required_source_count": 2,
        "cross_source_gap": gap,
        "max_cross_source_gap": 0.015,
        "policy": f"{label} 從可用來源池取至少兩個新鮮報價的中位數；任何指定供應商失敗均可替換，價差超過 1.5% 由 verifier 封鎖",
    }


def set_automated_input(
    inputs: dict[str, Any],
    provenance: dict[str, Any],
    key: str,
    observation: Observation | None,
    source_ref: str,
    source_type: str,
    confidence: str = "high",
    allow_none: bool = False,
) -> None:
    value = safe_float(observation.value) if observation else None
    if value is None and not allow_none:
        return
    if observation is None:
        inputs[key] = None
        provenance.setdefault("fields", {})[key] = {
            "source_type": "missing_required",
            "source_ref": source_ref,
            "detail": "No valid source observation was available; unknown is preserved and decision gates must fail closed",
            "as_of": None,
            "fetched_at": now_iso(),
            "basis": "unknown_not_zero",
            "source_tier": "missing",
            "confidence": "none",
        }
        return
    inputs[key] = value
    provenance.setdefault("fields", {})[key] = {
        "source_type": source_type,
        "source_ref": source_ref,
        "detail": observation.detail,
        "as_of": observation.as_of,
        "fetched_at": observation.fetched_at,
        "basis": observation.basis,
        "source_tier": observation.source_tier,
        "confidence": confidence,
    }


def build_effective_inputs(observations: list[Observation]) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = copy.deepcopy(MANUAL_INPUTS)
    provenance = copy.deepcopy(load_input_provenance())
    provenance["status"] = "mixed_automated_manual"
    provenance["updated_at"] = today_utc()
    sec_cash = latest_observation(observations, "mstr_sec_cash_musd")
    sec_weekly_reserve = latest_observation(observations, "mstr_sec_usd_reserve_settled_floor_musd")
    sec_weekly_reserve_gross = latest_observation(observations, "mstr_sec_usd_reserve_gross_musd")
    sec_common_shares = latest_observation(observations, "mstr_sec_common_shares_outstanding_m")
    atm_adjusted_common_shares = latest_observation(observations, "mstr_sec_atm_adjusted_common_shares_m")
    strategy_basic_shares = latest_observation(observations, "mstr_strategy_basic_shares_outstanding_m")
    sec_diluted = latest_observation(observations, "mstr_sec_diluted_shares_m")
    sec_dtl = latest_observation(observations, "mstr_sec_deferred_tax_liability_musd") or latest_observation(observations, "mstr_sec_balance_sheet_dtl_musd")
    strategy_btc = latest_observation(observations, "mstr_sec_btc_holdings_latest") or latest_observation(observations, "mstr_strategy_btc_holdings")
    strategy_weekly_sales = latest_observation(observations, "mstr_sec_rolling_7d_sales_musd") or latest_observation(observations, "mstr_strategy_rolling_7d_sales_musd")
    reserve_observation = sec_weekly_reserve or sec_weekly_reserve_gross or sec_cash
    reserve_confidence = "high" if sec_weekly_reserve else "medium" if sec_weekly_reserve_gross else "low"
    set_automated_input(inputs, provenance, "usd_reserve_musd", reserve_observation, "Strategy SEC 8-K conservative floor; gross disclosed reserve fallback; SEC companyfacts last resort", "official_filing_derived", reserve_confidence)
    set_automated_input(inputs, provenance, "common_shares_outstanding_m", atm_adjusted_common_shares or strategy_basic_shares or sec_common_shares, "Strategy official point-in-time basic shares plus subsequent SEC ATM issuance; filing cover fallback", "official_filing_derived")
    set_automated_input(inputs, provenance, "diluted_shares_m", sec_diluted, "SEC companyfacts WeightedAverageNumberOfDilutedSharesOutstanding", "official_filing_structured", "medium")
    set_automated_input(inputs, provenance, "deferred_tax_liability_musd", sec_dtl, "SEC DeferredIncomeTaxLiabilitiesNet; latest balance-sheet parser fallback", "official_filing_structured")
    set_automated_input(inputs, provenance, "mstr_btc_holdings", strategy_btc, "Strategy SEC 8-K BTC update; official purchases page fallback", "official_filing_disclosure")
    sales_confidence = "medium" if strategy_weekly_sales and "reconciliation_zero" in str(strategy_weekly_sales.basis) else "high"
    set_automated_input(inputs, provenance, "weekly_btc_sales_musd", strategy_weekly_sales, "Strategy SEC 8-K complete-week reported-sales reconciliation; official purchases ledger fallback", "official_filing_derived", sales_confidence, allow_none=True)
    set_automated_input(inputs, provenance, "debt_face_musd", latest_observation(observations, "mstr_sec_debt_face_musd"), "SEC 10-Q face debt less completed subsequent repurchases", "official_filing_derived")
    set_automated_input(inputs, provenance, "annual_interest_musd", latest_observation(observations, "mstr_sec_annual_interest_musd"), "SEC 10-Q contractual interest expense annualized", "official_filing_derived")
    set_automated_input(inputs, provenance, "other_debt_annual_service_musd", latest_observation(observations, "mstr_sec_other_debt_annual_service_musd"), "SEC 10-Q monthly principal and interest obligation annualized", "official_filing_derived")
    set_automated_input(inputs, provenance, "preferred_aggregate_musd", latest_observation(observations, "mstr_sec_preferred_official_aggregate_musd"), "Strategy official aggregate preferred notional carried forward only across contiguous no-issuance periods", "official_filing_derived", "medium")
    preferred: dict[str, dict[str, float]] = {}
    preferred_observations: list[Observation] = []
    for symbol in ("STRF", "STRC", "STRE", "STRK", "STRD"):
        notional = latest_observation(observations, f"mstr_sec_preferred_{symbol.lower()}_notional_musd")
        rate = latest_observation(observations, f"mstr_sec_preferred_{symbol.lower()}_rate")
        notional_value = safe_float(notional.value) if notional else None
        rate_value = safe_float(rate.value) if rate else None
        if None in (notional_value, rate_value):
            preferred = {}
            break
        preferred[symbol] = {"notional_musd": notional_value, "rate": rate_value}
        preferred_observations.extend([notional, rate])
    if preferred and preferred_observations:
        inputs["preferred"] = preferred
        latest_preferred = max(preferred_observations, key=lambda item: item.as_of or "")
        provenance.setdefault("fields", {})["preferred"] = {
            "source_type": "official_filing_derived",
            "source_ref": "SEC 10-Q class liquidation preferences plus contiguous weekly 8-K ATM ledger and latest dividend declaration",
            "detail": latest_preferred.detail,
            "as_of": latest_preferred.as_of,
            "fetched_at": latest_preferred.fetched_at,
            "basis": "class_liquidation_preference_plus_contiguous_atm_issuance",
            "source_tier": "official_filing_derived",
            "confidence": "medium",
        }
    provenance.setdefault("fields", {})["cash_other_musd"] = {
        "source_type": "policy_assumption",
        "source_ref": "Conservative valuation policy",
        "detail": "Unverified other assets receive zero value until a reviewed filing parser is available",
        "as_of": today_utc(),
        "fetched_at": now_iso(),
        "basis": "conservative_zero",
        "confidence": "high",
    }
    database = load_json(DATABASE_PATH, {"snapshots": []})
    prior = sorted(
        [item for item in database.get("snapshots", []) if str(item.get("date", "")) < today_utc()],
        key=lambda item: item.get("date", ""),
    )
    if prior:
        previous = prior[-1]
        previous_date = previous.get("date")
        previous_inputs = previous.get("metrics", {}).get("manual_inputs", {})
        previous_metrics = previous.get("metrics", {}).get("mstr_metrics", {})
        previous_pref_total = safe_float(previous_inputs.get("preferred_aggregate_musd")) or sum(
            safe_float(item.get("notional_musd")) or 0
            for item in previous_inputs.get("preferred", {}).values()
        )
        previous_pref_obs = obs(
            "previous_preferred_notional_musd",
            previous_pref_total,
            "Prior daily snapshot",
            str(DATABASE_PATH),
            as_of=previous_date,
            basis="prior_snapshot",
            source_tier="internal_derived",
        )
        previous_mnav_obs = obs(
            "previous_equity_mnav",
            previous_metrics.get("equity_mnav"),
            "Prior daily snapshot",
            str(DATABASE_PATH),
            ok=previous_metrics.get("equity_mnav") is not None,
            as_of=previous_date,
            basis="prior_snapshot",
            source_tier="internal_derived",
        )
        set_automated_input(inputs, provenance, "prev_pref_notional_musd", previous_pref_obs, "Prior daily snapshot preferred total", "derived_prior_snapshot")
        set_automated_input(inputs, provenance, "prev_mnav_equity", previous_mnav_obs, "Prior daily snapshot equity mNAV", "derived_prior_snapshot")
    risk_keys = ("mstr_btc_holdings", "usd_reserve_musd", "debt_face_musd", "annual_interest_musd", "other_debt_annual_service_musd", "preferred", "preferred_aggregate_musd", "common_shares_outstanding_m", "weekly_btc_sales_musd", "deferred_tax_liability_musd")
    field_types = [provenance.get("fields", {}).get(key, {}).get("source_type") for key in risk_keys]
    provenance["status"] = "automated" if all(source_type not in {None, "manual", "missing_required"} for source_type in field_types) else "mixed_automated_manual"
    return inputs, provenance


def load_input_provenance() -> dict[str, Any]:
    return load_json(PROVENANCE_PATH, {"schema": 1, "status": "missing", "fields": {}})


def score_between(value: float | None, cold: float, hot: float, invert: bool = False) -> float:
    if value is None:
        return 0.0
    midpoint = (cold + hot) / 2
    half_range = (hot - cold) / 2
    score = max(-2.0, min(2.0, (value - midpoint) / half_range * 2))
    return round(-score if invert else score, 1)


def build_btc_standards(metrics: dict[str, Any]) -> dict[str, Any]:
    prices = metrics.get("prices", {})
    radar = metrics.get("market_radar", {})
    mstr = metrics.get("mstr_metrics", {})
    btc = safe_float(prices.get("btc_usd"))
    ma200 = safe_float(radar.get("btc_200dma"))
    ma50 = safe_float(radar.get("btc_50dma"))
    mvrv = safe_float(radar.get("btc_mvrv_current"))
    fear_greed = safe_float(radar.get("fear_greed"))
    etf_7d = safe_float(radar.get("etf_flow_7d_usd"))
    dd_1y = safe_float(radar.get("btc_drawdown_1y_pct"))
    ret_30d = safe_float(radar.get("btc_return_30d_pct"))
    treasury_rate = safe_float(radar.get("treasury_avg_bill_rate_pct"))
    sale_ratio = safe_float(mstr.get("sale_ratio"))
    strc_discount = safe_float(mstr.get("strc_discount"))
    coverage_months = safe_float(mstr.get("coverage_months"))
    trend_vs_200dma = btc / ma200 - 1 if btc is not None and ma200 else None
    trend_vs_50dma = btc / ma50 - 1 if btc is not None and ma50 else None
    dimensions = {
        "估值便宜度": score_between(mvrv, 1.0, 2.2),
        "價格趨勢": score_between(trend_vs_200dma, -0.15, 0.15),
        "市場情緒": score_between(fear_greed, 25, 75),
        "ETF 邊際買盤": score_between(etf_7d, -500_000_000, 500_000_000),
        "週期回撤": score_between(dd_1y, -0.45, -0.10),
    }
    dimension_inputs = {
        "估值便宜度": mvrv,
        "價格趨勢": trend_vs_200dma,
        "市場情緒": fear_greed,
        "ETF 邊際買盤": etf_7d,
        "週期回撤": dd_1y,
    }
    weights = {
        "估值便宜度": 1.25,
        "價格趨勢": 1.0,
        "市場情緒": 0.75,
        "ETF 邊際買盤": 0.5,
        "週期回撤": 1.0,
    }
    missing_dimensions = [name for name, value in dimension_inputs.items() if value is None]
    available_weight = sum(weights[name] for name in dimensions if name not in missing_dimensions)
    weighted_score = sum(dimensions[name] * weights[name] for name in dimensions if name not in missing_dimensions)
    score = round(weighted_score / (2 * available_weight) * 10, 1) if available_weight else None
    coverage_ratio = (len(dimensions) - len(missing_dimensions)) / len(dimensions)
    capitulation_conditions = [
        btc is not None and btc <= 54_000,
        mvrv is not None and mvrv <= 1.0,
        fear_greed is not None and fear_greed <= 15,
        dd_1y is not None and dd_1y <= -0.45,
        ret_30d is not None and ret_30d <= -0.20,
    ]
    confirmation_conditions = [
        trend_vs_200dma is not None and trend_vs_200dma >= 0,
        trend_vs_50dma is not None and trend_vs_50dma >= 0,
        mvrv is not None and 1.0 < mvrv <= 1.5,
        fear_greed is not None and 25 <= fear_greed <= 65,
    ]
    capitulation_hits = sum(1 for item in capitulation_conditions if item)
    confirmation_hits = sum(1 for item in confirmation_conditions if item)
    if coverage_ratio < 0.8 or score is None:
        regime = "資料不足觀察區"
        action = "資料覆蓋不足；不做底部或追高判斷"
        tone = "data_limited"
    elif capitulation_hits >= 2 and score <= -6:
        regime = "投降接刀區"
        action = "只允許現貨分批；MSTR 合約仍需等待資本結構與右側確認"
        tone = "deep_value"
    elif confirmation_hits >= 4 and -4 <= score <= 3:
        regime = "便宜後右側確認區"
        action = "可研究大倉現貨加碼；合約仍需 MSTR 紅燈解除"
        tone = "constructive"
    elif score >= 6:
        regime = "偏熱追高區"
        action = "不追價；只檢查減碼與風險上限"
        tone = "overheated"
    elif score <= -3:
        regime = "偏冷等待區"
        action = "準備買單但等待投降或右側確認，不用預設今日是底"
        tone = "cold_watch"
    else:
        regime = "中性拉扯區"
        action = "保持觀察；避免用單一指標判斷 BTC 底部"
        tone = "neutral"
    return {
        "schema": 1,
        "model_id": "btc-regime-five-dimension",
        "model_version": "2.0.0",
        "formula_version": "weighted-normalized-v1",
        "calibrated": False,
        "score_comparable_from": "2026-07-21",
        "model_status": "heuristic_unbacktested",
        "intended_horizon": "weekly_to_monthly_regime_context",
        "score": score,
        "regime": regime,
        "tone": tone,
        "action": action,
        "one_line": f"BTC：{regime}｜{action}",
        "dimensions": dimensions,
        "dimension_weights": weights,
        "weighted_score_before_normalization": weighted_score,
        "data_quality": {
            "coverage_ratio": coverage_ratio,
            "missing_dimensions": missing_dimensions,
            "etf_flow_weight_capped": True,
            "etf_flow_counts_as_confirmation": False,
            "etf_flow_reason": "多源與官方主要基金持倉已交叉驗證；仍固定為 0.5，避免單一資金流維度直接放行交易",
        },
        "signals": {
            "btc_usd": btc,
            "btc_vs_200dma_pct": trend_vs_200dma,
            "btc_vs_50dma_pct": trend_vs_50dma,
            "btc_1y_drawdown_pct": dd_1y,
            "btc_30d_return_pct": ret_30d,
            "mvrv_ratio": mvrv,
            "fear_greed": fear_greed,
            "etf_flow_7d_usd": etf_7d,
        },
        "implementation_overlays": {
            "macro_liquidity": {
                "status": "restrictive" if treasury_rate is not None and treasury_rate > 4.5 else "neutral",
                "treasury_rate_pct": treasury_rate,
                "read": "無風險利率高於 4.5%，降低估值容忍度" if treasury_rate is not None and treasury_rate > 4.5 else "利率未觸發額外估值降權",
            },
            "mstr_vehicle": {
                "status": "blocked" if mstr.get("contract_red_light") else "watch",
                "sale_pressure_ratio": sale_ratio,
                "cash_coverage_months": coverage_months,
                "strc_discount": strc_discount,
                "read": "BTC 狀態不等於 MSTR 合約放行；載具紅燈獨立判斷",
            },
        },
        "thresholds": {
            "投降接刀區": "至少 2 個投降條件且標準分 ≤ -6；現貨分批，不自動開 MSTR 合約",
            "便宜後右側確認區": "至少 4 個右側確認條件且標準分 -4 到 +3；可研究現貨加碼",
            "偏熱追高區": "標準分 ≥ +6；禁止追價",
            "偏冷等待區": "總分 ≤ -3 但投降不足；準備但不預設見底",
        },
        "limits": [
            "MVRV-Z、realized loss、Google Trends 無穩定免費官方 API 時不作硬觸發",
            "ETF flow 已多源驗證，但資金流本身仍不可單獨放行交易",
            "BTC 判斷標準只決定現貨節奏；MSTR 合約需另過資本結構紅燈",
        ],
    }


def compute_metrics(observations: list[Observation]) -> dict[str, Any]:
    btc_px, btc_basis = verified_spot_price(
        observations,
        ["btc_usd_coingecko", "btc_usd_coinbase", "btc_usd_kraken"],
        "BTC",
    )
    eth_px, eth_basis = verified_spot_price(
        observations,
        ["eth_usd_coingecko", "eth_usd_coinbase", "eth_usd_kraken"],
        "ETH",
    )
    mstr_px, mstr_basis = selected_price(observations, "mstr_usd_yahoo", "mstr_usd_nasdaq", "MSTR")
    bmnr_px, bmnr_basis = selected_price(observations, "bmnr_usd_yahoo", "bmnr_usd_nasdaq", "BMNR")
    strc_px, strc_basis = selected_price(observations, "strc_usd_yahoo", "strc_usd_nasdaq", "STRC")

    inputs, input_provenance = build_effective_inputs(observations)
    preferred_class_total = sum(item["notional_musd"] for item in inputs["preferred"].values())
    pref_total = max(preferred_class_total, safe_float(inputs.get("preferred_aggregate_musd")) or 0)
    maximum_preferred_rate = max(item["rate"] for item in inputs["preferred"].values())
    annual_div = sum(item["notional_musd"] * item["rate"] for item in inputs["preferred"].values()) + max(pref_total - preferred_class_total, 0) * maximum_preferred_rate
    annual_obligation = annual_div + inputs["annual_interest_musd"] + inputs["other_debt_annual_service_musd"]
    coverage_months = inputs["usd_reserve_musd"] / (annual_obligation / 12)
    weekly_need = annual_obligation / 52
    weekly_sales = safe_float(inputs.get("weekly_btc_sales_musd"))
    sale_ratio = weekly_sales / weekly_need if weekly_sales is not None and weekly_need else None
    common_shares = inputs["common_shares_outstanding_m"]
    sats_per_share = inputs["mstr_btc_holdings"] * 1e8 / (common_shares * 1e6)

    btc_nav_musd = None
    equity_mnav = None
    enterprise_mnav = None
    pref_dilution_flag = False
    if btc_px and mstr_px:
        btc_nav_musd = inputs["mstr_btc_holdings"] * btc_px / 1e6
        mkt_cap_musd = common_shares * mstr_px
        net_to_common = btc_nav_musd + inputs["usd_reserve_musd"] + inputs["cash_other_musd"] - inputs["debt_face_musd"] - pref_total - inputs["deferred_tax_liability_musd"]
        equity_mnav = mkt_cap_musd / net_to_common if net_to_common > 0 else None
        enterprise_mnav = (mkt_cap_musd + inputs["debt_face_musd"] + pref_total - inputs["usd_reserve_musd"] - inputs["cash_other_musd"]) / btc_nav_musd
        pref_dilution_flag = pref_total > inputs["prev_pref_notional_musd"] and bool(equity_mnav and equity_mnav > inputs["prev_mnav_equity"])

    strc_discount = 1 - strc_px / 100 if strc_px else None
    common_valuation_gate_ok = bool(equity_mnav and equity_mnav <= 1 and not pref_dilution_flag)
    capital_flywheel_gate_ok = bool(equity_mnav and enterprise_mnav and equity_mnav >= 1 and enterprise_mnav >= 1 and not pref_dilution_flag)
    contract_red_light = bool(sale_ratio is None or sale_ratio > 2 or coverage_months < 12 or (strc_discount is None or strc_discount > 0.05))

    bmnr_eth = safe_float(latest_value(observations, "bmnr_eth_holdings"))
    bmnr_btc = safe_float(latest_value(observations, "bmnr_btc_holdings"))
    bmnr_cash = safe_float(latest_value(observations, "bmnr_cash_marketable_musd"))
    bmnr_beast = safe_float(latest_value(observations, "bmnr_beast_stake_musd"))
    bmnr_eightco = safe_float(latest_value(observations, "bmnr_eightco_stake_musd"))
    bmnr_staked_eth = safe_float(latest_value(observations, "bmnr_staked_eth"))
    bmnr_reported_total = safe_float(latest_value(observations, "bmnr_reported_total_holdings_musd"))
    bmnr_shares_obs = latest_observation(observations, "bmnr_sec_common_shares_m")
    bmnr_buyback_obs = latest_observation(observations, "bmnr_weekly_buyback_shares_m")
    bmnr_reported_shares = safe_float(bmnr_shares_obs.value) if bmnr_shares_obs else None
    bmnr_buyback = safe_float(bmnr_buyback_obs.value) if bmnr_buyback_obs else 0
    buyback_after_share_date = bool(
        bmnr_shares_obs
        and bmnr_buyback_obs
        and bmnr_shares_obs.as_of
        and bmnr_buyback_obs.as_of
        and bmnr_buyback_obs.as_of > bmnr_shares_obs.as_of
    )
    bmnr_estimated_shares = (
        max(bmnr_reported_shares - bmnr_buyback, 0)
        if bmnr_reported_shares is not None and buyback_after_share_date
        else bmnr_reported_shares
    )
    bmnr_gross_nav = None
    if all(value is not None for value in [bmnr_eth, eth_px, bmnr_btc, btc_px, bmnr_cash, bmnr_beast, bmnr_eightco]):
        bmnr_gross_nav = bmnr_eth * eth_px / 1e6 + bmnr_btc * btc_px / 1e6 + bmnr_cash + bmnr_beast + bmnr_eightco
    bmnr_market_cap = bmnr_estimated_shares * bmnr_px if bmnr_estimated_shares is not None and bmnr_px is not None else None
    bmnr_market_to_gross = bmnr_market_cap / bmnr_gross_nav if bmnr_market_cap is not None and bmnr_gross_nav else None
    bmnr_gross_discount = 1 - bmnr_market_to_gross if bmnr_market_to_gross is not None else None
    bmnr_nav_per_share = bmnr_gross_nav / bmnr_estimated_shares if bmnr_gross_nav is not None and bmnr_estimated_shares else None
    bmnr_eth_per_1000_shares = bmnr_eth * 1000 / (bmnr_estimated_shares * 1e6) if bmnr_eth is not None and bmnr_estimated_shares else None
    bmnr_staked_ratio = bmnr_staked_eth / bmnr_eth if bmnr_staked_eth is not None and bmnr_eth else None
    bmnr_reported_gap = (
        abs(bmnr_gross_nav - bmnr_reported_total) / ((bmnr_gross_nav + bmnr_reported_total) / 2)
        if bmnr_gross_nav and bmnr_reported_total
        else None
    )

    result = {
        "prices": {
            "btc_usd": btc_px,
            "eth_usd": eth_px,
            "mstr_usd": mstr_px,
            "bmnr_usd": bmnr_px,
            "strc_usd": strc_px,
        },
        "price_basis": {
            "btc_usd": btc_basis,
            "eth_usd": eth_basis,
            "mstr_usd": mstr_basis,
            "bmnr_usd": bmnr_basis,
            "strc_usd": strc_basis,
        },
        "market_radar": {
            "fear_greed": safe_float(latest_value(observations, "fear_greed_value")),
            "fear_greed_timestamp": latest_value(observations, "fear_greed_timestamp"),
            "btc_fee_fastest_sat_vb": safe_float(latest_value(observations, "btc_fee_fastest_sat_vb")),
            "btc_fee_hour_sat_vb": safe_float(latest_value(observations, "btc_fee_hour_sat_vb")),
            "btc_hashrate_ths": safe_float(latest_value(observations, "btc_hashrate_ths")),
            "treasury_avg_bill_rate_pct": safe_float(latest_value(observations, "treasury_avg_bill_rate_pct")),
            "btc_200dma": safe_float(latest_value(observations, "btc_200dma")),
            "btc_50dma": safe_float(latest_value(observations, "btc_50dma")),
            "btc_200wma": safe_float(latest_value(observations, "btc_200wma")),
            "btc_1y_ath": safe_float(latest_value(observations, "btc_1y_ath")),
            "btc_1y_ath_date": latest_value(observations, "btc_1y_ath_date"),
            "btc_days_from_1y_ath": safe_float(latest_value(observations, "btc_days_from_1y_ath")),
            "btc_drawdown_1y_pct": safe_float(latest_value(observations, "btc_drawdown_1y_pct")),
            "btc_return_7d_pct": safe_float(latest_value(observations, "btc_return_7d_pct")),
            "btc_return_30d_pct": safe_float(latest_value(observations, "btc_return_30d_pct")),
            "btc_return_90d_pct": safe_float(latest_value(observations, "btc_return_90d_pct")),
            "btc_mvrv_current": safe_float(latest_value(observations, "btc_mvrv_current")),
            "btc_supply_current": safe_float(latest_value(observations, "btc_supply_current")),
            "btc_market_cap_coinmetrics_usd": safe_float(latest_value(observations, "btc_market_cap_coinmetrics_usd")),
            "etf_flow_status": latest_value(observations, "btc_etf_flow_status") or "unavailable",
            "etf_flow_as_of": latest_observation(observations, "btc_etf_flow_1d_usd").as_of if latest_observation(observations, "btc_etf_flow_1d_usd") else None,
            "etf_flow_source_count": safe_float(latest_value(observations, "btc_etf_flow_source_count")),
            "etf_flow_component_completeness": safe_float(latest_value(observations, "btc_etf_component_completeness")),
            "etf_flow_official_major_fund_gap": safe_float(latest_value(observations, "btc_etf_official_major_fund_gap")),
            "etf_flow_official_major_fund_coverage": safe_float(latest_value(observations, "btc_etf_official_major_fund_coverage")),
            "etf_flow_backup_component_gap": safe_float(latest_value(observations, "btc_etf_backup_component_gap")),
            "etf_flow_backup_component_coverage": safe_float(latest_value(observations, "btc_etf_backup_component_coverage")),
            "etf_flow_validation_inputs_json": latest_value(observations, "btc_etf_validation_inputs_json"),
            "etf_flow_validation_scope": latest_observation(observations, "btc_etf_flow_status").detail if latest_observation(observations, "btc_etf_flow_status") else None,
            "etf_flow_1d_usd": safe_float(latest_value(observations, "btc_etf_flow_1d_usd")),
            "etf_flow_7d_usd": safe_float(latest_value(observations, "btc_etf_flow_7d_usd")),
            "etf_flow_30d_usd": safe_float(latest_value(observations, "btc_etf_flow_30d_usd")),
            "eth_etf_flow_status": latest_value(observations, "eth_etf_flow_status") or "unavailable",
            "eth_etf_flow_as_of": latest_observation(observations, "eth_etf_flow_1d_usd").as_of if latest_observation(observations, "eth_etf_flow_1d_usd") else None,
            "eth_etf_flow_source_count": safe_float(latest_value(observations, "eth_etf_flow_source_count")),
            "eth_etf_flow_component_completeness": safe_float(latest_value(observations, "eth_etf_component_completeness")),
            "eth_etf_flow_official_major_fund_gap": safe_float(latest_value(observations, "eth_etf_official_major_fund_gap")),
            "eth_etf_flow_official_major_fund_coverage": safe_float(latest_value(observations, "eth_etf_official_major_fund_coverage")),
            "eth_etf_flow_backup_component_gap": safe_float(latest_value(observations, "eth_etf_backup_component_gap")),
            "eth_etf_flow_backup_component_coverage": safe_float(latest_value(observations, "eth_etf_backup_component_coverage")),
            "eth_etf_flow_validation_inputs_json": latest_value(observations, "eth_etf_validation_inputs_json"),
            "eth_etf_flow_validation_scope": latest_observation(observations, "eth_etf_flow_status").detail if latest_observation(observations, "eth_etf_flow_status") else None,
            "eth_etf_flow_1d_usd": safe_float(latest_value(observations, "eth_etf_flow_1d_usd")),
            "eth_etf_flow_7d_usd": safe_float(latest_value(observations, "eth_etf_flow_7d_usd")),
            "eth_etf_flow_30d_usd": safe_float(latest_value(observations, "eth_etf_flow_30d_usd")),
            "automation_limits": {
                "mvrv_z_score_limit": "Coin Metrics community API exposes current MVRV ratio, not free MVRV-Z; dashboard uses ratio gate instead of stale Z-score.",
                "realized_loss": "Glassnode/CheckOnChain realized-loss series is not available as a stable free API; not used as a hard trigger.",
                "google_trends": "No stable unauthenticated official API; excluded from hard gates.",
                "macro_calendar": "No stable free official event API wired; regulatory/event gate remains manual review only.",
            },
        },
        "mstr_metrics": {
            "btc_nav_musd": btc_nav_musd,
            "common_equity_price_to_nav": equity_mnav,
            "equity_mnav": equity_mnav,
            "enterprise_value_to_btc_nav": enterprise_mnav,
            "enterprise_mnav": enterprise_mnav,
            "pref_dilution_flag": pref_dilution_flag,
            "usd_reserve_musd": inputs["usd_reserve_musd"],
            "usd_reserve_basis": input_provenance.get("fields", {}).get("usd_reserve_musd", {}).get("basis"),
            "usd_reserve_confidence": input_provenance.get("fields", {}).get("usd_reserve_musd", {}).get("confidence"),
            "coverage_months": coverage_months,
            "weekly_reported_btc_sales_musd": weekly_sales,
            "sale_ratio": sale_ratio,
            "sale_ratio_basis": input_provenance.get("fields", {}).get("weekly_btc_sales_musd", {}).get("basis"),
            "sale_ratio_confidence": input_provenance.get("fields", {}).get("weekly_btc_sales_musd", {}).get("confidence"),
            "sats_per_share": sats_per_share,
            "strc_discount": strc_discount,
            "common_valuation_gate_ok": common_valuation_gate_ok,
            "capital_flywheel_gate_ok": capital_flywheel_gate_ok,
            "contract_red_light": contract_red_light,
        },
        "bmnr_metrics": {
            "holdings_as_of": latest_observation(observations, "bmnr_eth_holdings").as_of if latest_observation(observations, "bmnr_eth_holdings") else None,
            "eth_holdings": bmnr_eth,
            "btc_holdings": bmnr_btc,
            "staked_eth": bmnr_staked_eth,
            "staked_eth_ratio": bmnr_staked_ratio,
            "cash_marketable_musd": bmnr_cash,
            "beast_stake_musd": bmnr_beast,
            "eightco_stake_musd": bmnr_eightco,
            "reported_total_holdings_musd": bmnr_reported_total,
            "bottom_up_gross_treasury_musd": bmnr_gross_nav,
            "reported_total_crosscheck_gap": bmnr_reported_gap,
            "sec_reported_shares_m": bmnr_reported_shares,
            "share_count_as_of": bmnr_shares_obs.as_of if bmnr_shares_obs else None,
            "weekly_buyback_shares_m": bmnr_buyback,
            "buyback_as_of": bmnr_buyback_obs.as_of if bmnr_buyback_obs else None,
            "buyback_adjusted_shares_estimate_m": bmnr_estimated_shares,
            "buyback_adjustment_applied": buyback_after_share_date,
            "market_cap_estimate_musd": bmnr_market_cap,
            "market_cap_to_gross_treasury": bmnr_market_to_gross,
            "gross_treasury_discount": bmnr_gross_discount,
            "gross_treasury_value_per_share": bmnr_nav_per_share,
            "eth_per_1000_shares": bmnr_eth_per_1000_shares,
            "gross_treasury_price_as_of": today_utc(),
            "reported_total_value_basis": "BMNR rounded management total; underlying price timestamp not disclosed",
            "quality": "gross_asset_view_not_net_nav",
            "liability_treatment": "未扣除完整負債、優先股與其他或有項目；不可當作普通股淨 NAV",
        },
        "manual_inputs": inputs,
        "manual_seed_inputs": MANUAL_INPUTS,
        "manual_input_provenance": input_provenance,
        "sec_companyfacts": {
            "cash_musd": safe_float(latest_value(observations, "mstr_sec_cash_musd")),
            "common_shares_outstanding_m": safe_float(latest_value(observations, "mstr_sec_common_shares_outstanding_m")),
            "diluted_shares_m": safe_float(latest_value(observations, "mstr_sec_diluted_shares_m")),
            "stockholders_equity_musd": safe_float(latest_value(observations, "mstr_sec_stockholders_equity_musd")),
            "preferred_dividends_musd": safe_float(latest_value(observations, "mstr_sec_preferred_dividends_musd")),
            "preferred_cash_dividends_musd": safe_float(latest_value(observations, "mstr_sec_preferred_cash_dividends_musd")),
            "deferred_tax_liability_musd": safe_float(latest_value(observations, "mstr_sec_deferred_tax_liability_musd")),
            "status": "automated_sec_companyfacts_supporting_check",
        },
    }
    result["btc_standard"] = build_btc_standards(result)
    return result


def score_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    m = metrics["mstr_metrics"]
    score = 0
    reasons: list[str] = []
    reason_codes: list[str] = []
    if m["common_valuation_gate_ok"]:
        score += 2
        reason_codes.append("COMMON_EQUITY_AT_OR_BELOW_NAV")
        reasons.append("Common-equity price/NAV is at or below 1.0 without preferred-financing distortion flag")
    else:
        score -= 2
        reason_codes.append("COMMON_EQUITY_PREMIUM_OR_UNVERIFIED")
        reasons.append("Common-equity price/NAV exceeds 1.0, is unavailable, or preferred-financing distortion flag is active")
    if m["coverage_months"] >= 12:
        score += 1
        reason_codes.append("COVERAGE_AT_LEAST_12M")
        reasons.append("USD reserve coverage remains above the 12-month minimum buffer")
    else:
        score -= 2
        reason_codes.append("COVERAGE_BELOW_12M")
        reasons.append("USD reserve coverage is below the 12-month red line")
    if m["sale_ratio"] is None:
        score -= 3
        reason_codes.append("SALE_RATIO_UNKNOWN")
        reasons.append("Rolling 7-day reported BTC sales are not observable from a current official disclosure; treated as unknown, not zero")
    elif m["sale_ratio"] > 2:
        score -= 3
        reason_codes.append("SALE_RATIO_ABOVE_2X")
        reasons.append("Weekly BTC-sale pressure ratio is above 2x")
    if m["strc_discount"] is not None and m["strc_discount"] > 0.05:
        score -= 2
        reason_codes.append("STRC_DISCOUNT_ABOVE_5PCT")
        reasons.append("STRC discount is above 5%, weakening the preferred-market trust vote")
    state_code = "BLOCK_LEVERAGED_ADD" if m["contract_red_light"] else "WATCH_ONLY_NO_CHASE"
    state = "block_leveraged_add" if m["contract_red_light"] else "watch_only_no_chase"
    return {"score": score, "state": state, "state_code": state_code, "reason_codes": reason_codes, "reasons": reasons}


def collect_all() -> list[Observation]:
    collectors = [
        ("coingecko", collect_coingecko_btc),
        ("coinbase", lambda: [collect_coinbase_btc()]),
        ("coinbase_eth", lambda: [collect_coinbase_eth()]),
        ("kraken_btc", lambda: [collect_kraken_spot("BTC", "XBTUSD")]),
        ("kraken_eth", lambda: [collect_kraken_spot("ETH", "ETHUSD")]),
        ("btc_technicals", collect_yahoo_btc_technicals),
        ("mstr_yahoo", lambda: [collect_yahoo_equity("MSTR")]),
        ("bmnr_yahoo", lambda: [collect_yahoo_equity("BMNR")]),
        ("strc_yahoo", lambda: [collect_yahoo_equity("STRC")]),
        ("mstr_nasdaq", lambda: [collect_nasdaq_equity("MSTR")]),
        ("bmnr_nasdaq", lambda: [collect_nasdaq_equity("BMNR")]),
        ("strc_nasdaq", lambda: [collect_nasdaq_equity("STRC")]),
        ("fng", collect_fear_greed),
        ("mempool", collect_mempool_fees),
        ("hashrate", lambda: [collect_blockchain_hashrate()]),
        ("treasury", lambda: [collect_treasury_average_rate()]),
        ("coinmetrics", collect_coinmetrics_btc_cycle),
        ("etf_flow", collect_verified_etf_flows),
        ("sec", collect_sec_submissions),
        ("sec_facts", collect_mstr_sec_companyfacts),
        ("mstr_cover_shares", collect_mstr_cover_shares),
        ("mstr_sec_capital_structure", collect_mstr_sec_capital_structure),
        ("strategy_purchases", collect_strategy_purchases),
        ("strategy_sec_btc_updates", collect_strategy_sec_btc_updates),
        ("bmnr_sec_treasury", collect_bmnr_sec_treasury),
        ("sbet_sec_treasury", collect_sbet_sec_treasury),
    ]
    observations: list[Observation] = []
    for name, collector in collectors:
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                observations.extend(collector())
                last_exc = None
                break
            except Exception as exc:  # Keep partial data visible, verifier decides pass/fail.
                last_exc = exc
                if attempt < 3:
                    time.sleep(0.75 * attempt)
        if last_exc is not None:
            observations.append(obs(f"{name}_error", None, name, "", ok=False, detail=repr(last_exc)[:500]))
        time.sleep(0.25)
    return observations


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    manual = snapshot.setdefault("metrics", {}).setdefault("manual_inputs", {})
    for key, value in MANUAL_INPUTS.items():
        manual.setdefault(key, value)
    return snapshot


def upsert_database(snapshot: dict[str, Any]) -> dict[str, Any]:
    database = load_json(DATABASE_PATH, {"schema": 1, "snapshots": []})
    snapshots = [normalize_snapshot(item) for item in database.get("snapshots", []) if item.get("date") != snapshot["date"]]
    snapshots.append(normalize_snapshot(snapshot))
    snapshots.sort(key=lambda item: item.get("date", ""))
    database["snapshots"] = snapshots[-730:]
    database["schema"] = 2
    database["updated_at"] = now_iso()
    return database


def main() -> int:
    observations = collect_all()
    batch_generated_at = now_iso()
    raw = {
        "schema": 2,
        "date": today_utc(),
        "generated_at": batch_generated_at,
        "batch_id": batch_generated_at,
        "observations": [item.to_dict() for item in observations],
    }
    metrics = compute_metrics(observations)
    snapshot = {
        "schema": 2,
        "date": today_utc(),
        "generated_at": batch_generated_at,
        "batch_id": batch_generated_at,
        "metrics": metrics,
        "decision": score_snapshot(metrics),
        "source_count": sum(1 for item in observations if item.ok),
        "error_count": sum(1 for item in observations if not item.ok),
    }
    write_json(RAW_PATH, raw)
    write_json(SNAPSHOT_PATH, snapshot)
    write_json(DATABASE_PATH, upsert_database(snapshot))
    print(json.dumps({"snapshot": str(SNAPSHOT_PATH), "ok_sources": snapshot["source_count"], "errors": snapshot["error_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
