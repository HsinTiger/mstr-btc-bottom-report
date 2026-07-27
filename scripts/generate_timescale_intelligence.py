#!/usr/bin/env python3
"""Generate deterministic daily, weekly, monthly, and quarterly market intelligence."""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
PRICE_HISTORY_PATH = DATA_DIR / "timescale_price_history.json"
DATA_VERIFICATION_PATH = DATA_DIR / "timescale_data_verification.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
DAILY_VERIFICATION_PATH = DATA_DIR / "agent_verification_report.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
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


def perspective(name: str, direction: str, key_number: str, plain_read: str, source: str) -> dict[str, str]:
    return {"name": name, "direction": direction, "key_number": key_number, "plain_read": plain_read, "source": source}


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
) -> list[dict[str, str]]:
    tracked = [asset_matrix[symbol][horizon_key] for symbol in ("BTC", "ETH", "MSTR", "BMNR") if asset_matrix.get(symbol, {}).get(horizon_key)]
    known_returns = [number(item.get("return")) for item in tracked]
    known_returns = [value for value in known_returns if value is not None]
    positive_count = sum(value > 0 for value in known_returns)
    breadth_direction = direction_from_ratio(positive_count, len(known_returns))
    technical_direction = btc.get("tone", "unknown")
    perspectives = [
        perspective(
            "價格趨勢",
            technical_direction,
            format_percent(number(btc.get("return"))),
            f"BTC 為「{btc.get('status')}」；趨勢擬合度 {format_percent(number(btc.get('trend_r_squared')), 0)}。",
            "雙來源完成日 K 衍生",
        ),
        perspective(
            "跨資產廣度",
            breadth_direction,
            f"{positive_count}/{len(known_returns)}",
            "BTC、ETH、MSTR、BMNR 同週期報酬的正負分布；只描述廣度，不形成交易動作。",
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
            perspective("衍生品擁擠", "positive" if funding is not None and funding > 0.10 else "negative" if funding is not None and funding < 0 else "mixed", format_percent(funding), "永續資金費率只衡量槓桿偏向與持有成本。", "OKX＋Hyperliquid"),
            perspective("市場情緒", "positive" if fear_greed is not None and fear_greed >= 60 else "negative" if fear_greed is not None and fear_greed <= 40 else "mixed", f"{fear_greed:.0f}" if fear_greed is not None else "資料不足", "情緒數值描述風險偏好，不採反向或順勢策略假設。", "Alternative.me"),
        ])
    elif horizon_key == "weekly":
        etf_item = nested(market, "etf.BTC") or {}
        etf_flow = number(etf_item.get("flow_7d_usd"))
        etf_verified = etf_item.get("status") == "sample_cross_source_verified" and etf_flow is not None
        etf_key = f"${etf_flow / 1e6:+,.0f}M" if etf_verified else f"{int(number(etf_item.get('source_count')) or 0)} 源未過"
        sale_ratio = number(mstr.get("sale_ratio"))
        perspectives.extend([
            perspective("現貨 ETF 邊際流", "positive" if etf_verified and etf_flow > 0 else "negative" if etf_verified else "unknown", etf_key, "已驗證 7 日淨流才描述方向；目前未過 quorum 時只顯示來源診斷。", "ETF 多來源＋發行商持倉核對"),
            perspective("MSTR 資本結構", "negative" if sale_ratio is None or sale_ratio > 2 else "mixed", format_multiple(sale_ratio, 1), "已報告賣幣壓力與普通股價格趨勢分開觀察。", "Strategy SEC／公司揭露"),
        ])
    elif horizon_key == "monthly":
        mvrv = number(radar.get("btc_mvrv_current"))
        common_ratio = number(mstr.get("common_equity_price_to_nav"))
        perspectives.extend([
            perspective("鏈上估值位置", "negative" if mvrv is not None and mvrv < 1 else "positive" if mvrv is not None and mvrv > 2 else "mixed", format_multiple(mvrv), "MVRV 描述市場價相對實現價位置，不等同方向訊號。", "Coin Metrics Community API"),
            perspective("MSTR 普通股估值", "negative" if common_ratio is not None and common_ratio > 1 else "positive" if common_ratio is not None else "unknown", format_multiple(common_ratio), "普通股市值／自算普通股淨值用來辨識估值與 BTC 趨勢是否背離。", "SEC＋市場價格衍生"),
        ])
    else:
        hashrate_change = number(nested(thesis, "security_consensus.hashrate_30d_change"))
        company_share = number(nested(thesis, "public_company_adoption.share_of_btc_supply"))
        stablecoin_change = number(nested(thesis, "digital_dollar_competition.stablecoin_supply_30d_change"))
        perspectives.extend([
            perspective("網路安全活動", "positive" if hashrate_change is not None and hashrate_change > 0 else "negative" if hashrate_change is not None else "unknown", format_percent(hashrate_change), "算力 30 日變化作為網路活動背景，不把單月變動外推為價格目標。", "Blockchain.com 多點序列"),
            perspective("結構性採用", "positive" if company_share is not None and company_share > 0.03 else "mixed", format_percent(company_share), f"公開公司持幣占供給；穩定幣供給 30 日 {format_percent(stablecoin_change)}。", "DAT 多來源＋SEC overlay"),
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
    known_directions = [item["direction"] for item in perspectives if item["direction"] in {"positive", "negative"}]
    positive = known_directions.count("positive")
    negative = known_directions.count("negative")
    resonance = "偏正向共振" if positive >= 2 and positive > negative else "偏負向共振" if negative >= 2 and negative > positive else "多維訊號分歧"
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
    lineage_ok = (
        price_history.get("snapshot_generated_at") == snapshot.get("generated_at")
        and data_verification.get("snapshot_generated_at") == snapshot.get("generated_at")
        and daily_verification.get("snapshot_generated_at") == snapshot.get("generated_at")
        and market.get("snapshot_generated_at") == snapshot.get("generated_at")
    )
    failures = list(data_verification.get("failures") or [])
    if daily_status == "fail":
        failures.extend(daily_verification.get("failures") or ["daily verification failed"])
    if not lineage_ok:
        failures.append("timescale inputs are not bound to the same daily snapshot")
    degradations = list(data_verification.get("degradations") or []) + list(daily_verification.get("degradations") or [])
    quality_status = "fail" if failures else "degraded" if source_status == "degraded" or daily_status == "degraded" else "pass"
    generated_at = now_iso()
    analysis = {
        "schema": 1,
        "date": snapshot.get("date"),
        "generated_at": generated_at,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "market_universe_generated_at": market.get("generated_at"),
        "price_history_generated_at": price_history.get("generated_at"),
        "quality": {
            "status": quality_status,
            "publication_mode": "diagnostics_only" if quality_status == "fail" else "analysis_only",
            "execution_gate_eligible": False,
            "failures": failures,
            "degradations": list(dict.fromkeys(degradations)),
            "lineage_bound": lineage_ok,
            "method": "deterministic dual-source completed-bar analysis",
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
