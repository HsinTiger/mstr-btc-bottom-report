#!/usr/bin/env python3
"""Collect completed daily bars for multi-horizon market analysis."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
OUTPUT_PATH = DATA_DIR / "timescale_price_history.json"

ASSETS = {
    "BTC": {"yahoo": "BTC-USD", "secondary": ("Kraken", "XBTUSD"), "market": "crypto"},
    "ETH": {"yahoo": "ETH-USD", "secondary": ("Kraken", "ETHUSD"), "market": "crypto"},
    "MSTR": {"yahoo": "MSTR", "secondary": ("Nasdaq", "MSTR"), "market": "equity"},
    "BMNR": {"yahoo": "BMNR", "secondary": ("Nasdaq", "BMNR"), "market": "equity"},
    "STRC": {"yahoo": "STRC", "secondary": ("Nasdaq", "STRC"), "market": "equity"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def completed_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def yahoo_rows(ticker: str) -> tuple[list[dict[str, Any]], str]:
    encoded = urllib.parse.quote(ticker)
    history_range = "8y" if ticker in {"BTC-USD", "ETH-USD"} else "2y"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={history_range}&interval=1d"
    payload = fetch_json(url)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    today = datetime.now(timezone.utc).date().isoformat()
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        date = completed_date(timestamp)
        if date >= today:
            continue
        def quote_value(field: str) -> Any:
            values = quote.get(field) or []
            return values[index] if index < len(values) else None

        close = number(quote_value("close"))
        if close is None or close <= 0:
            continue
        rows.append({
            "date": date,
            "open": number(quote_value("open")),
            "high": number(quote_value("high")),
            "low": number(quote_value("low")),
            "close": close,
            "volume": number(quote_value("volume")),
        })
    rows.sort(key=lambda item: item["date"])
    return rows, url


def kraken_rows(pair: str) -> tuple[list[dict[str, Any]], str]:
    url = f"https://api.kraken.com/0/public/OHLC?{urllib.parse.urlencode({'pair': pair, 'interval': 1440})}"
    payload = fetch_json(url)
    if payload.get("error"):
        raise ValueError(f"Kraken {pair}: {payload['error']}")
    result = payload.get("result") or {}
    key = next(key for key in result if key != "last")
    today = datetime.now(timezone.utc).date().isoformat()
    rows = []
    for raw in result[key]:
        date = completed_date(int(raw[0]))
        if date >= today:
            continue
        close = number(raw[4])
        if close is None or close <= 0:
            continue
        rows.append({
            "date": date,
            "open": number(raw[1]),
            "high": number(raw[2]),
            "low": number(raw[3]),
            "close": close,
            "volume": number(raw[6]),
        })
    rows.sort(key=lambda item: item["date"])
    return rows, url


def coinbase_rows(product: str) -> tuple[list[dict[str, Any]], str]:
    url = f"https://api.exchange.coinbase.com/products/{urllib.parse.quote(product)}/candles?granularity=86400"
    payload = fetch_json(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    today = datetime.now(timezone.utc).date().isoformat()
    rows = []
    for raw in payload:
        date = completed_date(int(raw[0]))
        if date >= today:
            continue
        close = number(raw[4])
        if close is None or close <= 0:
            continue
        rows.append({
            "date": date,
            "open": number(raw[3]),
            "high": number(raw[2]),
            "low": number(raw[1]),
            "close": close,
            "volume": number(raw[5]),
        })
    rows.sort(key=lambda item: item["date"])
    return rows, url


def nasdaq_rows(ticker: str) -> tuple[list[dict[str, Any]], str]:
    today = datetime.now(timezone.utc).date()
    query = urllib.parse.urlencode({
        "assetclass": "stocks",
        "fromdate": (today - timedelta(days=735)).isoformat(),
        "todate": today.isoformat(),
        "limit": 5000,
    })
    url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(ticker)}/historical?{query}"
    payload = fetch_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/historical",
        },
    )
    if (payload.get("status") or {}).get("rCode") != 200:
        raise ValueError(f"Nasdaq {ticker}: {payload.get('status')}")
    raw_rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        date = datetime.strptime(raw["date"], "%m/%d/%Y").date().isoformat()
        if date >= today.isoformat():
            continue
        close = number(raw.get("close"))
        if close is None or close <= 0:
            continue
        rows.append({
            "date": date,
            "open": number(raw.get("open")),
            "high": number(raw.get("high")),
            "low": number(raw.get("low")),
            "close": close,
            "volume": number(raw.get("volume")),
        })
    rows.sort(key=lambda item: item["date"])
    return rows, url


def collect_source(provider: str, symbol: str) -> tuple[list[dict[str, Any]], str]:
    collectors: dict[str, Callable[[str], tuple[list[dict[str, Any]], str]]] = {
        "Kraken": kraken_rows,
        "Coinbase": coinbase_rows,
        "Nasdaq": nasdaq_rows,
    }
    return collectors[provider](symbol)


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    generated_at = now_iso()
    incidents: list[str] = []
    assets: dict[str, Any] = {}
    for symbol, config in ASSETS.items():
        sources: dict[str, Any] = {}
        source_specs = [("Yahoo Finance", config["yahoo"]), config["secondary"]]
        if symbol in {"BTC", "ETH"}:
            source_specs.append(("Coinbase", f"{symbol}-USD"))
        for provider, provider_symbol in source_specs:
            try:
                rows, url = yahoo_rows(provider_symbol) if provider == "Yahoo Finance" else collect_source(provider, provider_symbol)
                if not rows:
                    raise ValueError("no completed bars")
                sources[provider] = {
                    "ticker": provider_symbol,
                    "url": url,
                    "as_of": rows[-1]["date"],
                    "bars": len(rows),
                    "rows": rows,
                }
            except Exception as error:
                incidents.append(f"{symbol} {provider}: {type(error).__name__}: {error}")
        canonical_provider = "Yahoo Finance" if "Yahoo Finance" in sources else next(iter(sources), None)
        assets[symbol] = {
            "market": config["market"],
            "canonical_provider": canonical_provider,
            "source_count": len(sources),
            "sources": sources,
        }
    missing_canonical = [symbol for symbol, item in assets.items() if not item.get("canonical_provider")]
    insufficient_sources = [symbol for symbol, item in assets.items() if item["source_count"] < 2]
    status = "fail" if missing_canonical else "degraded" if insufficient_sources else "pass"
    artifact = {
        "schema": 1,
        "date": snapshot.get("date"),
        "generated_at": generated_at,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "source_batch_id": snapshot.get("batch_id"),
        "quality": {
            "status": status,
            "failures": [f"missing canonical series: {', '.join(missing_canonical)}"] if missing_canonical else [],
            "degradations": [f"{symbol}: fewer than two independent sources" for symbol in insufficient_sources],
            "source_incidents": incidents,
            "policy": {
                "completed_bars_only": True,
                "canonical_preference": "Yahoo Finance, then verified secondary provider",
                "history_window": "BTC/ETH eight years; listed vehicles approximately two years",
                "minimum_independent_sources": 2,
                "research_only": True,
            },
        },
        "assets": assets,
    }
    write_json(OUTPUT_PATH, artifact)
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "status": status,
        "assets": len(assets),
        "source_incidents": len(incidents),
    }, ensure_ascii=False))
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
