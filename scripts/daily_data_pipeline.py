#!/usr/bin/env python3
"""Daily market data collector for the MSTR/BTC dashboard.

No paid API keys are required. The collector writes raw observations and a compact
snapshot that the verifier and static pages can consume.
"""

from __future__ import annotations

import csv
import json
import math
import os
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
    "net_deferred_tax_liability_musd": 0,  # TODO: 每季用 10-Q/10-K income tax footnote 更新；淨遞延稅資產用負值
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


def yahoo_chart(ticker: str) -> tuple[float | None, str]:
    encoded = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    data = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    result = data["chart"]["result"][0]
    price = result.get("meta", {}).get("regularMarketPrice")
    if price is None:
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        price = closes[-1] if closes else None
    return safe_float(price), url



def clean_price(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").replace("%", "").strip()
    return safe_float(value)


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
    detail = f"timestamp={primary.get('lastTradeTimestamp')} realTime={primary.get('isRealTime')}"
    return obs(f"{ticker.lower()}_usd_nasdaq", value, "Nasdaq quote API", url, ok=value is not None, detail=detail)

def collect_yahoo_equity(ticker: str) -> Observation:
    value, url = yahoo_chart(ticker)
    return obs(f"{ticker.lower()}_usd_yahoo", value, "Yahoo Finance chart", url, ok=value is not None)


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


def latest_value(observations: list[Observation], name: str) -> float | str | None:
    for item in observations:
        if item.name == name:
            return item.value
    return None


def load_input_provenance() -> dict[str, Any]:
    return load_json(PROVENANCE_PATH, {"schema": 1, "status": "missing", "fields": {}})


def compute_metrics(observations: list[Observation]) -> dict[str, Any]:
    btc_prices = [safe_float(latest_value(observations, n)) for n in ["btc_usd_coingecko", "btc_usd_coinbase"]]
    btc_prices = [p for p in btc_prices if p is not None]
    btc_px = sum(btc_prices) / len(btc_prices) if btc_prices else None
    mstr_px = safe_float(latest_value(observations, "mstr_usd_yahoo")) or safe_float(latest_value(observations, "mstr_usd_nasdaq"))
    bmnr_px = safe_float(latest_value(observations, "bmnr_usd_yahoo")) or safe_float(latest_value(observations, "bmnr_usd_nasdaq"))
    strc_px = safe_float(latest_value(observations, "strc_usd_yahoo")) or safe_float(latest_value(observations, "strc_usd_nasdaq"))

    inputs = MANUAL_INPUTS
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
        "market_radar": {
            "fear_greed": safe_float(latest_value(observations, "fear_greed_value")),
            "fear_greed_timestamp": latest_value(observations, "fear_greed_timestamp"),
            "btc_fee_fastest_sat_vb": safe_float(latest_value(observations, "btc_fee_fastest_sat_vb")),
            "btc_fee_hour_sat_vb": safe_float(latest_value(observations, "btc_fee_hour_sat_vb")),
            "btc_hashrate_ths": safe_float(latest_value(observations, "btc_hashrate_ths")),
            "treasury_avg_bill_rate_pct": safe_float(latest_value(observations, "treasury_avg_bill_rate_pct")),
            "etf_flow_status": "not_automated_yet",
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
        "manual_input_provenance": load_input_provenance(),
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
        ("sec", collect_sec_submissions),
    ]
    observations: list[Observation] = []
    for name, collector in collectors:
        try:
            observations.extend(collector())
            time.sleep(0.25)
        except Exception as exc:  # Keep partial data visible, verifier decides pass/fail.
            observations.append(obs(f"{name}_error", None, name, "", ok=False, detail=repr(exc)[:500]))
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
