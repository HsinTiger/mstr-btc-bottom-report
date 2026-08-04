#!/usr/bin/env python3
"""Deterministic unit tests for four-horizon market analysis math."""

from __future__ import annotations

from datetime import date, timedelta

from generate_timescale_intelligence import (
    HORIZONS,
    aggregate_completed_bars,
    alignment,
    asset_horizon,
    classify_state,
    detect_divergence,
    exponential_moving_average_series,
    history_percentile,
    log_trend,
    macd_series,
    period_return,
    perspective,
    range_position,
    resonance_from_perspectives,
    rsi_series,
    technical_horizon,
)
from verify_timescale_data import compare_sources, verify_rows


def series(values: list[float], market: str = "crypto") -> dict:
    rows = [
        {"date": f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}", "open": value, "high": value, "low": value, "close": value, "volume": 1}
        for index, value in enumerate(values)
    ]
    return {
        "assets": {
            "BTC": {
                "market": market,
                "canonical_provider": "primary",
                "source_count": 2,
                "sources": {"primary": {"rows": rows}},
            }
        }
    }


def dated_rows(values: list[float], start: date = date(2025, 1, 1)) -> list[dict]:
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": value,
            "high": value * 1.01,
            "low": value * 0.99,
            "close": value,
            "volume": 1000 + index,
        }
        for index, value in enumerate(values)
    ]


def main() -> int:
    checks = 0

    def expect(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    rising = [100 * (1.005 ** index) for index in range(320)]
    falling = [100 * (0.995 ** index) for index in range(320)]
    expect(abs(period_return(rising, 21) - (rising[-1] / rising[-22] - 1)) < 1e-12, "21-bar return mismatch")
    expect(period_return([1, 2], 5) is None, "insufficient history should be unknown")
    trend_return, trend_r_squared = log_trend(rising, 63)
    expect(trend_return is not None and trend_return > 0, "rising log trend should be positive")
    expect(trend_r_squared is not None and trend_r_squared > 0.999, "exponential series should have near-perfect fit")
    expect(range_position(rising, 20) == 1.0, "rising series should end at range high")
    expect(range_position(falling, 20) == 0.0, "falling series should end at range low")
    expect(classify_state(120, 110, 100, 0.1, 0.05) == ("上升趨勢", "positive"), "uptrend classification mismatch")
    expect(classify_state(80, 90, 100, -0.1, -0.05) == ("下降趨勢", "negative"), "downtrend classification mismatch")
    expect(classify_state(100, 101, 99, 0.02, -0.01) == ("方向切換", "mixed"), "direction-change classification mismatch")
    rising_daily = asset_horizon(series(rising), "BTC", HORIZONS["daily"])
    falling_quarterly = asset_horizon(series(falling), "BTC", HORIZONS["quarterly"])
    expect(rising_daily["status"] == "上升趨勢", "rising daily horizon mismatch")
    expect(falling_quarterly["status"] == "下降趨勢", "falling quarterly horizon mismatch")
    identical_rows = series(rising)["assets"]["BTC"]["sources"]["primary"]
    comparison = compare_sources(identical_rows, identical_rows)
    expect(comparison["median_close_gap"] == 0, "identical sources should have zero median gap")
    expect(max(comparison["period_return_gaps"].values()) == 0, "identical sources should have zero return gap")
    later_secondary = {"rows": identical_rows["rows"] + [{**identical_rows["rows"][-1], "date": "2026-12-31", "close": identical_rows["rows"][-1]["close"] * 1.2}]}
    aligned_comparison = compare_sources(identical_rows, later_secondary)
    expect(max(aligned_comparison["period_return_gaps"].values()) == 0, "cross-source returns must end on the latest shared observation date")
    expect(aligned_comparison["return_comparison_as_of"] == identical_rows["rows"][-1]["date"], "cross-source return cutoff must be explicit")
    expect(not verify_rows(identical_rows["rows"]), "valid rows should pass structural checks")
    percentile = history_percentile({"items": []}, "daily", 0.1)
    expect(percentile["status"] == "insufficient_history", "percentile must remain disabled below 20 dates")
    aligned = alignment({key: {"tone": "positive"} for key in HORIZONS})
    expect(aligned["dominant_state"] == "多週期同步上行", "alignment classification mismatch")
    two_votes = [perspective(str(index), f"cluster_{index}", "positive", "1", "test", "fixture") for index in range(2)]
    expect(resonance_from_perspectives(two_votes)[0] == "多維訊號分歧", "two clusters must not claim resonance")
    three_votes = [perspective(str(index), f"cluster_{index}", "positive", "1", "test", "fixture") for index in range(3)]
    expect(resonance_from_perspectives(three_votes)[0] == "偏正向共振", "three independent clusters should claim resonance")
    duplicate_cluster = three_votes[:2] + [perspective("duplicate", "cluster_1", "positive", "1", "test", "fixture")]
    expect(resonance_from_perspectives(duplicate_cluster)[0] == "多維訊號分歧", "duplicate cluster must count once")
    vehicle_vote = three_votes[:2] + [perspective("MSTR", "vehicle_mstr", "positive", "1", "test", "fixture", False)]
    expect(resonance_from_perspectives(vehicle_vote)[0] == "多維訊號分歧", "vehicle evidence must not vote in underlying resonance")
    calendar_rows = dated_rows([100 + index for index in range(41)], date(2026, 1, 1))
    weekly = aggregate_completed_bars(calendar_rows, "weekly")
    monthly = aggregate_completed_bars(calendar_rows, "monthly")
    expect(weekly[-1]["period_end"] == "2026-02-08", "weekly aggregation must exclude the incomplete current week")
    expect(monthly[-1]["period_end"] == "2026-01-31", "monthly aggregation must exclude the incomplete current month")
    expect(monthly[-1]["open"] == 100 and monthly[-1]["close"] == 130, "monthly OHLC aggregation mismatch")
    rising_rsi = rsi_series([float(index) for index in range(1, 22)], 14)
    expect(rising_rsi[-1] == 100.0, "Wilder RSI should be 100 for an uninterrupted rise")
    ema = exponential_moving_average_series([float(index) for index in range(1, 40)], 12)
    expect(ema[-1] is not None and ema[-1] > ema[-2], "EMA should rise with a rising input series")
    macd_line, signal_line, histogram = macd_series([float(index) for index in range(1, 80)])
    expect(macd_line[-1] is not None and macd_line[-1] > 0, "MACD line should be positive for a persistent rise")
    expect(signal_line[-1] is not None and histogram[-1] is not None, "MACD signal and histogram should be available with sufficient history")
    bullish_bars = dated_rows([100, 90, 95, 85, 92])
    bullish = detect_divergence(bullish_bars, [50, 20, 40, 30, 45], "bottom", radius=1)
    expect(bullish["detected"] and bullish["price_direction"] == "lower_low", "bullish divergence must require a lower price low and higher oscillator low")
    bearish_bars = dated_rows([100, 110, 105, 115, 109])
    bearish = detect_divergence(bearish_bars, [50, 80, 60, 70, 55], "top", radius=1)
    expect(bearish["detected"] and bearish["price_direction"] == "higher_high", "bearish divergence must require a higher price high and lower oscillator high")
    technical = technical_horizon(dated_rows([100 * (1.002 ** index) for index in range(900)]), "weekly")
    expect(technical["bar_basis"] == "completed_weekly_candles" and technical["bars"] > 100, "weekly technical layer must use completed weekly candles")
    expect({"rsi_14", "macd", "volume", "obv", "atr_14", "moving_averages", "divergence", "leading_signals", "lagging_confirmations", "invalidation"}.issubset(technical), "technical layer is missing required institutional fields")
    print(f"timescale intelligence tests: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
