#!/usr/bin/env python3
"""Verify four-horizon analysis math, lineage, scope, and append-only history."""

from __future__ import annotations

import json
import math
import statistics
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
ANALYSIS_PATH = DATA_DIR / "timescale_intelligence.json"
HISTORY_PATH = DATA_DIR / "timescale_intelligence_history.json"
PRICE_HISTORY_PATH = DATA_DIR / "timescale_price_history.json"
DATA_VERIFICATION_PATH = DATA_DIR / "timescale_data_verification.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
MARKET_VERIFICATION_PATH = DATA_DIR / "market_universe_verification.json"
CONTEXT_PATH = DATA_DIR / "market_context.json"
CONTEXT_VERIFICATION_PATH = DATA_DIR / "market_context_verification.json"
OUTPUT_PATH = DATA_DIR / "timescale_intelligence_verification.json"

HORIZON_BARS = {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63}
ASSETS = ("BTC", "ETH", "MSTR", "BMNR", "STRC")
PROHIBITED_ACTION_PHRASES = ("建議買進", "建議賣出", "應加碼", "應減碼", "開槓桿", "目標價為")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def expected_return(rows: list[dict[str, Any]], bars: int) -> float | None:
    if len(rows) <= bars:
        return None
    current = number(rows[-1].get("close"))
    previous = number(rows[-bars - 1].get("close"))
    return current / previous - 1 if current is not None and previous not in (None, 0) else None


def close_enough(first: Any, second: Any, tolerance: float = 1e-10) -> bool:
    first_number = number(first)
    second_number = number(second)
    if first_number is None or second_number is None:
        return first_number is None and second_number is None
    return abs(first_number - second_number) <= tolerance * max(1.0, abs(first_number), abs(second_number))


def age_hours(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    except (TypeError, ValueError):
        return float("inf")


def aggregate_completed(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    parsed = []
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row.get("date")))
        except (TypeError, ValueError):
            continue
        if number(row.get("close")) not in (None, 0):
            parsed.append((row_date, row))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return []
    latest = parsed[-1][0]
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    for row_date, row in parsed:
        if timeframe == "weekly":
            start = row_date - timedelta(days=row_date.weekday())
            end = start + timedelta(days=6)
            key = (start.isocalendar().year, start.isocalendar().week)
        else:
            start = row_date.replace(day=1)
            end = row_date.replace(day=monthrange(row_date.year, row_date.month)[1])
            key = (row_date.year, row_date.month)
        if end > latest:
            continue
        bucket = buckets.setdefault(key, {"period_end": end.isoformat(), "rows": []})
        bucket["rows"].append(row)
    output = []
    for bucket in sorted(buckets.values(), key=lambda item: item["period_end"]):
        bucket_rows = bucket["rows"]
        highs = [number(row.get("high")) for row in bucket_rows if number(row.get("high")) is not None]
        lows = [number(row.get("low")) for row in bucket_rows if number(row.get("low")) is not None]
        volumes = [number(row.get("volume")) for row in bucket_rows if number(row.get("volume")) is not None]
        output.append({
            "period_end": bucket["period_end"],
            "high": max(highs) if highs else max(number(row.get("close")) for row in bucket_rows),
            "low": min(lows) if lows else min(number(row.get("close")) for row in bucket_rows),
            "close": number(bucket_rows[-1].get("close")),
            "volume": sum(volumes) if volumes else None,
        })
    return output


def ema(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return output
    current = statistics.fmean(values[:period])
    output[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(values)):
        current += (values[index] - current) * multiplier
        output[index] = current
    return output


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gain = statistics.fmean(max(value, 0) for value in changes[:period])
    loss = statistics.fmean(max(-value, 0) for value in changes[:period])
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        output[index] = 100.0 if loss == 0 and gain > 0 else 50.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return output


def macd(values: list[float]) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    line = [first - second if first is not None and second is not None else None for first, second in zip(fast, slow)]
    indices = [index for index, value in enumerate(line) if value is not None]
    compact = ema([float(line[index]) for index in indices], 9)
    signal: list[float | None] = [None] * len(values)
    for compact_index, original_index in enumerate(indices):
        signal[original_index] = compact[compact_index]
    histogram = [first - second if first is not None and second is not None else None for first, second in zip(line, signal)]
    return line, signal, histogram


def atr(bars: list[dict[str, Any]], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None] * len(bars)
    ranges = []
    for index, bar in enumerate(bars):
        high = number(bar.get("high"))
        low = number(bar.get("low"))
        previous = number(bars[index - 1].get("close")) if index else number(bar.get("close"))
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    if len(ranges) < period:
        return output
    current = statistics.fmean(ranges[:period])
    output[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        output[index] = current
    return output


def obv(bars: list[dict[str, Any]]) -> list[float | None]:
    if not bars:
        return []
    output: list[float | None] = [0.0]
    current = 0.0
    for index in range(1, len(bars)):
        volume = number(bars[index].get("volume"))
        current_close = number(bars[index].get("close"))
        prior_close = number(bars[index - 1].get("close"))
        if volume is None:
            output.append(None)
            continue
        current += volume if current_close > prior_close else -volume if current_close < prior_close else 0
        output.append(current)
    return output


def log_slope(values: list[float], bars: int, offset: int = 0) -> float | None:
    end = len(values) - offset
    start = end - bars
    if start < 0:
        return None
    sample = values[start:end]
    mean_index = (len(sample) - 1) / 2
    logs = [math.log(value) for value in sample]
    mean_value = statistics.fmean(logs)
    denominator = sum((index - mean_index) ** 2 for index in range(len(sample)))
    return sum((index - mean_index) * (value - mean_value) for index, value in enumerate(logs)) / denominator


def divergence(bars: list[dict[str, Any]], oscillator: list[float | None], direction: str, lookback: int, radius: int = 2) -> dict[str, Any]:
    pivots = []
    for index in range(max(radius, len(bars) - lookback), len(bars) - radius):
        price = number(bars[index].get("close"))
        if price is None or oscillator[index] is None:
            continue
        neighbours = [number(bars[candidate].get("close")) for candidate in range(index - radius, index + radius + 1)]
        pivot = price == min(neighbours) and any(price < value for value in neighbours) if direction == "bottom" else price == max(neighbours) and any(price > value for value in neighbours)
        if pivot:
            pivots.append(index)
    if len(pivots) < 2:
        return {"detected": False}
    first, second = pivots[-2:]
    first_price = number(bars[first].get("close"))
    second_price = number(bars[second].get("close"))
    first_oscillator = number(oscillator[first])
    second_oscillator = number(oscillator[second])
    detected = second_price < first_price and second_oscillator > first_oscillator if direction == "bottom" else second_price > first_price and second_oscillator < first_oscillator
    return {
        "detected": detected,
        "first_date": bars[first].get("period_end"),
        "second_date": bars[second].get("period_end"),
        "first_price": first_price,
        "second_price": second_price,
        "first_oscillator": first_oscillator,
        "second_oscillator": second_oscillator,
    }


def expected_technical(rows: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
    bars = aggregate_completed(rows, timeframe)
    closes = [float(number(bar.get("close"))) for bar in bars]
    rsi_values = rsi(closes)
    macd_line, signal, histogram = macd(closes)
    atr_values = atr(bars)
    obv_values = obv(bars)
    fast_period, slow_period = (20, 30) if timeframe == "weekly" else (10, 20)
    fast = statistics.fmean(closes[-fast_period:]) if len(closes) >= fast_period else None
    slow = statistics.fmean(closes[-slow_period:]) if len(closes) >= slow_period else None
    slope_bars = 12 if timeframe == "weekly" else 6
    current_slope = log_slope(closes, slope_bars)
    previous_slope = log_slope(closes, slope_bars, slope_bars)
    volumes = [number(bar.get("volume")) for bar in bars]
    recent_volumes = [value for value in volumes[-20:] if value is not None]
    volume_average = statistics.fmean(recent_volumes) if len(recent_volumes) >= 10 else None
    latest_high = number(bars[-1].get("high"))
    latest_low = number(bars[-1].get("low"))
    recovery = (closes[-1] - latest_low) / (latest_high - latest_low) if latest_high > latest_low else None
    lookback = 30 if timeframe == "weekly" else 24
    return {
        "bars": len(bars),
        "as_of": bars[-1].get("period_end"),
        "close": closes[-1],
        "rsi_14": rsi_values[-1],
        "macd": {"line": macd_line[-1], "signal": signal[-1], "histogram": histogram[-1]},
        "atr": atr_values[-1],
        "obv": obv_values[-1],
        "obv_change_4": obv_values[-1] - obv_values[-5] if len(obv_values) >= 5 and obv_values[-1] is not None and obv_values[-5] is not None else None,
        "fast": fast,
        "slow": slow,
        "current_slope": current_slope,
        "previous_slope": previous_slope,
        "volume": volumes[-1],
        "volume_average": volume_average,
        "relative_volume": volumes[-1] / volume_average if volumes[-1] is not None and volume_average else None,
        "recovery": recovery,
        "divergence": {
            "rsi_bottom": divergence(bars, rsi_values, "bottom", lookback),
            "rsi_top": divergence(bars, rsi_values, "top", lookback),
            "macd_bottom": divergence(bars, histogram, "bottom", lookback),
            "macd_top": divergence(bars, histogram, "top", lookback),
        },
    }


def collect_analysis_text(analysis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for horizon in analysis.get("horizons", {}).values():
        for key in ("plain_read", "what_changed", "falsifier"):
            if horizon.get(key):
                values.append(str(horizon[key]))
        values.extend(str(item.get("plain_read")) for item in horizon.get("perspectives", []) if item.get("plain_read"))
    for insight in analysis.get("exclusive_insights", []):
        values.extend(str(insight.get(key)) for key in ("claim", "what_changed", "falsifier") if insight.get(key))
    for technical in analysis.get("technical_horizons", {}).values():
        values.append(str(technical.get("invalidation") or ""))
        values.extend(str(item.get("evidence")) for item in technical.get("leading_signals", []) if item.get("evidence"))
    for sentiment in analysis.get("news_sentiment", {}).values():
        if not isinstance(sentiment, dict):
            continue
        values.extend(str(sentiment.get(key)) for key in ("conclusion", "method", "invalidation") if sentiment.get(key))
        values.extend(str(item.get("interpretation")) for item in sentiment.get("evidence", []) if item.get("interpretation"))
    return values


def verify() -> dict[str, Any]:
    analysis = load_json(ANALYSIS_PATH)
    history = load_json(HISTORY_PATH)
    price_history = load_json(PRICE_HISTORY_PATH)
    data_verification = load_json(DATA_VERIFICATION_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    market = load_json(MARKET_PATH)
    market_verification = load_json(MARKET_VERIFICATION_PATH)
    context = load_json(CONTEXT_PATH)
    context_verification = load_json(CONTEXT_VERIFICATION_PATH)
    failures: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    if analysis.get("schema") != 1:
        failures.append("analysis schema is not 1")
    if history.get("schema") != 1:
        failures.append("analysis history schema is not 1")
    lineage = {
        "snapshot": analysis.get("snapshot_generated_at") == snapshot.get("generated_at"),
        "market": analysis.get("market_universe_generated_at") == market.get("generated_at"),
        "market_verifier": market_verification.get("market_generated_at") == market.get("generated_at") and market_verification.get("status") != "fail",
        "context": analysis.get("market_context_generated_at") == context.get("generated_at"),
        "context_verifier": context_verification.get("source_generated_at") == context.get("generated_at") and context_verification.get("status") != "fail",
        "price_history": analysis.get("price_history_generated_at") == price_history.get("generated_at"),
        "price_verification": data_verification.get("history_generated_at") == price_history.get("generated_at"),
        "market_fresh": -0.5 <= age_hours(market.get("generated_at")) <= 3,
        "context_fresh": -0.5 <= age_hours(context.get("generated_at")) <= 30,
        "price_history_fresh": -0.5 <= age_hours(price_history.get("generated_at")) <= 36,
        "snapshot_fresh": -0.5 <= age_hours(snapshot.get("generated_at")) <= 36,
    }
    if not all(lineage.values()):
        failures.append(f"analysis lineage mismatch: {lineage}")
    quality = analysis.get("quality", {})
    if quality.get("execution_gate_eligible") is not False:
        failures.append("timescale analysis must never be execution-gate eligible")
    if quality.get("publication_mode") not in {"analysis_only", "diagnostics_only"}:
        failures.append("invalid publication mode")
    if quality.get("status") not in {"pass", "degraded", "fail"}:
        failures.append("invalid quality status")
    if quality.get("status") == "fail":
        if analysis.get("horizons") or analysis.get("technical_horizons") or analysis.get("news_sentiment") or analysis.get("exclusive_insights"):
            failures.append("failed analysis still exposes conclusions")
    else:
        if set(analysis.get("horizons", {})) != set(HORIZON_BARS):
            failures.append("four-horizon analysis is incomplete")
        if set(analysis.get("asset_matrix", {})) != set(ASSETS):
            failures.append("asset matrix is incomplete")
        for symbol in ASSETS:
            price_asset = price_history.get("assets", {}).get(symbol, {})
            provider = price_asset.get("canonical_provider")
            rows = (price_asset.get("sources", {}).get(provider) or {}).get("rows") or []
            for horizon, bars in HORIZON_BARS.items():
                actual = (((analysis.get("asset_matrix") or {}).get(symbol) or {}).get(horizon) or {}).get("return")
                expected = expected_return(rows, bars)
                passed = close_enough(actual, expected)
                checks.append({"name": f"{symbol}_{horizon}_return", "status": "pass" if passed else "fail", "actual": actual, "expected": expected})
                if not passed:
                    failures.append(f"{symbol} {horizon} return does not match canonical completed bars")
                if symbol != "BTC":
                    btc_return = (((analysis.get("asset_matrix") or {}).get("BTC") or {}).get(horizon) or {}).get("return")
                    relative = (((analysis.get("asset_matrix") or {}).get(symbol) or {}).get(horizon) or {}).get("relative_to_btc")
                    expected_relative = number(actual) - number(btc_return) if number(actual) is not None and number(btc_return) is not None else None
                    if not close_enough(relative, expected_relative):
                        failures.append(f"{symbol} {horizon} relative-to-BTC return is inconsistent")
        btc_asset = price_history.get("assets", {}).get("BTC", {})
        btc_provider = btc_asset.get("canonical_provider")
        btc_rows = (btc_asset.get("sources", {}).get(btc_provider) or {}).get("rows") or []
        technical_horizons = analysis.get("technical_horizons") or {}
        if set(technical_horizons) != {"weekly", "monthly"}:
            failures.append("completed weekly/monthly technical analysis is incomplete")
        for timeframe in ("weekly", "monthly"):
            actual = technical_horizons.get(timeframe) or {}
            if not actual:
                continue
            expected = expected_technical(btc_rows, timeframe)
            expected_basis = f"completed_{timeframe}_candles"
            if actual.get("bar_basis") != expected_basis:
                failures.append(f"{timeframe} technical layer does not declare completed-candle basis")
            if actual.get("canonical_provider") != btc_provider or actual.get("source_count") != btc_asset.get("source_count"):
                failures.append(f"{timeframe} technical source provenance is inconsistent")
            value_checks = {
                "bars": (actual.get("bars"), expected.get("bars")),
                "close": (actual.get("close"), expected.get("close")),
                "rsi_14": (actual.get("rsi_14"), expected.get("rsi_14")),
                "macd_line": ((actual.get("macd") or {}).get("line"), expected["macd"]["line"]),
                "macd_signal": ((actual.get("macd") or {}).get("signal"), expected["macd"]["signal"]),
                "macd_histogram": ((actual.get("macd") or {}).get("histogram"), expected["macd"]["histogram"]),
                "atr_14": ((actual.get("atr_14") or {}).get("value"), expected.get("atr")),
                "obv": ((actual.get("obv") or {}).get("value"), expected.get("obv")),
                "obv_change_4": ((actual.get("obv") or {}).get("change_4_bars"), expected.get("obv_change_4")),
                "fast_average": ((actual.get("moving_averages") or {}).get(f"{20 if timeframe == 'weekly' else 10}_{timeframe}"), expected.get("fast")),
                "slow_average": ((actual.get("moving_averages") or {}).get(f"{30 if timeframe == 'weekly' else 20}_{timeframe}"), expected.get("slow")),
                "current_slope": ((actual.get("price_slope") or {}).get("current_log_slope_per_bar"), expected.get("current_slope")),
                "previous_slope": ((actual.get("price_slope") or {}).get("previous_log_slope_per_bar"), expected.get("previous_slope")),
                "volume": ((actual.get("volume") or {}).get("current"), expected.get("volume")),
                "volume_average": ((actual.get("volume") or {}).get("average_20"), expected.get("volume_average")),
                "relative_volume": ((actual.get("volume") or {}).get("relative_to_average"), expected.get("relative_volume")),
                "close_recovery": ((actual.get("volume") or {}).get("close_recovery"), expected.get("recovery")),
            }
            for metric, (reported, recomputed) in value_checks.items():
                passed = close_enough(reported, recomputed)
                checks.append({"name": f"BTC_{timeframe}_{metric}", "status": "pass" if passed else "fail", "actual": reported, "expected": recomputed})
                if not passed:
                    failures.append(f"BTC {timeframe} {metric} does not match independent recomputation")
            if actual.get("as_of") != expected.get("as_of"):
                failures.append(f"BTC {timeframe} completed-candle cutoff is inconsistent")
            for name, recomputed in expected["divergence"].items():
                reported = (actual.get("divergence") or {}).get(name) or {}
                if bool(reported.get("detected")) != bool(recomputed.get("detected")):
                    failures.append(f"BTC {timeframe} {name} divergence flag is inconsistent")
                if recomputed.get("first_date"):
                    first = reported.get("first") or {}
                    second = reported.get("second") or {}
                    if first.get("date") != recomputed.get("first_date") or second.get("date") != recomputed.get("second_date"):
                        failures.append(f"BTC {timeframe} {name} divergence pivots are inconsistent")
                    for label, reported_value, expected_value in (
                        ("first_price", first.get("price"), recomputed.get("first_price")),
                        ("second_price", second.get("price"), recomputed.get("second_price")),
                        ("first_oscillator", first.get("oscillator"), recomputed.get("first_oscillator")),
                        ("second_oscillator", second.get("oscillator"), recomputed.get("second_oscillator")),
                    ):
                        if not close_enough(reported_value, expected_value):
                            failures.append(f"BTC {timeframe} {name} {label} is inconsistent")
            leading = actual.get("leading_signals") or []
            lagging = actual.get("lagging_confirmations") or []
            warnings_set = (actual.get("top_risk") or {}).get("warnings") or []
            if len(leading) != 3 or len(lagging) != 4 or len(warnings_set) != 3:
                failures.append(f"BTC {timeframe} leading/lagging/top signal contract is incomplete")
            if (actual.get("bottom_assessment") or {}).get("leading_supportive") != sum(item.get("state") == "supportive" for item in leading):
                failures.append(f"BTC {timeframe} leading signal count is inconsistent")
            if (actual.get("bottom_assessment") or {}).get("lagging_confirmed") != sum(item.get("state") == "confirmed" for item in lagging):
                failures.append(f"BTC {timeframe} lagging confirmation count is inconsistent")
            if (actual.get("top_risk") or {}).get("active_warnings") != sum(bool(item.get("active")) for item in warnings_set):
                failures.append(f"BTC {timeframe} top-warning count is inconsistent")
        news = analysis.get("news_sentiment") or {}
        if news.get("execution_gate_eligible") is not False or news.get("scope") != "verified_context_only":
            failures.append("news sentiment scope or execution boundary is invalid")
        for timeframe in ("weekly", "monthly"):
            summary = news.get(timeframe) or {}
            evidence = summary.get("evidence") or []
            cluster_ids = [item.get("cluster_id") for item in evidence]
            if len(evidence) < 4 or len(cluster_ids) != len(set(cluster_ids)) or any(not cluster_id for cluster_id in cluster_ids):
                failures.append(f"{timeframe} news sentiment lacks independent evidence clusters")
            if summary.get("supportive_clusters") != sum(item.get("state") == "supportive" for item in evidence):
                failures.append(f"{timeframe} supportive sentiment count is inconsistent")
            if summary.get("risk_off_clusters") != sum(item.get("state") == "risk_off" for item in evidence):
                failures.append(f"{timeframe} risk-off sentiment count is inconsistent")
            for item in evidence:
                source = item.get("source") or {}
                url = str(source.get("url") or "")
                if not (url.startswith("https://") or url.startswith("data/daily/")) or not item.get("as_of"):
                    failures.append(f"{timeframe} sentiment evidence is missing source or as_of: {item.get('name')}")
                if source.get("status") == "unverified" and item.get("state") != "unknown":
                    failures.append(f"{timeframe} unverified sentiment evidence participates in direction: {item.get('name')}")
        alignment = analysis.get("alignment", {})
        if alignment.get("known_horizons") != 4:
            failures.append("alignment does not cover all four horizons")
        if len(analysis.get("exclusive_insights", [])) < 3:
            failures.append("exclusive insight set is incomplete")
        for horizon, item in analysis.get("horizons", {}).items():
            clusters: dict[str, set[str]] = {}
            for perspective in item.get("perspectives", []):
                cluster_id = str(perspective.get("cluster_id") or "")
                if not cluster_id:
                    failures.append(f"{horizon} perspective is missing cluster_id")
                    continue
                if not perspective.get("counts_toward_underlying_resonance"):
                    continue
                direction = perspective.get("direction")
                clusters.setdefault(cluster_id, set())
                if direction in {"positive", "negative"}:
                    clusters[cluster_id].add(direction)
            positive = sum(directions == {"positive"} for directions in clusters.values())
            negative = sum(directions == {"negative"} for directions in clusters.values())
            directional = sum(directions in ({"positive"}, {"negative"}) for directions in clusters.values())
            expected = "偏正向共振" if positive >= 3 and positive > negative else "偏負向共振" if negative >= 3 and negative > positive else "多維訊號分歧"
            if item.get("resonance") != expected:
                failures.append(f"{horizon} resonance is inconsistent with independent cluster votes")
            reported_votes = item.get("resonance_votes") or {}
            if reported_votes != {"positive_clusters": positive, "negative_clusters": negative, "directional_clusters": directional, "eligible_clusters": len(clusters)}:
                failures.append(f"{horizon} resonance vote audit is inconsistent")
            for perspective in item.get("perspectives", []):
                if perspective.get("cluster_id") in {"vehicle_mstr_capital_structure", "vehicle_mstr_relative_value"} and perspective.get("counts_toward_underlying_resonance"):
                    failures.append(f"{horizon} vehicle evidence participates in underlying resonance")
    text = "\n".join(collect_analysis_text(analysis))
    for phrase in PROHIBITED_ACTION_PHRASES:
        if phrase in text:
            failures.append(f"analysis contains prohibited strategy output: {phrase}")
    items = history.get("items") or []
    if not items or items[-1].get("generated_at") != analysis.get("generated_at"):
        failures.append("append-only history is not bound to the current analysis")
    if items:
        same_date = [item for item in items if item.get("date") == analysis.get("date")]
        if items[-1].get("revision") != len(same_date):
            failures.append("same-day revision numbering is inconsistent")
        if len(same_date) > 1 and not items[-1].get("supersedes_generated_at"):
            failures.append("same-day revision does not preserve superseded observation")
        if not items[-1].get("revision_note"):
            failures.append("append-only history is missing a revision note")
    distinct_dates = {item.get("date") for item in items if item.get("date")}
    if analysis.get("record_advantage", {}).get("observations") != len(items):
        failures.append("analysis observation count does not match append-only history")
    if analysis.get("record_advantage", {}).get("distinct_dates") != len(distinct_dates):
        failures.append("analysis distinct-date count does not match append-only history")
    if len(distinct_dates) < 20:
        warnings.append("history has fewer than 20 distinct dates; percentile claims remain disabled")
        if any((item.get("historical_percentile") or {}).get("status") == "available" for item in analysis.get("horizons", {}).values()):
            failures.append("percentile was published before the 20-date minimum")
    status = "fail" if failures else "pass"
    return {
        "schema": 1,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "analysis_generated_at": analysis.get("generated_at"),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
        "lineage": lineage,
        "scope": "analysis_integrity_only",
        "execution_gate_eligible": False,
    }


def main() -> int:
    report = verify()
    write_json(OUTPUT_PATH, report)
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "status": report["status"],
        "checks": len(report["checks"]),
        "failures": len(report["failures"]),
        "warnings": len(report["warnings"]),
    }, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
