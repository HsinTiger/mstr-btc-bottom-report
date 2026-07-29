#!/usr/bin/env python3
"""Deterministic unit tests for four-horizon market analysis math."""

from __future__ import annotations

from generate_timescale_intelligence import (
    HORIZONS,
    alignment,
    asset_horizon,
    classify_state,
    history_percentile,
    log_trend,
    period_return,
    perspective,
    range_position,
    resonance_from_perspectives,
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
    print(f"timescale intelligence tests: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
