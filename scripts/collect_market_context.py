#!/usr/bin/env python3
"""Collect verified macro, policy, equity, and on-chain context for daily research.

The artifact is analysis-only. Different update frequencies retain their own
``as_of`` values; retrieval time is never substituted for an observation date.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
OUTPUT_PATH = DATA_DIR / "market_context.json"
USER_AGENT = "mstr-btc-bottom-report/market-context hsin73@realtek.com"

FRED_SERIES = {
    "fed_assets": "WALCL",
    "m2_money_stock": "M2SL",
    "reserve_balances": "WRESBAL",
    "fed_funds": "DFF",
    "treasury_2y": "DGS2",
    "treasury_10y": "DGS10",
    "treasury_30y": "DGS30",
    "high_yield_oas": "BAMLH0A0HYM2",
    "investment_grade_oas": "BAMLC0A0CM",
    "wti_spot": "DCOILWTICO",
    "sp500": "SP500",
    "nasdaq": "NASDAQCOM",
    "vix": "VIXCLS",
    "broad_dollar": "DTWEXBGS",
}

POLICY_TERMS = (
    "bitcoin",
    "cryptocurrency",
    "crypto asset",
    "crypto-asset",
    "digital asset",
    "stablecoin",
    "blockchain",
    "tokenization",
    "tokenized",
    "distributed ledger",
    "virtual currency",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return now_utc().isoformat()


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def artifact_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "payload_hash"}


def fetch_text(url: str, *, timeout: int = 35) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,text/html,application/xml,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_json(url: str, *, data: Any = None, timeout: int = 35) -> Any:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = finite(error.headers.get("Retry-After")) if error.headers else None
            time.sleep(min(retry_after or attempt + 1, 5))
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError(f"JSON request exhausted retries: {url}")


def source_check(
    desk: str,
    provider: str,
    url: str,
    status: str,
    *,
    as_of: str | None = None,
    error: str | None = None,
    role: str = "primary",
) -> dict[str, Any]:
    return {
        "desk": desk,
        "provider": provider,
        "url": url,
        "status": status,
        "as_of": as_of,
        "error": error,
        "role": role,
    }


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def fred_rows(series_id: str) -> tuple[list[tuple[date, float]], str]:
    start = (now_utc().date() - timedelta(days=430)).isoformat()
    end = now_utc().date().isoformat()
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode(
        {"id": series_id, "cosd": start, "coed": end}
    )
    text = fetch_text(url)
    rows: list[tuple[date, float]] = []
    for row in csv.DictReader(io.StringIO(text)):
        value = finite(row.get(series_id))
        if value is None:
            continue
        rows.append((date.fromisoformat(row["observation_date"]), value))
    if not rows:
        raise ValueError(f"FRED {series_id} returned no observations")
    return rows, url


def value_on_or_before(rows: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    return next(((day, value) for day, value in reversed(rows) if day <= target), None)


def row_summary(rows: list[tuple[date, float]], *, unit: str, percent_change: bool = False) -> dict[str, Any]:
    latest_day, latest = rows[-1]
    prior_7 = value_on_or_before(rows, latest_day - timedelta(days=7))
    prior_30 = value_on_or_before(rows, latest_day - timedelta(days=30))
    prior_90 = value_on_or_before(rows, latest_day - timedelta(days=90))
    prior_365 = value_on_or_before(rows, latest_day - timedelta(days=365))

    def delta(prior: tuple[date, float] | None) -> float | None:
        if prior is None or prior[1] == 0:
            return None
        return latest / prior[1] - 1 if percent_change else latest - prior[1]

    return {
        "value": latest,
        "unit": unit,
        "as_of": latest_day.isoformat(),
        "prior_7d_value": prior_7[1] if prior_7 else None,
        "prior_7d_as_of": prior_7[0].isoformat() if prior_7 else None,
        "prior_30d_value": prior_30[1] if prior_30 else None,
        "prior_30d_as_of": prior_30[0].isoformat() if prior_30 else None,
        "prior_90d_value": prior_90[1] if prior_90 else None,
        "prior_90d_as_of": prior_90[0].isoformat() if prior_90 else None,
        "prior_365d_value": prior_365[1] if prior_365 else None,
        "prior_365d_as_of": prior_365[0].isoformat() if prior_365 else None,
        "change_7d": delta(prior_7),
        "change_30d": delta(prior_30),
        "change_90d": delta(prior_90),
        "change_365d": delta(prior_365),
        "change_mode": "percent" if percent_change else "absolute",
    }


def collect_fred() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(fred_rows, series): (name, series) for name, series in FRED_SERIES.items()}
        for future in as_completed(future_map):
            name, series = future_map[future]
            url = f"https://fred.stlouisfed.org/series/{series}"
            try:
                rows, fetch_url = future.result()
                percent_change = name in {"fed_assets", "m2_money_stock", "reserve_balances", "wti_spot", "sp500", "nasdaq", "vix", "broad_dollar"}
                unit = {
                    "fed_assets": "million_usd",
                    "m2_money_stock": "billion_usd_seasonally_adjusted",
                    "reserve_balances": "million_usd",
                    "fed_funds": "percent",
                    "treasury_2y": "percent",
                    "treasury_10y": "percent",
                    "treasury_30y": "percent",
                    "high_yield_oas": "percentage_points",
                    "investment_grade_oas": "percentage_points",
                    "wti_spot": "usd_per_barrel",
                    "sp500": "index",
                    "nasdaq": "index",
                    "vix": "index",
                    "broad_dollar": "index",
                }[name]
                output[name] = {**row_summary(rows, unit=unit, percent_change=percent_change), "series_id": series, "url": url}
                checks.append(source_check("macro", f"FRED {series}", fetch_url, "pass", as_of=rows[-1][0].isoformat()))
            except Exception as error:
                checks.append(source_check("macro", f"FRED {series}", url, "fail", error=str(error)))
    return output, checks


def collect_h41_assets() -> tuple[dict[str, Any], dict[str, Any]]:
    url = "https://www.federalreserve.gov/releases/h41/current/h41.htm"
    page = fetch_text(url)
    rows = re.findall(r"<tr\b[^>]*>(?:(?!</tr>).)*?</tr>", page, flags=re.IGNORECASE | re.DOTALL)
    row = next((item for item in rows if re.search(r">\s*Total assets\s*<", item, flags=re.IGNORECASE)), None)
    if not row:
        raise ValueError("H.4.1 total-assets row missing")
    values = [finite(item.replace(",", "")) for item in re.findall(r"\d[\d,]+", strip_tags(row))]
    total = next((item for item in values if item is not None and item > 1_000_000), None)
    if total is None:
        raise ValueError("H.4.1 total-assets value missing")
    plain_text = strip_tags(page)
    observation_match = re.search(r"Wednesday\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", plain_text)
    release_match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", plain_text)
    as_of = datetime.strptime(observation_match.group(1), "%b %d, %Y").date().isoformat() if observation_match else None
    release_date = datetime.strptime(release_match.group(1), "%B %d, %Y").date().isoformat() if release_match else None
    data = {"value": total, "unit": "million_usd", "as_of": as_of, "release_date": release_date, "url": url}
    return data, source_check("liquidity", "Federal Reserve H.4.1", url, "pass", as_of=as_of, role="independent_check")


def collect_nyfed_rrp() -> tuple[dict[str, Any], dict[str, Any]]:
    end = now_utc().date()
    start = end - timedelta(days=45)
    url = "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?" + urllib.parse.urlencode(
        {"startDate": start.isoformat(), "endDate": end.isoformat()}
    )
    operations = fetch_json(url).get("repo", {}).get("operations", [])
    valid = [item for item in operations if finite(item.get("totalAmtAccepted")) is not None]
    if not valid:
        raise ValueError("New York Fed RRP returned no observations")
    latest = max(valid, key=lambda item: item["operationDate"])
    latest_day = date.fromisoformat(latest["operationDate"])
    prior = max(
        (item for item in valid if date.fromisoformat(item["operationDate"]) <= latest_day - timedelta(days=30)),
        key=lambda item: item["operationDate"],
        default=None,
    )
    data = {
        "value": finite(latest["totalAmtAccepted"]),
        "unit": "usd",
        "as_of": latest["operationDate"],
        "operation_type": latest.get("operationType"),
        "prior_30d_value": finite((prior or {}).get("totalAmtAccepted")),
        "prior_30d_as_of": (prior or {}).get("operationDate"),
        "url": url,
    }
    return data, source_check("liquidity", "New York Fed RRP", url, "pass", as_of=data["as_of"])


def collect_tga() -> tuple[dict[str, Any], dict[str, Any]]:
    url = (
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/"
        "operating_cash_balance?sort=-record_date&page%5Bsize%5D=120"
    )
    rows = fetch_json(url).get("data", [])
    closing_rows = [row for row in rows if "Closing Balance" in str(row.get("account_type"))]
    item = max(closing_rows, key=lambda row: row["record_date"], default=None)
    value = finite((item or {}).get("open_today_bal"))
    if item is None or value is None:
        raise ValueError("Treasury TGA closing balance missing")
    latest_day = date.fromisoformat(item["record_date"])
    prior = max(
        (row for row in closing_rows if date.fromisoformat(row["record_date"]) <= latest_day - timedelta(days=30)),
        key=lambda row: row["record_date"],
        default=None,
    )
    data = {
        "value": value,
        "unit": "million_usd",
        "as_of": item["record_date"],
        "prior_30d_value": finite((prior or {}).get("open_today_bal")),
        "prior_30d_as_of": (prior or {}).get("record_date"),
        "url": url,
    }
    return data, source_check("liquidity", "U.S. Treasury Fiscal Data", url, "pass", as_of=data["as_of"])


def collect_treasury_curve() -> tuple[dict[str, Any], dict[str, Any]]:
    year = now_utc().year
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?"
        f"data=daily_treasury_yield_curve&field_tdr_date_value={year}"
    )
    root = ET.fromstring(fetch_text(url))
    namespaces = {
        "a": "http://www.w3.org/2005/Atom",
        "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
        "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    }
    observations: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", namespaces):
        props = entry.find("a:content/m:properties", namespaces)
        if props is None:
            continue
        values = {child.tag.split("}")[-1]: child.text for child in props}
        if values.get("NEW_DATE"):
            observations.append(values)
    if not observations:
        raise ValueError("Treasury yield curve returned no observations")
    latest = max(observations, key=lambda item: item["NEW_DATE"])
    data = {
        "treasury_2y": finite(latest.get("BC_2YEAR")),
        "treasury_10y": finite(latest.get("BC_10YEAR")),
        "treasury_30y": finite(latest.get("BC_30YEAR")),
        "as_of": latest["NEW_DATE"][:10],
        "unit": "percent",
        "url": url,
    }
    if any(data[key] is None for key in ("treasury_2y", "treasury_10y", "treasury_30y")):
        raise ValueError("Treasury curve tenor missing")
    return data, source_check("bonds", "U.S. Treasury", url, "pass", as_of=data["as_of"], role="primary")


def yahoo_history(symbol: str, days: int = 100) -> tuple[list[tuple[date, float]], str]:
    end = int((now_utc() + timedelta(days=1)).timestamp())
    start = int((now_utc() - timedelta(days=days)).timestamp())
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?" + urllib.parse.urlencode(
        {"period1": start, "period2": end, "interval": "1d", "events": "history"}
    )
    result = fetch_json(url).get("chart", {}).get("result", [None])[0]
    if not result:
        raise ValueError(f"Yahoo {symbol} returned no chart")
    timestamps = result.get("timestamp", [])
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    rows = [
        (datetime.fromtimestamp(timestamp, timezone.utc).date(), value)
        for timestamp, raw in zip(timestamps, closes)
        if (value := finite(raw)) is not None
    ]
    if len(rows) < 20:
        raise ValueError(f"Yahoo {symbol} history too short")
    return rows, url


def collect_market_proxies() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    symbols = {"sp500": "^GSPC", "nasdaq": "^IXIC", "vix": "^VIX", "oil_future": "CL=F", "hyg": "HYG", "ief": "IEF"}
    output: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(yahoo_history, symbol): (name, symbol) for name, symbol in symbols.items()}
        for future in as_completed(future_map):
            name, symbol = future_map[future]
            try:
                rows, url = future.result()
                output[name] = {**row_summary(rows, unit="index_or_price", percent_change=True), "symbol": symbol, "url": url}
                checks.append(source_check("cross_asset", f"Yahoo {symbol}", url, "pass", as_of=rows[-1][0].isoformat(), role="fallback_or_independent_check"))
            except Exception as error:
                checks.append(source_check("cross_asset", f"Yahoo {symbol}", "https://finance.yahoo.com", "fail", error=str(error), role="fallback_or_independent_check"))
    hyg = output.get("hyg", {}).get("value")
    ief = output.get("ief", {}).get("value")
    if finite(hyg) is not None and finite(ief) not in (None, 0):
        output["credit_risk_ratio"] = {
            "value": hyg / ief,
            "unit": "ratio",
            "as_of": min(output["hyg"]["as_of"], output["ief"]["as_of"]),
            "interpretation": "HYG/IEF 上升通常代表信用風險偏好改善；只作 OAS 失效時的市場代理",
        }
    return output, checks


def chart_values(name: str) -> tuple[list[tuple[datetime, float]], str]:
    url = f"https://api.blockchain.info/charts/{name}?timespan=180days&format=json"
    data = fetch_json(url)
    rows = [
        (datetime.fromtimestamp(item["x"], timezone.utc), value)
        for item in data.get("values", [])
        if (value := finite(item.get("y"))) is not None
    ]
    if len(rows) < 30:
        raise ValueError(f"Blockchain.com {name} history too short")
    return rows, url


def timed_series(rows: list[tuple[datetime, float]]) -> dict[str, Any]:
    latest_time, latest = rows[-1]
    target = latest_time - timedelta(days=30)
    prior = min(rows, key=lambda item: abs((item[0] - target).total_seconds()))
    change = latest / prior[1] - 1 if prior[1] else None
    return {"value": latest, "change_30d": change, "as_of": latest_time.date().isoformat()}


COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
COINMETRICS_METRICS = (
    "CapMVRVCur",
    "CapMrktCurUSD",
    "SplyCur",
    "TxCnt",
    "AdrActCnt",
    "IssTotUSD",
    "FlowInExUSD",
    "FlowOutExUSD",
    "HashRate",
)


def coinmetrics_series(asset: str, metrics: tuple[str, ...], days: int = 45) -> tuple[dict[str, list[tuple[str, float]]], str]:
    """Return {metric: [(date, value)]} sorted ascending from the free community API.

    The community tier needs no key. Preliminary values carry a ``<metric>-status``
    of ``flash``; that flag is preserved so downstream consumers never present a
    provisional exchange-flow reading as final.
    """
    start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    url = (
        f"{COINMETRICS_BASE}?assets={asset}&metrics={','.join(metrics)}"
        f"&frequency=1d&page_size=10000&start_time={start}"
    )
    payload = fetch_json(url)
    rows = payload.get("data", [])
    if not rows:
        raise ValueError(f"CoinMetrics {asset} returned no rows")
    series: dict[str, list[tuple[str, float]]] = {metric: [] for metric in metrics}
    flash: dict[str, str] = {}
    for row in sorted(rows, key=lambda item: str(item.get("time"))):
        as_of = str(row.get("time") or "")[:10]
        if not as_of:
            continue
        for metric in metrics:
            value = finite(row.get(metric))
            if value is not None:
                series[metric].append((as_of, value))
                if row.get(f"{metric}-status") == "flash":
                    flash[metric] = as_of
    series = {metric: values for metric, values in series.items() if values}
    if not series:
        raise ValueError(f"CoinMetrics {asset} returned no usable metric values")
    series["__flash__"] = flash  # type: ignore[assignment]
    return series, url


def coinmetrics_point(series: dict[str, Any], metric: str) -> dict[str, Any] | None:
    """Latest value plus its own 30-day change, keeping the observation date."""
    rows = series.get(metric)
    if not rows:
        return None
    as_of, value = rows[-1]
    latest_day = date.fromisoformat(as_of)
    target = latest_day - timedelta(days=30)
    prior = min(rows, key=lambda item: abs((date.fromisoformat(item[0]) - target).days))
    change = value / prior[1] - 1 if prior[1] else None
    point = {"value": value, "change_30d": change, "as_of": as_of}
    if metric in series.get("__flash__", {}):
        point["preliminary_as_of"] = series["__flash__"][metric]
    return point


def coinmetrics_valuation(series: dict[str, Any]) -> dict[str, Any]:
    """Valuation and flow block used by the author-thesis tracker.

    ``realized_price_usd`` is derived, not fetched: market cap / MVRV / supply.
    Keeping the derivation explicit means the verifier can recompute it.
    """
    mvrv = coinmetrics_point(series, "CapMVRVCur")
    market_cap = coinmetrics_point(series, "CapMrktCurUSD")
    supply = coinmetrics_point(series, "SplyCur")
    inflow = coinmetrics_point(series, "FlowInExUSD")
    outflow = coinmetrics_point(series, "FlowOutExUSD")
    issuance = coinmetrics_point(series, "IssTotUSD")
    realized_cap = (
        market_cap["value"] / mvrv["value"]
        if mvrv and market_cap and mvrv["value"] else None
    )
    realized_price = (
        realized_cap / supply["value"]
        if realized_cap is not None and supply and supply["value"] else None
    )
    net_flow = (
        inflow["value"] - outflow["value"]
        if inflow and outflow else None
    )
    block: dict[str, Any] = {
        "mvrv": mvrv,
        "market_cap_usd": market_cap,
        "supply": supply,
        "realized_cap_usd": realized_cap,
        "realized_price_usd": realized_price,
        "realized_price_basis": "market_cap_divided_by_mvrv_divided_by_supply",
        "exchange_inflow_usd": inflow,
        "exchange_outflow_usd": outflow,
        "exchange_net_flow_usd": net_flow,
        "exchange_net_flow_basis": "inflow_usd_minus_outflow_usd",
        "issuance_usd": issuance,
    }
    dates = [point["as_of"] for point in (mvrv, market_cap, supply) if point]
    block["as_of"] = min(dates) if dates else None
    flow_dates = [point["as_of"] for point in (inflow, outflow) if point]
    block["exchange_flow_as_of"] = min(flow_dates) if flow_dates else None
    block["exchange_flow_is_preliminary"] = any(
        point and point.get("preliminary_as_of") for point in (inflow, outflow)
    )
    return block


def fresher(candidate: dict[str, Any] | None, incumbent: dict[str, Any] | None) -> bool:
    """True when ``candidate`` carries a strictly newer observation date."""
    if not candidate or not candidate.get("as_of"):
        return False
    if not incumbent or not incumbent.get("as_of"):
        return True
    return str(candidate["as_of"]) > str(incumbent["as_of"])


def collect_btc_onchain() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    names = {"transactions": "n-transactions", "active_addresses": "n-unique-addresses", "hashrate": "hash-rate"}
    output: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(chart_values, chart): (name, chart) for name, chart in names.items()}
        for future in as_completed(future_map):
            name, chart = future_map[future]
            try:
                rows, url = future.result()
                output[name] = {**timed_series(rows), "url": url}
                checks.append(source_check("onchain_btc", f"Blockchain.com {chart}", url, "pass", as_of=output[name]["as_of"]))
            except Exception as error:
                checks.append(source_check("onchain_btc", f"Blockchain.com {chart}", "https://www.blockchain.com/explorer/charts", "fail", error=str(error)))

    try:
        series, coinmetrics_url = coinmetrics_series("btc", COINMETRICS_METRICS)
        candidates = {
            "transactions": coinmetrics_point(series, "TxCnt"),
            "active_addresses": coinmetrics_point(series, "AdrActCnt"),
            "hashrate": coinmetrics_point(series, "HashRate"),
        }
        output["coinmetrics"] = {name: point for name, point in candidates.items() if point}
        output["valuation"] = coinmetrics_valuation(series)
        # Blockchain.com's chart API now lags 3+ days; whichever provider carries the
        # newer observation date becomes canonical and the other stays as the check.
        promoted = []
        for name, point in candidates.items():
            if fresher(point, output.get(name)):
                output.setdefault("blockchain_com", {})[name] = output.get(name)
                output[name] = {**point, "url": coinmetrics_url}
                promoted.append(name)
        output["canonical_onchain_provider"] = "CoinMetrics" if promoted else "Blockchain.com"
        output["canonical_promoted_series"] = promoted
        checks.append(source_check(
            "onchain_btc", "CoinMetrics community", coinmetrics_url, "pass",
            as_of=output["valuation"].get("as_of"),
            role="primary" if promoted else "independent_check",
        ))
    except Exception as error:
        output["canonical_onchain_provider"] = "Blockchain.com"
        output["canonical_promoted_series"] = []
        checks.append(source_check(
            "onchain_btc", "CoinMetrics community", COINMETRICS_BASE, "fail",
            error=str(error), role="primary_or_independent_check",
        ))

    blockchair_url = "https://api.blockchair.com/bitcoin/stats"
    try:
        stats = fetch_json(blockchair_url).get("data", {})
        tx_24h = finite(stats.get("transactions_24h"))
        output["blockchair_transactions_24h"] = tx_24h
        output["blockchair_as_of"] = str(stats.get("best_block_time") or "")[:10] or None
        checks.append(source_check("onchain_btc", "Blockchair Bitcoin", blockchair_url, "pass", as_of=output["blockchair_as_of"], role="independent_check"))
    except Exception as error:
        checks.append(source_check("onchain_btc", "Blockchair Bitcoin", blockchair_url, "fail", error=str(error), role="independent_check"))

    mempool_url = "https://mempool.space/api/v1/mining/hashrate/3m"
    try:
        data = fetch_json(mempool_url)
        values = [item for item in data.get("hashrates", []) if finite(item.get("avgHashrate")) is not None]
        latest = max(values, key=lambda item: item["timestamp"])
        output["mempool_hashrate_ths"] = finite(latest["avgHashrate"]) / 1e12
        output["mempool_hashrate_as_of"] = datetime.fromtimestamp(latest["timestamp"], timezone.utc).date().isoformat()
        checks.append(source_check("onchain_btc", "mempool.space", mempool_url, "pass", as_of=output["mempool_hashrate_as_of"], role="independent_check"))
    except Exception as error:
        checks.append(source_check("onchain_btc", "mempool.space", mempool_url, "fail", error=str(error), role="independent_check"))

    tx = finite(output.get("transactions", {}).get("value"))
    tx_check = finite(output.get("blockchair_transactions_24h"))
    output["transactions_cross_source_gap"] = abs(tx - tx_check) / max(tx, tx_check) if tx and tx_check else None
    hashrate = finite(output.get("hashrate", {}).get("value"))
    hash_check = finite(output.get("mempool_hashrate_ths"))
    output["hashrate_cross_source_gap"] = abs(hashrate - hash_check) / max(hashrate, hash_check) if hashrate and hash_check else None
    output["source_count"] = sum(item["status"] == "pass" for item in checks)
    output["status"] = "pass" if output.get("transactions_cross_source_gap") is not None and output.get("hashrate_cross_source_gap") is not None else "degraded"
    return output, checks


def rpc_call(url: str, payload: Any) -> Any:
    return fetch_json(url, data=payload, timeout=45)


def rpc_head(url: str) -> int:
    result = rpc_call(url, {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1})
    return int(result["result"], 16)


def rpc_blocks(url: str, heights: list[int]) -> dict[int, dict[str, Any]]:
    blocks: dict[int, dict[str, Any]] = {}
    for offset in range(0, len(heights), 5):
        chunk = heights[offset:offset + 5]
        payload = [
            {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [hex(height), False], "id": index}
            for index, height in enumerate(chunk)
        ]
        response = rpc_call(url, payload)
        if not isinstance(response, list):
            continue
        by_id = {item["id"]: item.get("result") for item in response if isinstance(item, dict)}
        for index, height in enumerate(chunk):
            block = by_id.get(index)
            if block:
                blocks[height] = block
    return blocks


def summarize_eth_blocks(blocks: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not blocks:
        raise ValueError("Ethereum RPC returned no sampled blocks")
    tx_counts = [len(item.get("transactions", [])) for item in blocks.values()]
    gas_ratios = [int(item["gasUsed"], 16) / int(item["gasLimit"], 16) for item in blocks.values() if int(item["gasLimit"], 16)]
    latest = max(blocks.values(), key=lambda item: int(item["number"], 16))
    return {
        "sample_blocks": len(blocks),
        "average_transactions_per_block": statistics.fmean(tx_counts),
        "average_gas_utilization": statistics.fmean(gas_ratios),
        "as_of": datetime.fromtimestamp(int(latest["timestamp"], 16), timezone.utc).isoformat(),
    }


def collect_eth_onchain() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    providers = {
        "PublicNode": "https://ethereum-rpc.publicnode.com",
        "1RPC": "https://1rpc.io/eth",
        "Flashbots": "https://rpc.flashbots.net",
        "LlamaRPC": "https://eth.llamarpc.com",
        "MEVBlocker": "https://rpc.mevblocker.io",
    }
    heads: dict[str, int] = {}
    checks: list[dict[str, Any]] = []
    for index, (name, url) in enumerate(providers.items()):
        try:
            heads[name] = rpc_head(url)
            checks.append(source_check("onchain_eth", name, url, "pass", role="primary" if index == 0 else "independent_check"))
        except Exception as error:
            checks.append(source_check("onchain_eth", name, url, "fail", error=str(error), role="primary_or_failover"))
    output: dict[str, Any] = {"provider_heads": heads, "source_count": len(heads)}
    primary_name: str | None = None
    current: dict[int, dict[str, Any]] = {}
    prior: dict[int, dict[str, Any]] = {}
    if heads:
        head = min(heads.values())
        current_heights = [head - index * 600 for index in range(12)]
        prior_heights = [head - 50_400 - index * 600 for index in range(12)]
        for candidate in heads:
            try:
                candidate_current = rpc_blocks(providers[candidate], current_heights)
                candidate_prior = rpc_blocks(providers[candidate], prior_heights)
                if len(candidate_current) < 10 or len(candidate_prior) < 10:
                    raise ValueError("Ethereum RPC sample depth below 10 blocks")
                primary_name = candidate
                current = candidate_current
                prior = candidate_prior
                checks.append(source_check("onchain_eth", f"{candidate} block sample", providers[candidate], "pass", role="sample_primary"))
                break
            except Exception as error:
                checks.append(source_check("onchain_eth", f"{candidate} block sample", providers[candidate], "fail", error=str(error), role="sample_failover"))
    if primary_name:
        current_summary = summarize_eth_blocks(current)
        prior_summary = summarize_eth_blocks(prior)
        output.update({
            "head_block": head,
            "head_gap_blocks": max(heads.values()) - min(heads.values()),
            "current_sample": current_summary,
            "prior_week_sample": prior_summary,
            "transactions_per_block_7d_change": current_summary["average_transactions_per_block"] / prior_summary["average_transactions_per_block"] - 1 if prior_summary["average_transactions_per_block"] else None,
            "gas_utilization_7d_change": current_summary["average_gas_utilization"] - prior_summary["average_gas_utilization"],
            "as_of": current_summary["as_of"],
        })
        backup: dict[int, dict[str, Any]] = {}
        backup_name: str | None = None
        for candidate in heads:
            if candidate == primary_name:
                continue
            try:
                candidate_blocks = rpc_blocks(providers[candidate], current_heights)
                if len(candidate_blocks) < 10:
                    raise ValueError("Ethereum RPC independent sample depth below 10 blocks")
                backup = candidate_blocks
                backup_name = candidate
                checks.append(source_check("onchain_eth", f"{candidate} block sample", providers[candidate], "pass", role="sample_independent_check"))
                break
            except Exception as error:
                checks.append(source_check("onchain_eth", f"{candidate} block sample", providers[candidate], "fail", error=str(error), role="sample_failover"))
        if backup_name:
            common = sorted(set(current) & set(backup))
            output["sample_check_provider"] = backup_name
            output["sample_agreement"] = len(common) >= 10 and all(
                current[height].get("hash") == backup[height].get("hash")
                and len(current[height].get("transactions", [])) == len(backup[height].get("transactions", []))
                for height in common
            )
        else:
            output["sample_agreement"] = None
    if not primary_name:
        blockchair_url = "https://api.blockchair.com/ethereum/stats"
        try:
            stats = fetch_json(blockchair_url).get("data", {})
            output.update({
                "head_block": stats.get("best_block_height"),
                "transactions_24h": finite(stats.get("transactions_24h")),
                "as_of": stats.get("best_block_time"),
                "fallback": "Blockchair",
            })
            checks.append(source_check("onchain_eth", "Blockchair Ethereum", blockchair_url, "pass", as_of=str(stats.get("best_block_time") or "")[:10], role="fallback"))
        except Exception as error:
            checks.append(source_check("onchain_eth", "Blockchair Ethereum", blockchair_url, "fail", error=str(error), role="fallback"))
    try:
        series, coinmetrics_url = coinmetrics_series("eth", COINMETRICS_METRICS)
        output["valuation"] = coinmetrics_valuation(series)
        output["coinmetrics"] = {
            name: point
            for name, point in (
                ("transactions", coinmetrics_point(series, "TxCnt")),
                ("active_addresses", coinmetrics_point(series, "AdrActCnt")),
            )
            if point
        }
        checks.append(source_check(
            "onchain_eth", "CoinMetrics community", coinmetrics_url, "pass",
            as_of=output["valuation"].get("as_of"), role="independent_check",
        ))
    except Exception as error:
        checks.append(source_check(
            "onchain_eth", "CoinMetrics community", COINMETRICS_BASE, "fail",
            error=str(error), role="independent_check",
        ))
    output["status"] = "pass" if output.get("sample_agreement") is True and output.get("head_gap_blocks", 99) <= 3 else "degraded" if output.get("head_block") else "fail"
    return output, checks


def parse_rss(url: str, provider: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = ET.fromstring(fetch_text(url))
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        description = strip_tags(item.findtext("description") or "")
        text = f"{title} {description}".lower()
        matched = [term for term in POLICY_TERMS if term in text]
        if not matched:
            continue
        published_raw = item.findtext("pubDate") or item.findtext("date")
        try:
            published = parsedate_to_datetime(published_raw).astimezone(timezone.utc).isoformat() if published_raw else None
        except (TypeError, ValueError):
            published = None
        items.append({
            "provider": provider,
            "title": title,
            "summary": description[:500],
            "published_at": published,
            "url": (item.findtext("link") or "").strip(),
            "matched_terms": matched,
            "source_type": "official_feed",
        })
    return items, source_check("policy", provider, url, "pass", as_of=iso_now())


def collect_federal_register() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    urls: list[str] = []
    for term in ("digital asset", "cryptocurrency", "stablecoin"):
        url = "https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(
            {"per_page": 20, "order": "newest", "conditions[term]": term}
        )
        urls.append(url)
        for item in fetch_json(url).get("results", []):
            title = str(item.get("title") or "")
            abstract = strip_tags(str(item.get("abstract") or ""))
            text = f"{title} {abstract}".lower()
            matched = [candidate for candidate in POLICY_TERMS if candidate in text]
            if matched:
                events.append({
                    "provider": "Federal Register",
                    "title": title,
                    "summary": abstract[:500],
                    "published_at": item.get("publication_date"),
                    "url": item.get("html_url"),
                    "matched_terms": matched,
                    "source_type": "official_rulemaking",
                })
    return events, source_check("policy", "Federal Register", urls[0], "pass", as_of=iso_now())


def collect_congress() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = "https://api.congress.gov/v3/bill/119?" + urllib.parse.urlencode(
        {"format": "json", "limit": 250, "sort": "updateDate desc", "api_key": "DEMO_KEY"},
        quote_via=urllib.parse.quote,
    )
    events: list[dict[str, Any]] = []
    for item in fetch_json(url).get("bills", []):
        title = str(item.get("title") or "")
        action = str((item.get("latestAction") or {}).get("text") or "")
        text = f"{title} {action}".lower()
        matched = [term for term in POLICY_TERMS if term in text]
        if not matched:
            continue
        congress = item.get("congress")
        bill_type = str(item.get("type") or "").lower()
        number = item.get("number")
        events.append({
            "provider": "Congress.gov",
            "title": title,
            "summary": action[:500],
            "published_at": item.get("updateDate"),
            "url": f"https://www.congress.gov/bill/{congress}th-congress/{bill_type}/{number}",
            "matched_terms": matched,
            "source_type": "official_legislation",
        })
    return events, source_check("policy", "Congress.gov", url, "pass", as_of=iso_now())


def collect_policy() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    collectors = [
        (collect_federal_register, None),
        (collect_congress, None),
        (parse_rss, ("https://www.sec.gov/news/pressreleases.rss", "SEC")),
        (parse_rss, ("https://www.federalreserve.gov/feeds/press_all.xml", "Federal Reserve")),
        (parse_rss, ("https://www.whitehouse.gov/presidential-actions/feed/", "White House")),
    ]
    events: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(func, *args) if args else executor.submit(func) for func, args in collectors]
        for future in as_completed(futures):
            try:
                items, check = future.result()
                events.extend(items)
                checks.append(check)
            except Exception as error:
                checks.append(source_check("policy", "policy source", "", "fail", error=str(error), role="primary_or_failover"))
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in events:
        key = (str(item.get("provider")), str(item.get("url") or item.get("title")))
        unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: str(item.get("published_at") or ""), reverse=True)[:40]
    cutoff_7 = now_utc().date() - timedelta(days=7)
    cutoff_30 = now_utc().date() - timedelta(days=30)

    def event_date(item: dict[str, Any]) -> date | None:
        try:
            return datetime.fromisoformat(str(item.get("published_at") or "").replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(str(item.get("published_at"))[:10])
            except ValueError:
                return None

    success = sum(item["status"] == "pass" for item in checks)
    return {
        "events": ordered,
        "event_count_7d": sum((day := event_date(item)) is not None and day >= cutoff_7 for item in ordered),
        "event_count_30d": sum((day := event_date(item)) is not None and day >= cutoff_30 for item in ordered),
        "successful_sources": success,
        "checked_sources": len(checks),
        "status": "pass" if success >= 3 else "degraded" if success >= 1 else "fail",
        "no_event_policy": "成功查詢但零事件代表近期未命中，不等同監管風險消失",
    }, checks


def collect_macro() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fred, checks = collect_fred()
    direct: dict[str, Any] = {}
    for name, collector, provider, url in (
        ("h41_assets", collect_h41_assets, "Federal Reserve H.4.1", "https://www.federalreserve.gov/releases/h41/current/h41.htm"),
        ("rrp", collect_nyfed_rrp, "New York Fed RRP", "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json"),
        ("tga", collect_tga, "U.S. Treasury Fiscal Data", "https://api.fiscaldata.treasury.gov"),
        ("treasury_curve", collect_treasury_curve, "U.S. Treasury", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"),
    ):
        try:
            direct[name], check = collector()
            checks.append(check)
        except Exception as error:
            checks.append(source_check("macro", provider, url, "fail", error=str(error), role="primary_or_independent_check"))
    proxies, proxy_checks = collect_market_proxies()
    checks.extend(proxy_checks)

    fed_assets = finite(fred.get("fed_assets", {}).get("value"))
    h41_assets = finite(direct.get("h41_assets", {}).get("value"))
    tga = finite(direct.get("tga", {}).get("value"))
    rrp = finite(direct.get("rrp", {}).get("value"))
    prior_fed_assets = finite(fred.get("fed_assets", {}).get("prior_30d_value"))
    prior_fed_assets_as_of = fred.get("fed_assets", {}).get("prior_30d_as_of")
    prior_tga = finite(direct.get("tga", {}).get("prior_30d_value"))
    prior_rrp = finite(direct.get("rrp", {}).get("prior_30d_value"))
    prior_net = prior_fed_assets - prior_tga - prior_rrp / 1_000_000 if None not in {prior_fed_assets, prior_tga, prior_rrp} else None
    current_net = fed_assets - tga - rrp / 1_000_000 if fed_assets is not None and tga is not None and rrp is not None else None
    m2 = fred.get("m2_money_stock", {})
    m2_value = finite(m2.get("value"))
    m2_yoy = finite(m2.get("change_365d"))
    m2_90d = finite(m2.get("change_90d"))
    m2_3m_annualized = (1 + m2_90d) ** 4 - 1 if m2_90d is not None and m2_90d > -1 else None
    reserves = fred.get("reserve_balances", {})
    reserve_value = finite(reserves.get("value"))
    reserve_30d = finite(reserves.get("change_30d"))
    net_30d = current_net / prior_net - 1 if current_net is not None and prior_net not in (None, 0) else None

    def liquidity_vote(value: float | None, threshold: float = 0.01) -> str:
        if value is None or abs(value) < threshold:
            return "neutral"
        return "positive" if value > 0 else "negative"

    liquidity_votes = {
        "fed_net_liquidity_30d": liquidity_vote(net_30d),
        "bank_reserve_balances_30d": liquidity_vote(reserve_30d),
        "m2_money_stock_yoy": liquidity_vote(m2_yoy),
    }
    positive_votes = sum(value == "positive" for value in liquidity_votes.values())
    negative_votes = sum(value == "negative" for value in liquidity_votes.values())
    neutral_votes = sum(value == "neutral" for value in liquidity_votes.values())
    if positive_votes >= 2 and negative_votes == 0:
        liquidity_state = "擴張共振"
    elif negative_votes >= 2 and positive_votes == 0:
        liquidity_state = "收縮共振"
    else:
        liquidity_state = "不同頻率分歧"
    liquidity = {
        "fed_assets_million_usd": fed_assets,
        "fed_assets_as_of": fred.get("fed_assets", {}).get("as_of"),
        "h41_assets_million_usd": h41_assets,
        "h41_assets_as_of": direct.get("h41_assets", {}).get("as_of"),
        "h41_release_date": direct.get("h41_assets", {}).get("release_date"),
        "fed_assets_cross_source_gap": abs(fed_assets - h41_assets) / max(fed_assets, h41_assets) if fed_assets and h41_assets else None,
        "tga_million_usd": tga,
        "tga_as_of": direct.get("tga", {}).get("as_of"),
        "rrp_usd": rrp,
        "rrp_as_of": direct.get("rrp", {}).get("as_of"),
        "net_liquidity_million_usd": current_net,
        "net_liquidity_30d_change": net_30d,
        "prior_net_liquidity_million_usd": prior_net,
        "prior_net_liquidity_as_of": min(filter(None, [prior_fed_assets_as_of, direct.get("tga", {}).get("prior_30d_as_of"), direct.get("rrp", {}).get("prior_30d_as_of")]), default=None),
        "prior_component_as_of": {
            "fed_assets": prior_fed_assets_as_of,
            "tga": direct.get("tga", {}).get("prior_30d_as_of"),
            "rrp": direct.get("rrp", {}).get("prior_30d_as_of"),
        },
        "as_of": min(filter(None, [fred.get("fed_assets", {}).get("as_of"), direct.get("tga", {}).get("as_of"), direct.get("rrp", {}).get("as_of")]), default=None),
        "formula": "Fed total assets (million USD) - TGA closing balance (million USD) - ON RRP accepted amount converted to million USD",
        "m2_money_stock_billion_usd": m2_value,
        "m2_money_stock_as_of": m2.get("as_of"),
        "m2_money_stock_yoy_change": m2_yoy,
        "m2_money_stock_3m_annualized_change": m2_3m_annualized,
        "m2_money_stock_prior_365d_value": finite(m2.get("prior_365d_value")),
        "m2_money_stock_prior_90d_value": finite(m2.get("prior_90d_value")),
        "reserve_balances_million_usd": reserve_value,
        "reserve_balances_as_of": reserves.get("as_of"),
        "reserve_balances_30d_change": reserve_30d,
        "reserve_balances_prior_30d_value": finite(reserves.get("prior_30d_value")),
        "dollar_liquidity_resonance": {
            "state": liquidity_state,
            "positive_votes": positive_votes,
            "negative_votes": negative_votes,
            "neutral_votes": neutral_votes,
            "components": liquidity_votes,
            "method": "Fed 淨流動性 30 日、銀行準備金 30 日與 M2 年增各一票；只判方向共振，不混成黑箱指數",
        },
    }
    curve = direct.get("treasury_curve", {})
    direct_treasury_2y = finite(curve.get("treasury_2y"))
    direct_treasury_10y = finite(curve.get("treasury_10y"))
    direct_treasury_30y = finite(curve.get("treasury_30y"))
    fred_treasury_2y = finite(fred.get("treasury_2y", {}).get("value"))
    fred_treasury_10y = finite(fred.get("treasury_10y", {}).get("value"))
    fred_treasury_30y = finite(fred.get("treasury_30y", {}).get("value"))
    treasury_2y = direct_treasury_2y if direct_treasury_2y is not None else fred_treasury_2y
    treasury_10y = direct_treasury_10y if direct_treasury_10y is not None else fred_treasury_10y
    treasury_30y = direct_treasury_30y if direct_treasury_30y is not None else fred_treasury_30y
    rates = {
        "fed_funds_pct": finite(fred.get("fed_funds", {}).get("value")),
        "fed_funds_as_of": fred.get("fed_funds", {}).get("as_of"),
        "treasury_2y_pct": treasury_2y,
        "treasury_10y_pct": treasury_10y,
        "treasury_30y_pct": treasury_30y,
        "curve_2s10s_pp": treasury_10y - treasury_2y if treasury_10y is not None and treasury_2y is not None else None,
        "curve_10s30s_pp": treasury_30y - treasury_10y if treasury_30y is not None and treasury_10y is not None else None,
        "as_of": curve.get("as_of") or fred.get("treasury_10y", {}).get("as_of"),
        "canonical_provider": "U.S. Treasury" if direct_treasury_10y is not None else "FRED fallback",
        "direct_values": {"treasury_2y_pct": direct_treasury_2y, "treasury_10y_pct": direct_treasury_10y, "treasury_30y_pct": direct_treasury_30y, "as_of": curve.get("as_of")},
        "fred_values": {"treasury_2y_pct": fred_treasury_2y, "treasury_10y_pct": fred_treasury_10y, "treasury_30y_pct": fred_treasury_30y, "as_of": fred.get("treasury_10y", {}).get("as_of")},
        "direct_fred_10y_gap_pp": abs(direct_treasury_10y - fred_treasury_10y) if direct_treasury_10y is not None and fred_treasury_10y is not None else None,
    }
    credit = {
        "high_yield_oas_pct": finite(fred.get("high_yield_oas", {}).get("value")),
        "high_yield_oas_30d_change_pp": finite(fred.get("high_yield_oas", {}).get("change_30d")),
        "investment_grade_oas_pct": finite(fred.get("investment_grade_oas", {}).get("value")),
        "investment_grade_oas_30d_change_pp": finite(fred.get("investment_grade_oas", {}).get("change_30d")),
        "as_of": min(filter(None, [fred.get("high_yield_oas", {}).get("as_of"), fred.get("investment_grade_oas", {}).get("as_of")]), default=None),
        "fallback_proxy": proxies.get("credit_risk_ratio"),
    }
    oil = {
        "wti_spot_usd": finite(fred.get("wti_spot", {}).get("value")),
        "wti_spot_30d_change": finite(fred.get("wti_spot", {}).get("change_30d")),
        "wti_spot_as_of": fred.get("wti_spot", {}).get("as_of"),
        "wti_future_proxy_usd": finite(proxies.get("oil_future", {}).get("value")),
        "wti_future_proxy_30d_change": finite(proxies.get("oil_future", {}).get("change_30d")),
        "wti_future_proxy_as_of": proxies.get("oil_future", {}).get("as_of"),
    }
    equities = {
        "sp500": fred.get("sp500") or proxies.get("sp500"),
        "sp500_provider": "FRED SP500" if fred.get("sp500") else "Yahoo ^GSPC fallback",
        "sp500_independent_check": proxies.get("sp500") if fred.get("sp500") else None,
        "nasdaq": fred.get("nasdaq") or proxies.get("nasdaq"),
        "nasdaq_provider": "FRED NASDAQCOM" if fred.get("nasdaq") else "Yahoo ^IXIC fallback",
        "nasdaq_independent_check": proxies.get("nasdaq") if fred.get("nasdaq") else None,
        "vix": fred.get("vix") or proxies.get("vix"),
        "vix_provider": "FRED VIXCLS" if fred.get("vix") else "Yahoo ^VIX fallback",
        "vix_independent_check": proxies.get("vix") if fred.get("vix") else None,
        "broad_dollar": fred.get("broad_dollar"),
    }
    independent_pairs = (
        liquidity["fed_assets_cross_source_gap"] is not None,
        rates["direct_fred_10y_gap_pp"] is not None,
        equities["sp500"] is not None and equities["sp500_independent_check"] is not None,
        equities["nasdaq"] is not None and equities["nasdaq_independent_check"] is not None,
    )
    status = "pass" if liquidity["net_liquidity_million_usd"] is not None and liquidity["m2_money_stock_yoy_change"] is not None and liquidity["reserve_balances_30d_change"] is not None and rates["treasury_10y_pct"] is not None and equities["sp500"] and all(independent_pairs) else "degraded"
    return {
        "status": status,
        "liquidity": liquidity,
        "rates": rates,
        "credit": credit,
        "oil": oil,
        "equities": equities,
        "fred": fred,
        "market_proxies": proxies,
    }, checks


def main() -> int:
    generated_at = iso_now()
    results: dict[str, Any] = {}
    all_checks: list[dict[str, Any]] = []
    tasks = {"macro": collect_macro, "policy": collect_policy, "btc_onchain": collect_btc_onchain, "eth_onchain": collect_eth_onchain}
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {executor.submit(collector): name for name, collector in tasks.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                results[name], checks = future.result()
                all_checks.extend(checks)
            except Exception as error:
                results[name] = {"status": "fail", "error": str(error)}
                all_checks.append(source_check(name, name, "", "fail", error=str(error)))

    groups = {
        "macro": results.get("macro", {}).get("status", "fail"),
        "policy": results.get("policy", {}).get("status", "fail"),
        "onchain_btc": results.get("btc_onchain", {}).get("status", "fail"),
        "onchain_eth": results.get("eth_onchain", {}).get("status", "fail"),
    }
    usable = sum(status in {"pass", "degraded"} for status in groups.values())
    quality_status = "pass" if all(status == "pass" for status in groups.values()) else "degraded" if usable >= 3 else "fail"
    output = {
        "schema": 1,
        "date": now_utc().date().isoformat(),
        "generated_at": generated_at,
        "quality": {
            "status": quality_status,
            "publication_mode": "analysis_only" if quality_status != "fail" else "diagnostics_only",
            "execution_gate_eligible": False,
            "group_status": groups,
            "successful_sources": sum(item["status"] == "pass" for item in all_checks),
            "failed_sources": sum(item["status"] == "fail" for item in all_checks),
            "policy": "單一來源失效先切換同欄位備援或同研究桌替代代理；保留原始 as_of，不以抓取時間冒充觀測時間。",
        },
        "macro": results.get("macro", {}),
        "policy": results.get("policy", {}),
        "onchain": {"BTC": results.get("btc_onchain", {}), "ETH": results.get("eth_onchain", {})},
        "source_checks": sorted(all_checks, key=lambda item: (item["desk"], item["provider"])),
    }
    output["payload_hash"] = canonical_hash(artifact_payload(output))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT_PATH), "status": quality_status, "groups": groups, "sources": len(all_checks)}, ensure_ascii=False))
    return 1 if quality_status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
