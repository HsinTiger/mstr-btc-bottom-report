#!/usr/bin/env python3
"""Generate deterministic daily, weekly, monthly, and quarterly market intelligence."""

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
PRICE_HISTORY_PATH = DATA_DIR / "timescale_price_history.json"
DATA_VERIFICATION_PATH = DATA_DIR / "timescale_data_verification.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
DAILY_VERIFICATION_PATH = DATA_DIR / "agent_verification_report.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
MARKET_VERIFICATION_PATH = DATA_DIR / "market_universe_verification.json"
CONTEXT_PATH = DATA_DIR / "market_context.json"
CONTEXT_VERIFICATION_PATH = DATA_DIR / "market_context_verification.json"
OUTPUT_PATH = DATA_DIR / "timescale_intelligence.json"
HISTORY_PATH = DATA_DIR / "timescale_intelligence_history.json"

HORIZONS = {
    "daily": {"label": "日線", "return_bars": 1, "fast_bars": 5, "slow_bars": 20, "volatility_bars": 20, "range_bars": 20},
    "weekly": {"label": "週線", "return_bars": 5, "fast_bars": 10, "slow_bars": 30, "volatility_bars": 30, "range_bars": 60},
    "monthly": {"label": "月線", "return_bars": 21, "fast_bars": 21, "slow_bars": 63, "volatility_bars": 63, "range_bars": 126},
    "quarterly": {"label": "季線", "return_bars": 63, "fast_bars": 63, "slow_bars": 126, "volatility_bars": 126, "range_bars": 252},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def nested(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def format_percent(value: float | None, decimals: int = 1) -> str:
    return "資料不足" if value is None else f"{value * 100:+.{decimals}f}%"


def format_multiple(value: float | None, decimals: int = 2) -> str:
    return "資料不足" if value is None else f"{value:.{decimals}f}x"


def moving_average(values: list[float], bars: int) -> float | None:
    return statistics.fmean(values[-bars:]) if len(values) >= bars else None


def period_return(values: list[float], bars: int, offset: int = 0) -> float | None:
    end_index = len(values) - 1 - offset
    start_index = end_index - bars
    if start_index < 0 or end_index < 0:
        return None
    start = values[start_index]
    end = values[end_index]
    return end / start - 1 if start else None


def annualized_volatility(values: list[float], bars: int, annualization: int) -> float | None:
    sample = values[-(bars + 1):]
    if len(sample) < max(10, bars // 2):
        return None
    returns = [math.log(sample[index] / sample[index - 1]) for index in range(1, len(sample)) if sample[index - 1] > 0]
    return statistics.stdev(returns) * math.sqrt(annualization) if len(returns) >= 2 else None


def log_trend(values: list[float], bars: int) -> tuple[float | None, float | None]:
    sample = values[-bars:]
    if len(sample) < max(10, bars // 2) or any(value <= 0 for value in sample):
        return None, None
    log_values = [math.log(value) for value in sample]
    mean_index = (len(sample) - 1) / 2
    mean_value = statistics.fmean(log_values)
    denominator = sum((index - mean_index) ** 2 for index in range(len(sample)))
    slope = sum((index - mean_index) * (value - mean_value) for index, value in enumerate(log_values)) / denominator
    fitted = [mean_value + slope * (index - mean_index) for index in range(len(sample))]
    total_variance = sum((value - mean_value) ** 2 for value in log_values)
    residual_variance = sum((value - estimate) ** 2 for value, estimate in zip(log_values, fitted))
    r_squared = 1 - residual_variance / total_variance if total_variance else 0.0
    trend_return = math.exp(slope * (len(sample) - 1)) - 1
    return trend_return, max(0.0, min(1.0, r_squared))


def range_position(values: list[float], bars: int) -> float | None:
    sample = values[-bars:]
    if len(sample) < max(10, bars // 2):
        return None
    lower = min(sample)
    upper = max(sample)
    return (sample[-1] - lower) / (upper - lower) if upper > lower else 0.5


def aggregate_completed_bars(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    if timeframe not in {"weekly", "monthly"}:
        raise ValueError(f"unsupported completed-bar timeframe: {timeframe}")
    normalized: list[tuple[date, dict[str, Any]]] = []
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row.get("date")))
        except (TypeError, ValueError):
            continue
        close = number(row.get("close"))
        if close is None or close <= 0:
            continue
        normalized.append((row_date, row))
    normalized.sort(key=lambda item: item[0])
    if not normalized:
        return []
    latest_date = normalized[-1][0]
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    for row_date, row in normalized:
        if timeframe == "weekly":
            period_start = row_date - timedelta(days=row_date.weekday())
            period_end = period_start + timedelta(days=6)
            key = (period_start.isocalendar().year, period_start.isocalendar().week)
        else:
            period_start = row_date.replace(day=1)
            period_end = row_date.replace(day=monthrange(row_date.year, row_date.month)[1])
            key = (row_date.year, row_date.month)
        if period_end > latest_date:
            continue
        bucket = groups.setdefault(key, {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "rows": [],
        })
        bucket["rows"].append(row)
    completed: list[dict[str, Any]] = []
    for bucket in sorted(groups.values(), key=lambda item: item["period_end"]):
        bucket_rows = bucket["rows"]
        opens = [number(row.get("open")) for row in bucket_rows]
        highs = [number(row.get("high")) for row in bucket_rows]
        lows = [number(row.get("low")) for row in bucket_rows]
        volumes = [number(row.get("volume")) for row in bucket_rows]
        valid_opens = [value for value in opens if value is not None]
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        valid_volumes = [value for value in volumes if value is not None]
        completed.append({
            "date": bucket["period_end"],
            "period_start": bucket["period_start"],
            "period_end": bucket["period_end"],
            "source_last_date": bucket_rows[-1].get("date"),
            "observed_days": len(bucket_rows),
            "open": valid_opens[0] if valid_opens else number(bucket_rows[0].get("close")),
            "high": max(valid_highs) if valid_highs else max(number(row.get("close")) for row in bucket_rows),
            "low": min(valid_lows) if valid_lows else min(number(row.get("close")) for row in bucket_rows),
            "close": number(bucket_rows[-1].get("close")),
            "volume": sum(valid_volumes) if valid_volumes else None,
        })
    return completed


def exponential_moving_average_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    seed = statistics.fmean(values[:period])
    result[period - 1] = seed
    multiplier = 2 / (period + 1)
    current = seed
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return result
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    average_gain = statistics.fmean(max(change, 0.0) for change in changes[:period])
    average_loss = statistics.fmean(max(-change, 0.0) for change in changes[:period])

    def value() -> float:
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        relative_strength = average_gain / average_loss
        return 100 - 100 / (1 + relative_strength)

    result[period] = value()
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        result[index] = value()
    return result


def macd_series(values: list[float]) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast = exponential_moving_average_series(values, 12)
    slow = exponential_moving_average_series(values, 26)
    macd: list[float | None] = [
        fast_value - slow_value if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast, slow)
    ]
    valid_indices = [index for index, value in enumerate(macd) if value is not None]
    valid_values = [macd[index] for index in valid_indices]
    compact_signal = exponential_moving_average_series([float(value) for value in valid_values], 9)
    signal: list[float | None] = [None] * len(values)
    for compact_index, original_index in enumerate(valid_indices):
        signal[original_index] = compact_signal[compact_index]
    histogram = [
        macd_value - signal_value if macd_value is not None and signal_value is not None else None
        for macd_value, signal_value in zip(macd, signal)
    ]
    return macd, signal, histogram


def atr_series(bars: list[dict[str, Any]], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    if not bars:
        return result
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        high = number(bar.get("high"))
        low = number(bar.get("low"))
        close = number(bar.get("close"))
        if high is None or low is None or close is None:
            true_ranges.append(float("nan"))
            continue
        previous_close = number(bars[index - 1].get("close")) if index else close
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(true_ranges) < period or any(not math.isfinite(value) for value in true_ranges[:period]):
        return result
    current = statistics.fmean(true_ranges[:period])
    result[period - 1] = current
    for index in range(period, len(true_ranges)):
        if not math.isfinite(true_ranges[index]):
            continue
        current = (current * (period - 1) + true_ranges[index]) / period
        result[index] = current
    return result


def obv_series(bars: list[dict[str, Any]]) -> list[float | None]:
    if not bars:
        return []
    result: list[float | None] = [0.0]
    current = 0.0
    for index in range(1, len(bars)):
        volume = number(bars[index].get("volume"))
        close = number(bars[index].get("close"))
        previous_close = number(bars[index - 1].get("close"))
        if volume is None or close is None or previous_close is None:
            result.append(None)
            continue
        current += volume if close > previous_close else -volume if close < previous_close else 0.0
        result.append(current)
    return result


def detect_divergence(
    bars: list[dict[str, Any]],
    oscillator: list[float | None],
    direction: str,
    *,
    radius: int = 2,
    lookback: int | None = None,
) -> dict[str, Any]:
    if direction not in {"bottom", "top"}:
        raise ValueError(f"unsupported divergence direction: {direction}")
    start = max(radius, len(bars) - lookback) if lookback else radius
    pivots: list[int] = []
    for index in range(start, len(bars) - radius):
        price = number(bars[index].get("close"))
        oscillator_value = oscillator[index] if index < len(oscillator) else None
        if price is None or oscillator_value is None:
            continue
        neighbours = [number(bars[candidate].get("close")) for candidate in range(index - radius, index + radius + 1)]
        if any(value is None for value in neighbours):
            continue
        is_pivot = price == min(neighbours) and any(price < value for value in neighbours) if direction == "bottom" else price == max(neighbours) and any(price > value for value in neighbours)
        if is_pivot:
            pivots.append(index)
    if len(pivots) < 2:
        return {"detected": False, "direction": direction, "reason": "可比較轉折點不足"}
    first_index, second_index = pivots[-2], pivots[-1]
    first_price = number(bars[first_index].get("close"))
    second_price = number(bars[second_index].get("close"))
    first_oscillator = number(oscillator[first_index])
    second_oscillator = number(oscillator[second_index])
    lower_low = second_price < first_price and second_oscillator > first_oscillator
    higher_high = second_price > first_price and second_oscillator < first_oscillator
    detected = lower_low if direction == "bottom" else higher_high
    return {
        "detected": detected,
        "direction": direction,
        "price_direction": "lower_low" if second_price < first_price else "higher_high" if second_price > first_price else "equal",
        "oscillator_direction": "higher_low" if second_oscillator > first_oscillator else "lower_high" if second_oscillator < first_oscillator else "equal",
        "first": {"date": bars[first_index].get("period_end") or bars[first_index].get("date"), "price": first_price, "oscillator": first_oscillator},
        "second": {"date": bars[second_index].get("period_end") or bars[second_index].get("date"), "price": second_price, "oscillator": second_oscillator},
    }


def regression_log_slope(values: list[float], bars: int, offset: int = 0) -> float | None:
    end = len(values) - offset
    start = end - bars
    if start < 0 or bars < 2:
        return None
    sample = values[start:end]
    if any(value <= 0 for value in sample):
        return None
    mean_index = (len(sample) - 1) / 2
    mean_value = statistics.fmean(math.log(value) for value in sample)
    denominator = sum((index - mean_index) ** 2 for index in range(len(sample)))
    return sum((index - mean_index) * (math.log(value) - mean_value) for index, value in enumerate(sample)) / denominator if denominator else None


def technical_horizon(rows: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
    bars = aggregate_completed_bars(rows, timeframe)
    basis = f"completed_{timeframe}_candles"
    if not bars:
        return {"status": "資料不足", "bar_basis": basis, "bars": 0}
    closes = [float(number(bar.get("close"))) for bar in bars]
    rsi = rsi_series(closes)
    macd_line, signal_line, histogram = macd_series(closes)
    atr = atr_series(bars)
    obv = obv_series(bars)
    fast_period, slow_period = (20, 30) if timeframe == "weekly" else (10, 20)
    fast_average = moving_average(closes, fast_period)
    slow_average = moving_average(closes, slow_period)
    slope_bars = 12 if timeframe == "weekly" else 6
    current_slope = regression_log_slope(closes, slope_bars)
    previous_slope = regression_log_slope(closes, slope_bars, slope_bars)
    slope_change = current_slope - previous_slope if current_slope is not None and previous_slope is not None else None
    volume_values = [number(bar.get("volume")) for bar in bars]
    volume_average_values = [value for value in volume_values[-20:] if value is not None]
    volume_average = statistics.fmean(volume_average_values) if len(volume_average_values) >= 10 else None
    current_volume = volume_values[-1]
    relative_volume = current_volume / volume_average if current_volume is not None and volume_average else None
    bar_return = closes[-1] / closes[-2] - 1 if len(closes) >= 2 else None
    latest = bars[-1]
    high = number(latest.get("high"))
    low = number(latest.get("low"))
    recovery = (closes[-1] - low) / (high - low) if high is not None and low is not None and high > low else None
    if relative_volume is not None and relative_volume >= 1.5 and bar_return is not None and bar_return <= 0.02 and recovery is not None and recovery >= 0.6:
        volume_state = "放量承接"
    elif relative_volume is not None and relative_volume >= 1.5 and bar_return is not None and abs(bar_return) <= 0.02:
        volume_state = "量增價滯"
    elif relative_volume is not None and relative_volume >= 1.5 and bar_return is not None and bar_return < -0.02:
        volume_state = "放量下跌"
    elif relative_volume is not None and relative_volume <= 0.7:
        volume_state = "量能收縮"
    else:
        volume_state = "量價中性"
    rsi_bottom = detect_divergence(bars, rsi, "bottom", lookback=30 if timeframe == "weekly" else 24)
    rsi_top = detect_divergence(bars, rsi, "top", lookback=30 if timeframe == "weekly" else 24)
    macd_bottom = detect_divergence(bars, histogram, "bottom", lookback=30 if timeframe == "weekly" else 24)
    macd_top = detect_divergence(bars, histogram, "top", lookback=30 if timeframe == "weekly" else 24)
    for item, oscillator_name in ((rsi_bottom, "RSI 14"), (rsi_top, "RSI 14"), (macd_bottom, "MACD 柱狀體"), (macd_top, "MACD 柱狀體")):
        item["oscillator"] = oscillator_name
    slope_decelerating_down = current_slope is not None and current_slope < 0 and slope_change is not None and slope_change > 0
    slope_decelerating_up = current_slope is not None and current_slope > 0 and slope_change is not None and slope_change < 0
    leading_signals = [
        {"name": "動能底背離", "state": "supportive" if rsi_bottom.get("detected") or macd_bottom.get("detected") else "neutral", "evidence": "RSI 或 MACD 柱狀體在價格創低時未同步創低。"},
        {"name": "下跌斜率放緩", "state": "supportive" if slope_decelerating_down else "neutral", "evidence": f"每根 K 的對數斜率 {current_slope:+.4f}，較前窗變化 {slope_change:+.4f}。" if current_slope is not None and slope_change is not None else "斜率歷史不足。"},
        {"name": "恐慌量承接", "state": "supportive" if volume_state == "放量承接" else "warning" if volume_state in {"量增價滯", "放量下跌"} else "neutral", "evidence": f"量比 {relative_volume:.2f}x、收盤回復幅度 {recovery:.0%}，判為{volume_state}。" if relative_volume is not None and recovery is not None else f"量價判為{volume_state}。"},
    ]
    recent_pivots = []
    for index in range(2, len(bars) - 2):
        neighbours = closes[index - 2:index + 3]
        if closes[index] == min(neighbours) and any(closes[index] < value for value in neighbours):
            recent_pivots.append(index)
    higher_low = len(recent_pivots) >= 2 and closes[recent_pivots[-1]] > closes[recent_pivots[-2]]
    macd_cross_positive = macd_line[-1] is not None and signal_line[-1] is not None and macd_line[-1] > signal_line[-1]
    lagging_confirmations = [
        {"name": f"站回 {fast_period} {('週' if timeframe == 'weekly' else '月')}均線", "state": "confirmed" if fast_average is not None and closes[-1] > fast_average else "not_confirmed", "value": fast_average},
        {"name": f"站回 {slow_period} {('週' if timeframe == 'weekly' else '月')}均線", "state": "confirmed" if slow_average is not None and closes[-1] > slow_average else "not_confirmed", "value": slow_average},
        {"name": "MACD 位於訊號線之上", "state": "confirmed" if macd_cross_positive else "not_confirmed", "value": histogram[-1]},
        {"name": "低點墊高", "state": "confirmed" if higher_low else "not_confirmed", "value": None},
    ]
    leading_count = sum(item["state"] == "supportive" for item in leading_signals)
    confirmation_count = sum(item["state"] == "confirmed" for item in lagging_confirmations)
    bottom_state = "底部形成證據增加" if leading_count >= 2 and confirmation_count >= 2 else "底部候選，尚待落後指標確認" if leading_count >= 2 else "底部證據尚未成形"
    top_warnings = [
        {"name": "動能頂背離", "active": bool(rsi_top.get("detected") or macd_top.get("detected"))},
        {"name": "上漲斜率放緩", "active": slope_decelerating_up},
        {"name": "量增價滯", "active": volume_state == "量增價滯"},
    ]
    top_count = sum(item["active"] for item in top_warnings)
    return {
        "status": "可用" if len(bars) >= 35 else "歷史深度有限",
        "bar_basis": basis,
        "bars": len(bars),
        "as_of": bars[-1].get("period_end"),
        "close": closes[-1],
        "period_return": bar_return,
        "price_slope": {"lookback_bars": slope_bars, "current_log_slope_per_bar": current_slope, "previous_log_slope_per_bar": previous_slope, "change": slope_change},
        "rsi_14": rsi[-1],
        "macd": {"line": macd_line[-1], "signal": signal_line[-1], "histogram": histogram[-1]},
        "volume": {"current": current_volume, "average_20": volume_average, "relative_to_average": relative_volume, "close_recovery": recovery, "state": volume_state},
        "obv": {"value": obv[-1], "change_4_bars": obv[-1] - obv[-5] if len(obv) >= 5 and obv[-1] is not None and obv[-5] is not None else None},
        "atr_14": {"value": atr[-1], "percent_of_close": atr[-1] / closes[-1] if atr[-1] is not None else None},
        "moving_averages": {
            f"{fast_period}_{timeframe}": fast_average,
            f"{slow_period}_{timeframe}": slow_average,
            "distance_from_fast": closes[-1] / fast_average - 1 if fast_average else None,
            "distance_from_slow": closes[-1] / slow_average - 1 if slow_average else None,
        },
        "divergence": {"rsi_bottom": rsi_bottom, "macd_bottom": macd_bottom, "rsi_top": rsi_top, "macd_top": macd_top},
        "leading_signals": leading_signals,
        "lagging_confirmations": lagging_confirmations,
        "bottom_assessment": {"state": bottom_state, "leading_supportive": leading_count, "lagging_confirmed": confirmation_count},
        "top_risk": {"state": "頂部風險升高" if top_count >= 2 else "未見多項頂部共振", "active_warnings": top_count, "warnings": top_warnings},
        "invalidation": "若下一根完成 K 同時打破最近結構低點、動能再創低且量價轉為放量下跌，底部候選失效；頂部警訊則以價格與動能同步再創高失效。",
    }


def sentiment_evidence(
    name: str,
    cluster_id: str,
    value: Any,
    state: str,
    as_of: Any,
    interpretation: str,
    source_label: str,
    source_url: str,
    source_status: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "cluster_id": cluster_id,
        "value": value,
        "state": state,
        "as_of": as_of,
        "interpretation": interpretation,
        "source": {"label": source_label, "url": source_url, "status": source_status},
    }


def sentiment_summary(label: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    supportive = sum(item.get("state") == "supportive" for item in evidence)
    risk_off = sum(item.get("state") == "risk_off" for item in evidence)
    if supportive >= 2 and supportive > risk_off:
        conclusion = f"{label}消息與情緒偏支持，但仍需價格結構確認"
    elif risk_off >= 2 and risk_off > supportive:
        conclusion = f"{label}消息與情緒偏風險收縮"
    else:
        conclusion = f"{label}消息與情緒分歧"
    return {
        "conclusion": conclusion,
        "supportive_clusters": supportive,
        "risk_off_clusters": risk_off,
        "evidence": evidence,
        "method": "每個獨立證據群只計一票；政策事件量只描述監管活動，不臆測利多或利空。",
        "invalidation": "若 ETF、流動性、鏈上活動或情緒代理的方向在下一次已驗證更新中反轉，本結論即失效。",
    }


def build_news_sentiment(snapshot: dict[str, Any], market: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    radar = nested(snapshot, "metrics.market_radar") or {}
    fear_greed = number(radar.get("fear_greed"))
    fear_state = "risk_off" if fear_greed is not None and fear_greed <= 25 else "supportive" if fear_greed is not None and fear_greed >= 60 else "neutral"
    btc_etf = nested(market, "etf.BTC") or {}
    etf_verified = btc_etf.get("status") == "sample_cross_source_verified"
    etf_7d = number(btc_etf.get("flow_7d_usd")) if etf_verified else None
    etf_30d = number(btc_etf.get("flow_30d_usd")) if etf_verified else None
    source_observations = btc_etf.get("source_observations") or {}
    canonical_etf = source_observations.get("The Block") or {}
    policy = context.get("policy") or {}
    policy_events = policy.get("events") or []
    latest_policy = policy_events[0] if policy_events else {}
    funding = number(nested(market, "analysis.BTC.funding_annualized_median"))
    liquidity = nested(context, "macro.liquidity") or {}
    liquidity_resonance = liquidity.get("dollar_liquidity_resonance") or {}
    liquidity_positive = int(number(liquidity_resonance.get("positive_votes")) or 0)
    liquidity_negative = int(number(liquidity_resonance.get("negative_votes")) or 0)
    liquidity_state = "supportive" if liquidity_positive > liquidity_negative else "risk_off" if liquidity_negative > liquidity_positive else "neutral"
    active_addresses = nested(context, "onchain.BTC.active_addresses") or {}
    active_change = number(active_addresses.get("change_30d"))
    active_state = "supportive" if active_change is not None and active_change > 0 else "risk_off" if active_change is not None and active_change < 0 else "neutral"
    hashrate = nested(context, "onchain.BTC.hashrate") or {}
    hashrate_change = number(hashrate.get("change_30d"))
    hashrate_state = "supportive" if hashrate_change is not None and hashrate_change > 0 else "risk_off" if hashrate_change is not None and hashrate_change < 0 else "neutral"
    weekly_evidence = [
        sentiment_evidence("恐懼貪婪情緒", "market_sentiment", fear_greed, fear_state, radar.get("fear_greed_timestamp"), "只描述市場風險偏好；極端恐懼不是自動反向買進訊號。", "Alternative.me Fear & Greed", "https://api.alternative.me/fng/", "verified_snapshot"),
        sentiment_evidence("BTC 現貨 ETF 七日淨流", "institutional_flows", etf_7d, "supportive" if etf_7d is not None and etf_7d > 0 else "risk_off" if etf_7d is not None and etf_7d < 0 else "unknown", btc_etf.get("as_of") or market.get("generated_at"), "僅在基金明細、官方主要基金與同日備援通過後計票。", "The Block＋發行商官方＋同日備援", canonical_etf.get("url") or "data/daily/market_universe.json", "verified" if etf_verified else "unverified"),
        sentiment_evidence("永續資金費率", "derivatives_positioning", funding, "risk_off" if funding is not None and funding > 0.15 else "neutral", market.get("generated_at"), "高正資金費率代表多方持有成本與擁擠度升高，不直接代表方向。", "OKX＋Hyperliquid", "data/daily/market_universe.json", "verified_market_universe"),
        sentiment_evidence("官方政策事件", "policy_activity", policy.get("event_count_7d"), "context", latest_policy.get("published_at") or context.get("date"), "零事件不代表監管風險消失；事件量不自行判定利多或利空。", latest_policy.get("provider") or "官方來源集合", latest_policy.get("url") or "https://www.federalregister.gov/", policy.get("status") or "unknown"),
    ]
    monthly_evidence = [
        sentiment_evidence("BTC 現貨 ETF 三十日淨流", "institutional_flows", etf_30d, "supportive" if etf_30d is not None and etf_30d > 0 else "risk_off" if etf_30d is not None and etf_30d < 0 else "unknown", btc_etf.get("as_of") or market.get("generated_at"), "觀察機構資金的月級別持續性，不以單日流量替代。", "The Block＋發行商官方＋同日備援", canonical_etf.get("url") or "data/daily/market_universe.json", "verified" if etf_verified else "unverified"),
        sentiment_evidence("美元流動性三速共振", "macro_liquidity", liquidity_resonance.get("state"), liquidity_state, liquidity.get("as_of"), f"M2、銀行準備金與 Fed 淨流動性分開計票：正向 {liquidity_positive}、負向 {liquidity_negative}。", "Federal Reserve＋U.S. Treasury＋FRED", "https://fred.stlouisfed.org/series/WALCL", context.get("quality", {}).get("status") or "unknown"),
        sentiment_evidence("BTC 活躍地址三十日變化", "network_activity", active_change, active_state, active_addresses.get("as_of") or context.get("date"), "鏈上活動是使用背景與落後驗證，不等同價格領先訊號。", "Blockchain.com＋Blockchair", active_addresses.get("url") or "https://api.blockchain.info/charts/n-unique-addresses", nested(context, "onchain.BTC.status") or "unknown"),
        sentiment_evidence("BTC 算力三十日變化", "network_security", hashrate_change, hashrate_state, hashrate.get("as_of") or context.get("date"), "算力描述安全活動；短期下降需與難度、礦工經濟及價格分開解讀。", "Blockchain.com＋mempool.space", hashrate.get("url") or "https://api.blockchain.info/charts/hash-rate", nested(context, "onchain.BTC.status") or "unknown"),
        sentiment_evidence("官方政策事件", "policy_activity", policy.get("event_count_30d"), "context", latest_policy.get("published_at") or context.get("date"), "政策活動只提供可追溯事件背景，不以標題情緒替代法案狀態。", latest_policy.get("provider") or "官方來源集合", latest_policy.get("url") or "https://www.federalregister.gov/", policy.get("status") or "unknown"),
    ]
    return {
        "weekly": sentiment_summary("週線", weekly_evidence),
        "monthly": sentiment_summary("月線", monthly_evidence),
        "scope": "verified_context_only",
        "execution_gate_eligible": False,
    }


def classify_state(
    current: float,
    fast_average: float | None,
    slow_average: float | None,
    current_return: float | None,
    previous_return: float | None,
) -> tuple[str, str]:
    if None in (fast_average, slow_average, current_return):
        return "資料不足", "unknown"
    if current > fast_average > slow_average and current_return > 0:
        return "上升趨勢", "positive"
    if current < fast_average < slow_average and current_return < 0:
        return "下降趨勢", "negative"
    if previous_return is not None and current_return * previous_return < 0:
        return "方向切換", "mixed"
    return "震盪分歧", "mixed"


def source_rows(price_history: dict[str, Any], symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    asset = price_history.get("assets", {}).get(symbol) or {}
    provider = asset.get("canonical_provider")
    source = (asset.get("sources") or {}).get(provider) or {}
    return source.get("rows") or [], asset


def asset_horizon(price_history: dict[str, Any], symbol: str, horizon: dict[str, Any]) -> dict[str, Any]:
    rows, asset = source_rows(price_history, symbol)
    closes = [number(row.get("close")) for row in rows]
    values = [value for value in closes if value is not None and value > 0]
    if not values:
        return {"status": "資料不足", "tone": "unknown", "bars": 0}
    return_bars = horizon["return_bars"]
    fast_average = moving_average(values, horizon["fast_bars"])
    slow_average = moving_average(values, horizon["slow_bars"])
    current_return = period_return(values, return_bars)
    previous_return = period_return(values, return_bars, return_bars)
    trend_return, trend_r_squared = log_trend(values, horizon["slow_bars"])
    state, tone = classify_state(values[-1], fast_average, slow_average, current_return, previous_return)
    annualization = 365 if asset.get("market") == "crypto" else 252
    return {
        "status": state,
        "tone": tone,
        "bars": len(values),
        "as_of": rows[-1].get("date"),
        "close": values[-1],
        "return": current_return,
        "previous_equal_window_return": previous_return,
        "return_acceleration": current_return - previous_return if current_return is not None and previous_return is not None else None,
        "fast_average": fast_average,
        "slow_average": slow_average,
        "distance_from_fast_average": values[-1] / fast_average - 1 if fast_average else None,
        "distance_from_slow_average": values[-1] / slow_average - 1 if slow_average else None,
        "trend_return": trend_return,
        "trend_r_squared": trend_r_squared,
        "realized_volatility_annualized": annualized_volatility(values, horizon["volatility_bars"], annualization),
        "range_position": range_position(values, horizon["range_bars"]),
        "drawdown_from_range_high": values[-1] / max(values[-horizon["range_bars"]:]) - 1 if len(values) >= horizon["range_bars"] else None,
        "canonical_provider": asset.get("canonical_provider"),
        "source_count": asset.get("source_count"),
    }


def direction_from_ratio(positive: int, total: int) -> str:
    if not total:
        return "unknown"
    ratio = positive / total
    if ratio >= 0.75:
        return "positive"
    if ratio <= 0.25:
        return "negative"
    return "mixed"


def perspective(
    name: str,
    cluster_id: str,
    direction: str,
    key_number: str,
    plain_read: str,
    source: str,
    counts_toward_underlying_resonance: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "cluster_id": cluster_id,
        "direction": direction,
        "key_number": key_number,
        "plain_read": plain_read,
        "source": source,
        "counts_toward_underlying_resonance": counts_toward_underlying_resonance,
    }


def resonance_from_perspectives(perspectives: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    cluster_directions: dict[str, set[str]] = {}
    for item in perspectives:
        if not item.get("counts_toward_underlying_resonance"):
            continue
        cluster_id = str(item.get("cluster_id") or "")
        direction = str(item.get("direction") or "")
        if not cluster_id:
            continue
        cluster_directions.setdefault(cluster_id, set())
        if direction in {"positive", "negative"}:
            cluster_directions[cluster_id].add(direction)
    positive = sum(directions == {"positive"} for directions in cluster_directions.values())
    negative = sum(directions == {"negative"} for directions in cluster_directions.values())
    directional = sum(directions in ({"positive"}, {"negative"}) for directions in cluster_directions.values())
    resonance = "偏正向共振" if positive >= 3 and positive > negative else "偏負向共振" if negative >= 3 and negative > positive else "多維訊號分歧"
    return resonance, {
        "positive_clusters": positive,
        "negative_clusters": negative,
        "directional_clusters": directional,
        "eligible_clusters": len(cluster_directions),
    }


def history_percentile(history: dict[str, Any], horizon: str, value: float | None) -> dict[str, Any]:
    observations = []
    seen_dates: set[str] = set()
    for item in reversed(history.get("items", [])):
        date = str(item.get("date") or "")
        if not date or date in seen_dates:
            continue
        seen_dates.add(date)
        candidate = number(nested(item, f"horizons.{horizon}.btc_return"))
        if candidate is not None:
            observations.append(candidate)
    if value is None or len(observations) < 20:
        return {"status": "insufficient_history", "observations": len(observations), "percentile": None}
    rank = sum(candidate <= value for candidate in observations) / len(observations)
    return {"status": "available", "observations": len(observations), "percentile": rank}


def prior_distinct_observation(history: dict[str, Any], current_date: str) -> dict[str, Any] | None:
    candidates = [item for item in history.get("items", []) if item.get("date") and item.get("date") != current_date]
    candidates.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("generated_at") or "")))
    return candidates[-1] if candidates else None


def horizon_perspectives(
    horizon_key: str,
    btc: dict[str, Any],
    asset_matrix: dict[str, Any],
    snapshot: dict[str, Any],
    market: dict[str, Any],
) -> list[dict[str, Any]]:
    tracked = [asset_matrix[symbol][horizon_key] for symbol in ("BTC", "ETH") if asset_matrix.get(symbol, {}).get(horizon_key)]
    known_returns = [number(item.get("return")) for item in tracked]
    known_returns = [value for value in known_returns if value is not None]
    positive_count = sum(value > 0 for value in known_returns)
    breadth_direction = direction_from_ratio(positive_count, len(known_returns))
    technical_direction = btc.get("tone", "unknown")
    perspectives = [
        perspective(
            "價格趨勢",
            "underlying_price_trend",
            technical_direction,
            format_percent(number(btc.get("return"))),
            f"BTC 為「{btc.get('status')}」；趨勢擬合度 {format_percent(number(btc.get('trend_r_squared')), 0)}。",
            "雙來源完成日 K 衍生",
        ),
        perspective(
            "加密底層廣度",
            "underlying_crypto_breadth",
            breadth_direction,
            f"{positive_count}/{len(known_returns)}",
            "BTC、ETH 同週期報酬的正負分布；MSTR、BMNR 上市載具不參與底層體制投票。",
            "雙來源完成日 K 衍生",
        ),
    ]
    radar = snapshot.get("metrics", {}).get("market_radar", {})
    mstr = snapshot.get("metrics", {}).get("mstr_metrics", {})
    thesis = market.get("btc_thesis", {})
    if horizon_key == "daily":
        funding = number(nested(market, "analysis.BTC.funding_annualized_median"))
        fear_greed = number(radar.get("fear_greed"))
        perspectives.extend([
            perspective("衍生品擁擠", "underlying_derivatives_positioning", "positive" if funding is not None and funding > 0.10 else "negative" if funding is not None and funding < 0 else "mixed", format_percent(funding), "永續資金費率只衡量槓桿偏向與持有成本。", "OKX＋Hyperliquid"),
            perspective("市場情緒", "underlying_market_sentiment", "positive" if fear_greed is not None and fear_greed >= 60 else "negative" if fear_greed is not None and fear_greed <= 40 else "mixed", f"{fear_greed:.0f}" if fear_greed is not None else "資料不足", "情緒數值描述風險偏好，不採反向或順勢策略假設。", "Alternative.me"),
        ])
    elif horizon_key == "weekly":
        etf_item = nested(market, "etf.BTC") or {}
        etf_flow = number(etf_item.get("flow_7d_usd"))
        etf_verified = etf_item.get("status") == "sample_cross_source_verified" and etf_flow is not None
        etf_key = f"${etf_flow / 1e6:+,.0f}M" if etf_verified else f"{int(number(etf_item.get('source_count')) or 0)} 源未過"
        sale_ratio = number(mstr.get("sale_ratio"))
        perspectives.extend([
            perspective("現貨 ETF 邊際流", "underlying_institutional_flows", "positive" if etf_verified and etf_flow > 0 else "negative" if etf_verified else "unknown", etf_key, "已驗證 7 日淨流才描述方向；目前未過 quorum 時只顯示來源診斷。", "ETF 多來源＋發行商持倉核對"),
            perspective("MSTR 資本結構", "vehicle_mstr_capital_structure", "negative" if sale_ratio is None or sale_ratio > 2 else "mixed", format_multiple(sale_ratio, 1), "已報告賣幣壓力與普通股價格趨勢分開觀察。", "Strategy SEC／公司揭露", False),
        ])
    elif horizon_key == "monthly":
        mvrv = number(radar.get("btc_mvrv_current"))
        common_ratio = number(mstr.get("common_equity_price_to_nav"))
        perspectives.extend([
            perspective("鏈上估值位置", "underlying_onchain_valuation", "negative" if mvrv is not None and mvrv < 1 else "positive" if mvrv is not None and mvrv > 2 else "mixed", format_multiple(mvrv), "MVRV 描述市場價相對實現價位置，不等同方向訊號。", "Coin Metrics Community API"),
            perspective("MSTR 普通股估值", "vehicle_mstr_relative_value", "negative" if common_ratio is not None and common_ratio > 1 else "positive" if common_ratio is not None else "unknown", format_multiple(common_ratio), "普通股市值／自算普通股淨值用來辨識估值與 BTC 趨勢是否背離。", "SEC＋市場價格衍生", False),
        ])
    else:
        hashrate_change = number(nested(thesis, "security_consensus.hashrate_30d_change"))
        company_share = number(nested(thesis, "public_company_adoption.share_of_btc_supply"))
        stablecoin_change = number(nested(thesis, "digital_dollar_competition.stablecoin_supply_30d_change"))
        perspectives.extend([
            perspective("網路安全活動", "underlying_network_security", "positive" if hashrate_change is not None and hashrate_change > 0 else "negative" if hashrate_change is not None else "unknown", format_percent(hashrate_change), "算力 30 日變化作為網路活動背景，不把單月變動外推為價格目標。", "Blockchain.com 多點序列"),
            perspective("結構性採用", "underlying_structural_adoption", "positive" if company_share is not None and company_share > 0.03 else "mixed", format_percent(company_share), f"公開公司持幣占供給；穩定幣供給 30 日 {format_percent(stablecoin_change)}。", "DAT 多來源＋SEC overlay"),
        ])
    return perspectives


def horizon_summary(
    horizon_key: str,
    horizon: dict[str, Any],
    asset_matrix: dict[str, Any],
    snapshot: dict[str, Any],
    market: dict[str, Any],
    history: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    btc = asset_matrix["BTC"][horizon_key]
    perspectives = horizon_perspectives(horizon_key, btc, asset_matrix, snapshot, market)
    resonance, resonance_votes = resonance_from_perspectives(perspectives)
    prior_state = nested(previous, f"horizons.{horizon_key}.status") if previous else None
    prior_return = number(nested(previous, f"horizons.{horizon_key}.btc_return")) if previous else None
    current_return = number(btc.get("return"))
    if prior_state and prior_state != btc.get("status"):
        what_changed = f"較前一觀察日由「{prior_state}」轉為「{btc.get('status')}」。"
    elif prior_return is not None and current_return is not None:
        what_changed = f"同週期 BTC 報酬較前一觀察日變化 {(current_return - prior_return) * 100:+.1f} 個百分點。"
    else:
        what_changed = "歷史觀察仍不足，先建立可比較基線。"
    acceleration = number(btc.get("return_acceleration"))
    acceleration_text = "加速" if acceleration is not None and acceleration > 0 else "減速" if acceleration is not None and acceleration < 0 else "持平"
    return {
        "label": horizon["label"],
        "status": btc.get("status"),
        "tone": btc.get("tone"),
        "key_number": format_percent(current_return),
        "plain_read": f"BTC {horizon['label']}報酬 {format_percent(current_return)}，目前屬「{btc.get('status')}」，相較前一等長窗口為{acceleration_text}。",
        "what_changed": what_changed,
        "resonance": resonance,
        "resonance_votes": resonance_votes,
        "perspectives": perspectives,
        "metrics": {
            "btc_return": current_return,
            "previous_equal_window_return": btc.get("previous_equal_window_return"),
            "return_acceleration": acceleration,
            "distance_from_fast_average": btc.get("distance_from_fast_average"),
            "distance_from_slow_average": btc.get("distance_from_slow_average"),
            "trend_r_squared": btc.get("trend_r_squared"),
            "realized_volatility_annualized": btc.get("realized_volatility_annualized"),
            "range_position": btc.get("range_position"),
            "drawdown_from_range_high": btc.get("drawdown_from_range_high"),
        },
        "historical_percentile": history_percentile(history, horizon_key, current_return),
        "data_depth": {
            "bars": btc.get("bars"),
            "as_of": btc.get("as_of"),
            "source_count": btc.get("source_count"),
            "canonical_provider": btc.get("canonical_provider"),
        },
        "falsifier": "若下一個完成窗口的均線排序、等長報酬方向與跨資產廣度同時反轉，本期狀態描述失效。",
    }


def alignment(horizons: dict[str, Any]) -> dict[str, Any]:
    states = {key: value.get("tone") for key, value in horizons.items()}
    positive = sum(tone == "positive" for tone in states.values())
    negative = sum(tone == "negative" for tone in states.values())
    known = sum(tone in {"positive", "negative", "mixed"} for tone in states.values())
    if positive >= 3:
        dominant = "多週期同步上行"
    elif negative >= 3:
        dominant = "多週期同步下行"
    elif positive and negative:
        dominant = "長短週期背離"
    else:
        dominant = "週期分歧／盤整"
    return {
        "dominant_state": dominant,
        "aligned_horizons": max(positive, negative),
        "known_horizons": known,
        "positive_horizons": positive,
        "negative_horizons": negative,
        "states": states,
        "plain_read": f"四個週期中 {positive} 個上升、{negative} 個下降；目前判讀為「{dominant}」。",
    }


def exclusive_insights(
    asset_matrix: dict[str, Any],
    horizons: dict[str, Any],
    snapshot: dict[str, Any],
    market: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    mstr = snapshot.get("metrics", {}).get("mstr_metrics", {})
    bmnr = snapshot.get("metrics", {}).get("bmnr_metrics", {})
    monthly_btc = number(asset_matrix["BTC"]["monthly"].get("return"))
    monthly_mstr = number(asset_matrix["MSTR"]["monthly"].get("return"))
    monthly_eth = number(asset_matrix["ETH"]["monthly"].get("return"))
    monthly_bmnr = number(asset_matrix["BMNR"]["monthly"].get("return"))
    mstr_relative = monthly_mstr - monthly_btc if monthly_mstr is not None and monthly_btc is not None else None
    bmnr_relative = monthly_bmnr - monthly_eth if monthly_bmnr is not None and monthly_eth is not None else None
    funding = number(nested(market, "analysis.BTC.funding_annualized_median"))
    etf_item = nested(market, "etf.BTC") or {}
    etf_flow = number(etf_item.get("flow_7d_usd"))
    etf_verified = etf_item.get("status") == "sample_cross_source_verified" and etf_flow is not None
    etf_source_count = int(number(etf_item.get("source_count")) or 0)
    etf_key = f"ETF ${etf_flow / 1e6:+,.0f}M" if etf_verified else f"ETF {etf_source_count} 源未過"
    weekly_btc = number(asset_matrix["BTC"]["weekly"].get("return"))
    gross_multiple = number(bmnr.get("market_cap_to_gross_treasury"))
    if gross_multiple is None:
        gross_multiple = number(bmnr.get("gross_treasury_multiple"))
    current_alignment = alignment(horizons)
    raw = [
        {
            "id": "multi_horizon_alignment",
            "title": "四週期同步程度",
            "key_number": f"{current_alignment['aligned_horizons']}/{current_alignment['known_horizons']}",
            "claim": current_alignment["plain_read"],
            "evidence": [f"日線 {horizons['daily']['status']}", f"週線 {horizons['weekly']['status']}", f"月線 {horizons['monthly']['status']}", f"季線 {horizons['quarterly']['status']}"],
            "falsifier": "任兩個完成週期的狀態方向翻轉，需重新分類同步程度。",
            "horizons": ["daily", "weekly", "monthly", "quarterly"],
        },
        {
            "id": "mstr_price_structure_divergence",
            "title": "MSTR 價格強弱與資本結構背離",
            "key_number": format_percent(mstr_relative),
            "claim": f"MSTR 月線相對 BTC {format_percent(mstr_relative)}；普通股市值／自算普通股淨值 {format_multiple(number(mstr.get('common_equity_price_to_nav')))}，STRC 折價 {format_percent(number(mstr.get('strc_discount')))}。",
            "evidence": ["MSTR 與 BTC 雙來源完成日 K", "SEC 資本結構", "STRC 市場價格"],
            "falsifier": "相對報酬、普通股估值與優先股信任票三者若轉為同方向，背離描述失效。",
            "horizons": ["monthly"],
        },
        {
            "id": "spot_leverage_divergence",
            "title": "現貨需求與槓桿定價差",
            "key_number": etf_key,
            "claim": f"BTC 週線 {format_percent(weekly_btc)}、ETF {'七日淨流 ' + f'${etf_flow / 1e6:+,.0f}M' if etf_verified else f'{etf_source_count} 個來源仍未通過 quorum'}、永續資金費率年化 {format_percent(funding)}；未驗證 ETF 不參與方向判讀。",
            "evidence": ["BTC 雙來源完成日 K", "ETF 多來源＋發行商核對", "OKX＋Hyperliquid 資金費率"],
            "falsifier": "ETF 流向與資金費率在下一完整週同向收斂，現貨／槓桿背離描述失效。",
            "horizons": ["weekly"],
        },
        {
            "id": "bmnr_eth_treasury_divergence",
            "title": "BMNR 相對 ETH 與 gross treasury 差",
            "key_number": format_percent(bmnr_relative),
            "claim": f"BMNR 月線相對 ETH {format_percent(bmnr_relative)}；市值／gross treasury {format_multiple(gross_multiple)}，質押比例 {format_percent(number(bmnr.get('staked_eth_ratio')))}。",
            "evidence": ["BMNR 與 ETH 雙來源完成日 K", "BMNR SEC 8-K 持倉", "股數與回購調整"],
            "falsifier": "完整負債與稀釋資料改變 gross treasury 解讀，或相對強弱方向反轉。",
            "horizons": ["monthly", "quarterly"],
        },
    ]
    previous_insights = {item.get("id"): item for item in (previous or {}).get("exclusive_insights", [])}
    for item in raw:
        prior = previous_insights.get(item["id"])
        item["what_changed"] = "首次建立可比較觀察。" if not prior else (
            "核心數字未變。" if prior.get("key_number") == item["key_number"] else f"前值 {prior.get('key_number')}，本期 {item['key_number']}。"
        )
        item["confidence"] = "中" if len(item["evidence"]) >= 3 else "中低"
    return raw


def compact_observation(analysis: dict[str, Any], revision: int, supersedes: str | None) -> dict[str, Any]:
    return {
        "date": analysis.get("date"),
        "generated_at": analysis.get("generated_at"),
        "revision": revision,
        "supersedes_generated_at": supersedes,
        "revision_note": "same-day source refresh; prior observation preserved" if supersedes else "first observation for this date",
        "quality_status": nested(analysis, "quality.status"),
        "horizons": {
            key: {
                "status": value.get("status"),
                "tone": value.get("tone"),
                "btc_return": nested(value, "metrics.btc_return"),
                "return_acceleration": nested(value, "metrics.return_acceleration"),
                "resonance": value.get("resonance"),
            }
            for key, value in analysis.get("horizons", {}).items()
        },
        "alignment": analysis.get("alignment"),
        "technical_horizons": {
            key: {
                "as_of": value.get("as_of"),
                "rsi_14": value.get("rsi_14"),
                "macd_histogram": nested(value, "macd.histogram"),
                "bottom_state": nested(value, "bottom_assessment.state"),
                "top_state": nested(value, "top_risk.state"),
            }
            for key, value in analysis.get("technical_horizons", {}).items()
        },
        "news_sentiment": {
            key: value.get("conclusion")
            for key, value in analysis.get("news_sentiment", {}).items()
            if isinstance(value, dict) and value.get("conclusion")
        },
        "exclusive_insights": [
            {"id": item.get("id"), "key_number": item.get("key_number"), "claim": item.get("claim")}
            for item in analysis.get("exclusive_insights", [])
        ],
    }


def main() -> int:
    price_history = load_json(PRICE_HISTORY_PATH)
    data_verification = load_json(DATA_VERIFICATION_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    daily_verification = load_json(DAILY_VERIFICATION_PATH)
    market = load_json(MARKET_PATH)
    market_verification = load_json(MARKET_VERIFICATION_PATH)
    context = load_json(CONTEXT_PATH)
    context_verification = load_json(CONTEXT_VERIFICATION_PATH)
    history = load_json(HISTORY_PATH, {"schema": 1, "items": []})
    previous = prior_distinct_observation(history, snapshot.get("date", ""))
    asset_matrix: dict[str, dict[str, Any]] = {}
    for symbol in ("BTC", "ETH", "MSTR", "BMNR", "STRC"):
        asset_matrix[symbol] = {key: asset_horizon(price_history, symbol, horizon) for key, horizon in HORIZONS.items()}
        for key in HORIZONS:
            btc_return = number(asset_matrix["BTC"][key].get("return")) if "BTC" in asset_matrix else None
            asset_return = number(asset_matrix[symbol][key].get("return"))
            asset_matrix[symbol][key]["relative_to_btc"] = asset_return - btc_return if symbol != "BTC" and asset_return is not None and btc_return is not None else 0.0 if symbol == "BTC" else None
    horizons = {
        key: horizon_summary(key, horizon, asset_matrix, snapshot, market, history, previous)
        for key, horizon in HORIZONS.items()
    }
    source_status = data_verification.get("status")
    daily_status = daily_verification.get("status")
    market_status = market_verification.get("status")
    context_status = context_verification.get("status")
    lineage_ok = (
        price_history.get("snapshot_generated_at") == snapshot.get("generated_at")
        and data_verification.get("snapshot_generated_at") == snapshot.get("generated_at")
        and daily_verification.get("snapshot_generated_at") == snapshot.get("generated_at")
        and market.get("snapshot_generated_at") == snapshot.get("generated_at")
    )
    verifier_bindings_ok = (
        market_verification.get("market_generated_at") == market.get("generated_at")
        and market_status != "fail"
        and context_verification.get("source_generated_at") == context.get("generated_at")
        and context_status != "fail"
    )
    failures = list(data_verification.get("failures") or [])
    if daily_status == "fail":
        failures.extend(daily_verification.get("failures") or ["daily verification failed"])
    if not lineage_ok:
        failures.append("timescale inputs are not bound to the same daily snapshot")
    if not verifier_bindings_ok:
        failures.append("market or context verifier is not bound to the current analysis inputs")
    degradations = list(data_verification.get("degradations") or []) + list(daily_verification.get("degradations") or []) + list(context_verification.get("degradations") or [])
    quality_status = "fail" if failures else "degraded" if "degraded" in {source_status, daily_status, market_status, context_status} else "pass"
    generated_at = now_iso()
    btc_rows, btc_asset = source_rows(price_history, "BTC")
    technical_horizons = {timeframe: technical_horizon(btc_rows, timeframe) for timeframe in ("weekly", "monthly")}
    for item in technical_horizons.values():
        item["canonical_provider"] = btc_asset.get("canonical_provider")
        item["source_count"] = btc_asset.get("source_count")
    news_sentiment = build_news_sentiment(snapshot, market, context)
    analysis = {
        "schema": 1,
        "date": snapshot.get("date"),
        "generated_at": generated_at,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "market_universe_generated_at": market.get("generated_at"),
        "market_context_generated_at": context.get("generated_at"),
        "price_history_generated_at": price_history.get("generated_at"),
        "quality": {
            "status": quality_status,
            "publication_mode": "diagnostics_only" if quality_status == "fail" else "analysis_only",
            "execution_gate_eligible": False,
            "failures": failures,
            "degradations": list(dict.fromkeys(degradations)),
            "lineage_bound": lineage_ok and verifier_bindings_ok,
            "method": "deterministic dual-source daily history plus completed weekly/monthly candle analysis and verified context synthesis",
        },
        "system": {
            "name": "四週期市場狀態判讀系統",
            "purpose": "累積價格、趨勢、廣度、槓桿、流向與資本結構證據；描述市場狀態，不輸出買賣策略。",
            "horizons": HORIZONS,
            "prohibited_outputs": ["買進", "賣出", "加碼", "減碼", "槓桿倍數", "部位比例", "目標價"],
        },
        "horizons": horizons if quality_status != "fail" else {},
        "alignment": alignment(horizons) if quality_status != "fail" else {"dominant_state": "資料封鎖", "plain_read": "必要資料或血緣驗證失敗。"},
        "asset_matrix": asset_matrix if quality_status != "fail" else {},
        "technical_horizons": technical_horizons if quality_status != "fail" else {},
        "news_sentiment": news_sentiment if quality_status != "fail" else {},
        "exclusive_insights": exclusive_insights(asset_matrix, horizons, snapshot, market, previous) if quality_status != "fail" else [],
        "record_advantage": {
            "observations": len(history.get("items", [])) + 1,
            "distinct_dates": len({item.get("date") for item in history.get("items", []) if item.get("date")} | {snapshot.get("date")}),
            "first_date": next((item.get("date") for item in history.get("items", []) if item.get("date")), snapshot.get("date")),
            "statistical_claim_minimum": 20,
            "plain_read": "歷史未達 20 個相異日期前，只顯示變化與基線，不宣稱分位數具有統計意義。",
        },
    }
    write_json(OUTPUT_PATH, analysis)
    same_date_items = [item for item in history.get("items", []) if item.get("date") == analysis["date"]]
    supersedes = same_date_items[-1].get("generated_at") if same_date_items else None
    observation = compact_observation(analysis, len(same_date_items) + 1, supersedes)
    items = list(history.get("items", []))
    items.append(observation)
    write_json(HISTORY_PATH, {
        "schema": 1,
        "updated_at": generated_at,
        "policy": "append-only observations; same-day refreshes carry revision, revision_note, and supersedes_generated_at; retain 3650 observations",
        "items": items[-3650:],
    })
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "history": str(HISTORY_PATH),
        "status": quality_status,
        "horizons": len(analysis["horizons"]),
        "history_observations": len(items),
    }, ensure_ascii=False))
    return 1 if quality_status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
