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
        return response.read()


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


def compute_metrics(observations: list[Observation]) -> dict[str, Any]:
    btc_prices = [safe_float(latest_value(observations, n)) for n in ["btc_usd_coingecko", "btc_usd_coinbase"]]
    btc_prices = [p for p in btc_prices if p is not None]
    btc_px = sum(btc_prices) / len(btc_prices) if btc_prices else None
    mstr_px = safe_float(latest_value(observations, "mstr_usd_yahoo")) or safe_float(latest_value(observations, "mstr_usd_stooq"))
    bmnr_px = safe_float(latest_value(observations, "bmnr_usd_yahoo")) or safe_float(latest_value(observations, "bmnr_usd_stooq"))
    strc_px = safe_float(latest_value(observations, "strc_usd_yahoo")) or safe_float(latest_value(observations, "strc_usd_stooq"))

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
    }


def score_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    m = metrics["mstr_metrics"]
    score = 0
    reasons: list[str] = []
    if m["mnav_gate_ok"]:
        score += 2
        reasons.append("M1/M2 雙軌 mNAV 達標且未觸發稀釋旗標")
    else:
        score -= 2
        reasons.append("M1/M2 或稀釋旗標尚未允許第二等份加倉")
    if m["coverage_months"] >= 12:
        score += 1
        reasons.append("覆蓋月數仍高於 12 個月紅線")
    else:
        score -= 2
        reasons.append("覆蓋月數低於 12 個月紅線")
    if m["sale_ratio"] > 2:
        score -= 3
        reasons.append("週賣幣比值高於 2，視為被迫賣幣風險")
    if m["strc_discount"] is not None and m["strc_discount"] > 0.05:
        score -= 2
        reasons.append("STRC 折價超過 5%，優先股信任票轉弱")
    state = "禁止小倉合約加碼" if m["contract_red_light"] else "可列入觀察，不自動追價"
    return {"score": score, "state": state, "reasons": reasons}


def collect_all() -> list[Observation]:
    collectors = [
        ("coingecko", collect_coingecko_btc),
        ("coinbase", lambda: [collect_coinbase_btc()]),
        ("mstr_yahoo", lambda: [collect_yahoo_equity("MSTR")]),
        ("bmnr_yahoo", lambda: [collect_yahoo_equity("BMNR")]),
        ("strc_yahoo", lambda: [collect_yahoo_equity("STRC")]),
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
