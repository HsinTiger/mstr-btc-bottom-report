#!/usr/bin/env python3
"""Daily market data collector for the MSTR/BTC dashboard.

No paid API keys are required. The collector writes raw observations and a compact
snapshot that the verifier and static pages can consume.
"""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import sys
import time
import gzip
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
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

MANUAL_INPUTS = {
    "mstr_btc_holdings": 843_775,
    "usd_reserve_musd": 2_550,
    "cash_other_musd": 0,
    "net_deferred_tax_liability_musd": 0,  # fallback only; SEC companyfacts overrides when available
    "debt_face_musd": 8_214,
    "annual_interest_musd": 34,
    "preferred": {
        "STRF": {"notional_musd": 3_700, "rate": 0.10},
        "STRC": {"notional_musd": 7_800, "rate": 0.12},
        "STRK": {"notional_musd": 2_100, "rate": 0.08},
        "STRD": {"notional_musd": 4_200, "rate": 0.10},
    },
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


def obs(name: str, value: float | str | None, source: str, url: str, ok: bool = True, detail: str = "") -> Observation:
    return Observation(name=name, value=value, source=source, url=url, fetched_at=now_iso(), ok=ok, detail=detail)


def collect_coingecko_btc() -> list[Observation]:
    url = (
        "https://api.coingecko.com/api/v3/simple/price?"
        "ids=bitcoin&vs_currencies=usd&include_market_cap=true&"
        "include_24hr_vol=true&include_24hr_change=true&include_last_updated_at=true"
    )
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})["bitcoin"]
    return [
        obs("btc_usd_coingecko", data.get("usd"), "CoinGecko simple price", url),
        obs("btc_market_cap_usd", data.get("usd_market_cap"), "CoinGecko simple price", url),
        obs("btc_24h_volume_usd", data.get("usd_24h_vol"), "CoinGecko simple price", url),
        obs("btc_24h_change_pct", data.get("usd_24h_change"), "CoinGecko simple price", url),
        obs("btc_last_updated_unix", data.get("last_updated_at"), "CoinGecko simple price", url),
    ]


def collect_coinbase_btc() -> Observation:
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    return obs("btc_usd_coinbase", safe_float(data.get("price")), "Coinbase Exchange ticker", url)


def yahoo_chart(ticker: str) -> tuple[float | None, str, str]:
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
    return safe_float(price), url, detail



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
    value, url, detail = yahoo_chart(ticker)
    return obs(f"{ticker.lower()}_usd_yahoo", value, "Yahoo Finance chart", url, ok=value is not None, detail=detail)


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
    return [
        obs("fear_greed_value", safe_float(row.get("value")), "Alternative.me Fear & Greed", url, ok=row.get("value") is not None, detail=str(row.get("value_classification") or "")),
        obs("fear_greed_timestamp", row.get("timestamp"), "Alternative.me Fear & Greed", url, ok=bool(row.get("timestamp"))),
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
    return [
        obs("btc_price_coinmetrics_usd", safe_float(latest.get("PriceUSD")), "Coin Metrics community API", url, ok=latest.get("PriceUSD") is not None, detail=detail),
        obs("btc_mvrv_current", safe_float(latest.get("CapMVRVCur")), "Coin Metrics community API", url, ok=latest.get("CapMVRVCur") is not None, detail=detail),
        obs("btc_supply_current", safe_float(latest.get("SplyCur")), "Coin Metrics community API", url, ok=latest.get("SplyCur") is not None, detail=detail),
        obs("btc_market_cap_coinmetrics_usd", safe_float(latest.get("CapMrktCurUSD")), "Coin Metrics community API", url, ok=latest.get("CapMrktCurUSD") is not None, detail=detail),
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


def collect_walletpilot_etf_flows() -> list[Observation]:
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
    status = "automated_third_party_single_source" if one_btc is not None else "unavailable"
    detail = "source=WalletPilot third_party_single_source hard_trigger=false"
    return [
        obs("btc_etf_flow_status", status, "WalletPilot Bitcoin ETF tracker", url, ok=status != "unavailable", detail=detail),
        obs("btc_etf_flow_1d_btc", one_btc, "WalletPilot Bitcoin ETF tracker", url, ok=one_btc is not None, detail=detail),
        obs("btc_etf_flow_1d_usd", one_usd, "WalletPilot Bitcoin ETF tracker", url, ok=one_usd is not None, detail=detail),
        obs("btc_etf_flow_7d_btc", seven_btc, "WalletPilot Bitcoin ETF tracker", url, ok=seven_btc is not None, detail=detail),
        obs("btc_etf_flow_7d_usd", seven_usd, "WalletPilot Bitcoin ETF tracker", url, ok=seven_usd is not None, detail=detail),
        obs("btc_etf_flow_30d_btc", thirty_btc, "WalletPilot Bitcoin ETF tracker", url, ok=thirty_btc is not None, detail=detail),
        obs("btc_etf_flow_30d_usd", thirty_usd, "WalletPilot Bitcoin ETF tracker", url, ok=thirty_usd is not None, detail=detail),
    ]


def latest_sec_fact(facts: dict[str, Any], tag: str, unit: str = "USD", instant: bool | None = None) -> tuple[float | None, str]:
    rows = facts.get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    if instant is True:
        rows = [row for row in rows if not row.get("start")]
    elif instant is False:
        rows = [row for row in rows if row.get("start")]
    if not rows:
        return None, f"tag={tag} unit={unit} missing"
    latest = sorted(rows, key=lambda row: (row.get("end") or "", row.get("filed") or "", row.get("frame") or ""))[-1]
    return safe_float(latest.get("val")), f"tag={tag} unit={unit} form={latest.get('form')} filed={latest.get('filed')} end={latest.get('end')} accn={latest.get('accn')}"


def collect_mstr_sec_companyfacts() -> list[Observation]:
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001050446.json"
    data = fetch_json(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    facts = data.get("facts", {})
    cash, cash_detail = latest_sec_fact(facts, "CashAndCashEquivalentsAtCarryingValue", "USD", True)
    diluted, diluted_detail = latest_sec_fact(facts, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False)
    stockholders_equity, equity_detail = latest_sec_fact(facts, "StockholdersEquity", "USD", True)
    pref_div, pref_div_detail = latest_sec_fact(facts, "DividendsPreferredStock", "USD", False)
    pref_cash_div, pref_cash_div_detail = latest_sec_fact(facts, "DividendsPreferredStockCash", "USD", False)
    deferred_tax_liability, dtl_detail = latest_sec_fact(facts, "DeferredTaxLiabilities", "USD", True)
    return [
        obs("mstr_sec_cash_musd", cash / 1e6 if cash is not None else None, "SEC companyfacts", url, ok=cash is not None, detail=cash_detail),
        obs("mstr_sec_diluted_shares_m", diluted / 1e6 if diluted is not None else None, "SEC companyfacts", url, ok=diluted is not None, detail=diluted_detail),
        obs("mstr_sec_stockholders_equity_musd", stockholders_equity / 1e6 if stockholders_equity is not None else None, "SEC companyfacts", url, ok=stockholders_equity is not None, detail=equity_detail),
        obs("mstr_sec_preferred_dividends_musd", pref_div / 1e6 if pref_div is not None else None, "SEC companyfacts", url, ok=pref_div is not None, detail=pref_div_detail),
        obs("mstr_sec_preferred_cash_dividends_musd", pref_cash_div / 1e6 if pref_cash_div is not None else None, "SEC companyfacts", url, ok=pref_cash_div is not None, detail=pref_cash_div_detail),
        obs("mstr_sec_deferred_tax_liability_musd", deferred_tax_liability / 1e6 if deferred_tax_liability is not None else None, "SEC companyfacts", url, ok=deferred_tax_liability is not None, detail=dtl_detail),
    ]

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
    detail = (
        f"date={latest.get('date_of_purchase')} title={latest.get('title')} "
        f"sec_url={(latest.get('sec') or {}).get('url')} source=strategy_purchases_next_data"
    )
    return [
        obs("mstr_strategy_btc_holdings", safe_float(latest.get("btc_holdings")), "Strategy purchases page", url, ok=latest.get("btc_holdings") is not None, detail=detail),
        obs("mstr_strategy_latest_btc_delta", safe_float(latest.get("count")), "Strategy purchases page", url, ok=latest.get("count") is not None, detail=detail),
        obs("mstr_strategy_latest_purchase_price", safe_float(latest.get("purchase_price")), "Strategy purchases page", url, ok=latest.get("purchase_price") is not None, detail=detail),
        obs("mstr_strategy_latest_purchase_usd_m", (safe_float(latest.get("total_purchase_price")) or 0) / 1e6, "Strategy purchases page", url, ok=latest.get("total_purchase_price") is not None, detail=detail),
        obs("mstr_strategy_average_cost", safe_float(latest.get("average_price")), "Strategy purchases page", url, ok=latest.get("average_price") is not None, detail=detail),
        obs("mstr_strategy_latest_purchase_date", latest.get("date_of_purchase"), "Strategy purchases page", url, ok=bool(latest.get("date_of_purchase")), detail=detail),
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


def set_automated_input(
    inputs: dict[str, Any],
    provenance: dict[str, Any],
    key: str,
    value: float | None,
    source_ref: str,
    detail: str | None,
) -> None:
    if value is None:
        return
    inputs[key] = value
    provenance.setdefault("fields", {})[key] = {
        "source_type": "automated_sec_companyfacts",
        "source_ref": source_ref,
        "detail": detail,
        "as_of": today_utc(),
        "confidence": "medium",
    }


def build_effective_inputs(observations: list[Observation]) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = copy.deepcopy(MANUAL_INPUTS)
    provenance = copy.deepcopy(load_input_provenance())
    provenance["status"] = "mixed_automated_manual"
    provenance["updated_at"] = today_utc()
    sec_cash = latest_observation(observations, "mstr_sec_cash_musd")
    sec_diluted = latest_observation(observations, "mstr_sec_diluted_shares_m")
    sec_dtl = latest_observation(observations, "mstr_sec_deferred_tax_liability_musd")
    strategy_btc = latest_observation(observations, "mstr_strategy_btc_holdings")
    strategy_weekly = latest_observation(observations, "mstr_strategy_latest_purchase_usd_m")
    set_automated_input(inputs, provenance, "usd_reserve_musd", safe_float(sec_cash.value) if sec_cash else None, "SEC companyfacts CashAndCashEquivalentsAtCarryingValue", sec_cash.detail if sec_cash else None)
    set_automated_input(inputs, provenance, "diluted_shares_m", safe_float(sec_diluted.value) if sec_diluted else None, "SEC companyfacts WeightedAverageNumberOfDilutedSharesOutstanding", sec_diluted.detail if sec_diluted else None)
    set_automated_input(inputs, provenance, "net_deferred_tax_liability_musd", safe_float(sec_dtl.value) if sec_dtl else None, "SEC companyfacts DeferredTaxLiabilities", sec_dtl.detail if sec_dtl else None)
    set_automated_input(inputs, provenance, "mstr_btc_holdings", safe_float(strategy_btc.value) if strategy_btc else None, "Strategy official purchases page", strategy_btc.detail if strategy_btc else None)
    set_automated_input(inputs, provenance, "weekly_btc_sales_musd", abs(safe_float(strategy_weekly.value) or 0) if strategy_weekly else None, "Strategy official purchases page latest transaction absolute USD amount", strategy_weekly.detail if strategy_weekly else None)
    return inputs, provenance


def load_input_provenance() -> dict[str, Any]:
    return load_json(PROVENANCE_PATH, {"schema": 1, "status": "missing", "fields": {}})


def compute_metrics(observations: list[Observation]) -> dict[str, Any]:
    btc_prices = [safe_float(latest_value(observations, n)) for n in ["btc_usd_coingecko", "btc_usd_coinbase"]]
    btc_prices = [p for p in btc_prices if p is not None]
    btc_px = sum(btc_prices) / len(btc_prices) if btc_prices else None
    mstr_px, mstr_basis = selected_price(observations, "mstr_usd_yahoo", "mstr_usd_nasdaq", "MSTR")
    bmnr_px, bmnr_basis = selected_price(observations, "bmnr_usd_yahoo", "bmnr_usd_nasdaq", "BMNR")
    strc_px, strc_basis = selected_price(observations, "strc_usd_yahoo", "strc_usd_nasdaq", "STRC")

    inputs, input_provenance = build_effective_inputs(observations)
    pref_total = sum(item["notional_musd"] for item in inputs["preferred"].values())
    annual_div = sum(item["notional_musd"] * item["rate"] for item in inputs["preferred"].values())
    annual_obligation = annual_div + inputs["annual_interest_musd"]
    coverage_months = inputs["usd_reserve_musd"] / (annual_obligation / 12)
    weekly_need = annual_obligation / 52
    sale_ratio = inputs["weekly_btc_sales_musd"] / weekly_need
    sats_per_share = inputs["mstr_btc_holdings"] * 1e8 / (inputs["diluted_shares_m"] * 1e6)

    btc_nav_musd = None
    equity_mnav = None
    enterprise_mnav = None
    pref_dilution_flag = False
    if btc_px and mstr_px:
        btc_nav_musd = inputs["mstr_btc_holdings"] * btc_px / 1e6
        mkt_cap_musd = inputs["diluted_shares_m"] * mstr_px
        net_to_common = btc_nav_musd + inputs["usd_reserve_musd"] + inputs["cash_other_musd"] - inputs["debt_face_musd"] - pref_total - inputs["net_deferred_tax_liability_musd"]
        equity_mnav = mkt_cap_musd / net_to_common if net_to_common > 0 else None
        enterprise_mnav = (mkt_cap_musd + inputs["debt_face_musd"] + pref_total) / btc_nav_musd
        pref_dilution_flag = pref_total > inputs["prev_pref_notional_musd"] and bool(equity_mnav and equity_mnav > inputs["prev_mnav_equity"])

    strc_discount = 1 - strc_px / 100 if strc_px else None
    mnav_gate_ok = bool(equity_mnav and enterprise_mnav and equity_mnav >= 1 and enterprise_mnav >= 1 and not pref_dilution_flag)
    contract_red_light = bool(sale_ratio > 2 or coverage_months < 12 or (strc_discount is not None and strc_discount > 0.05))

    return {
        "prices": {
            "btc_usd": btc_px,
            "mstr_usd": mstr_px,
            "bmnr_usd": bmnr_px,
            "strc_usd": strc_px,
        },
        "price_basis": {
            "btc_usd": {
                "selected_source": "CoinGecko/Coinbase average",
                "policy": "BTC 使用 CoinGecko 與 Coinbase 平均值，並由 verifier 檢查兩者差距",
            },
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
            "btc_mvrv_current": safe_float(latest_value(observations, "btc_mvrv_current")),
            "btc_supply_current": safe_float(latest_value(observations, "btc_supply_current")),
            "btc_market_cap_coinmetrics_usd": safe_float(latest_value(observations, "btc_market_cap_coinmetrics_usd")),
            "etf_flow_status": latest_value(observations, "btc_etf_flow_status") or "unavailable",
            "etf_flow_1d_btc": safe_float(latest_value(observations, "btc_etf_flow_1d_btc")),
            "etf_flow_1d_usd": safe_float(latest_value(observations, "btc_etf_flow_1d_usd")),
            "etf_flow_7d_btc": safe_float(latest_value(observations, "btc_etf_flow_7d_btc")),
            "etf_flow_7d_usd": safe_float(latest_value(observations, "btc_etf_flow_7d_usd")),
            "etf_flow_30d_btc": safe_float(latest_value(observations, "btc_etf_flow_30d_btc")),
            "etf_flow_30d_usd": safe_float(latest_value(observations, "btc_etf_flow_30d_usd")),
            "automation_limits": {
                "mvrv_z_score_limit": "Coin Metrics community API exposes current MVRV ratio, not free MVRV-Z; dashboard uses ratio gate instead of stale Z-score.",
                "realized_loss": "Glassnode/CheckOnChain realized-loss series is not available as a stable free API; not used as a hard trigger.",
                "google_trends": "No stable unauthenticated official API; excluded from hard gates.",
                "macro_calendar": "No stable free official event API wired; regulatory/event gate remains manual review only.",
            },
        },
        "mstr_metrics": {
            "btc_nav_musd": btc_nav_musd,
            "equity_mnav": equity_mnav,
            "enterprise_mnav": enterprise_mnav,
            "pref_dilution_flag": pref_dilution_flag,
            "coverage_months": coverage_months,
            "sale_ratio": sale_ratio,
            "sats_per_share": sats_per_share,
            "strc_discount": strc_discount,
            "mnav_gate_ok": mnav_gate_ok,
            "contract_red_light": contract_red_light,
        },
        "manual_inputs": inputs,
        "manual_seed_inputs": MANUAL_INPUTS,
        "manual_input_provenance": input_provenance,
        "sec_companyfacts": {
            "cash_musd": safe_float(latest_value(observations, "mstr_sec_cash_musd")),
            "diluted_shares_m": safe_float(latest_value(observations, "mstr_sec_diluted_shares_m")),
            "stockholders_equity_musd": safe_float(latest_value(observations, "mstr_sec_stockholders_equity_musd")),
            "preferred_dividends_musd": safe_float(latest_value(observations, "mstr_sec_preferred_dividends_musd")),
            "preferred_cash_dividends_musd": safe_float(latest_value(observations, "mstr_sec_preferred_cash_dividends_musd")),
            "deferred_tax_liability_musd": safe_float(latest_value(observations, "mstr_sec_deferred_tax_liability_musd")),
            "status": "automated_sec_companyfacts_supporting_check",
        },
    }


def score_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    m = metrics["mstr_metrics"]
    score = 0
    reasons: list[str] = []
    reason_codes: list[str] = []
    if m["mnav_gate_ok"]:
        score += 2
        reason_codes.append("MNAV_GATE_OK")
        reasons.append("Self-calculated common-equity and enterprise-value safety margins passed without preferred-dilution flag")
    else:
        score -= 2
        reason_codes.append("MNAV_GATE_CLOSED")
        reasons.append("Self-calculated common-equity or enterprise-value safety margin gate is closed, or preferred-dilution flag is active")
    if m["coverage_months"] >= 12:
        score += 1
        reason_codes.append("COVERAGE_ABOVE_12M")
        reasons.append("USD reserve coverage remains above the 12-month red line")
    else:
        score -= 2
        reason_codes.append("COVERAGE_BELOW_12M")
        reasons.append("USD reserve coverage is below the 12-month red line")
    if m["sale_ratio"] > 2:
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
        ("etf_flow", collect_walletpilot_etf_flows),
        ("sec", collect_sec_submissions),
        ("sec_facts", collect_mstr_sec_companyfacts),
        ("strategy_purchases", collect_strategy_purchases),
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
    database["updated_at"] = now_iso()
    return database


def main() -> int:
    observations = collect_all()
    raw = {
        "schema": 1,
        "date": today_utc(),
        "generated_at": now_iso(),
        "observations": [item.to_dict() for item in observations],
    }
    metrics = compute_metrics(observations)
    snapshot = {
        "schema": 1,
        "date": today_utc(),
        "generated_at": now_iso(),
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
