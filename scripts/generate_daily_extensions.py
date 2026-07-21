#!/usr/bin/env python3
"""Generate daily three-viewpoint research notes from verified data + wiki_llm."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
VERIFY_PATH = DATA_DIR / "agent_verification_report.json"
EXTENSIONS_PATH = DATA_DIR / "daily_extensions.json"
WIKI_PATH = ROOT / "wiki_llm.md"


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


def fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def pct(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def decision_label(decision: dict[str, Any]) -> str:
    labels = {
        "BLOCK_LEVERAGED_ADD": "\u7981\u6b62\u5c0f\u5009\u5408\u7d04\u52a0\u78bc",
        "WATCH_ONLY_NO_CHASE": "\u53ef\u5217\u5165\u89c0\u5bdf\uff0c\u4e0d\u81ea\u52d5\u8ffd\u50f9",
    }
    return labels.get(decision.get("state_code"), str(decision.get("state", "N/A")))



def wiki_signals() -> list[str]:
    if not WIKI_PATH.exists():
        return []
    text = WIKI_PATH.read_text(encoding="utf-8-sig")
    matches = re.findall(r"^- `([^`]+)`：([^\n]+)", text, flags=re.M)
    return [f"{name}: {desc}" for name, desc in matches[:6]]


def build_viewpoints(snapshot: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, str]]:
    metrics = snapshot["metrics"]["mstr_metrics"]
    btc_standard = snapshot["metrics"].get("btc_standard", {})
    bmnr = snapshot["metrics"].get("bmnr_metrics", {})
    prices = snapshot["metrics"]["prices"]
    decision = snapshot["decision"]
    status = verification.get("status")
    confidence = "低" if status == "fail" else "中低" if status == "degraded" else "中" if verification.get("warnings") else "高"

    return [
        {
            "title": "BTC 市場狀態與載具風險必須分開",
            "lens": "BTC 估值／趨勢／情緒／ETF／週期回撤",
            "claim": f"BTC 標準分={fmt(btc_standard.get('score'), 1)}、regime={btc_standard.get('regime')}、BTC={fmt(prices.get('btc_usd'), 0)}。",
            "so_what": btc_standard.get("action", "資料不足，不做方向判斷"),
            "study_note": "BTC regime 只決定現貨節奏；MSTR 與 BMNR 的資本結構、股數與 senior claims 另設 implementation overlay，避免把載具壓力誤算成 BTC 過熱。",
            "confidence": confidence,
        },
        {
            "title": "MSTR 的 7 日賣幣壓力只看滾動官方事件窗，不沿用舊交易",
            "lens": "7 日 reported sales／現金覆蓋／STRC 信任票",
            "claim": f"7 日賣幣壓力倍數={fmt(metrics.get('sale_ratio'), 1)}x、現金覆蓋月數={fmt(metrics.get('coverage_months'), 1)}、STRC 折價={pct(metrics.get('strc_discount'))}。",
            "so_what": f"今日狀態：{decision_label(decision)}；若每週賣幣壓力連續高於 2 倍，或 STRC 優先股折價高於 5%，估值回升也要降權。",
            "study_note": "舊版把最近一筆交易絕對值當成每週壓力，會讓 15 天前的賣幣持續污染今日判斷；新版改為官方 ledger 的 rolling 7-day sales。",
            "confidence": confidence,
        },
        {
            "title": "BMNR 折價只能先稱 gross treasury 折價，不能偷換成普通股淨值",
            "lens": "官方 ETH/BTC 持倉／回購後估計股數／gross treasury",
            "claim": f"BMNR={fmt(prices.get('bmnr_usd'))}、ETH 持倉={fmt(bmnr.get('eth_holdings'), 0)}、市值/gross treasury={fmt(bmnr.get('market_cap_to_gross_treasury'))}x、質押比例={pct(bmnr.get('staked_eth_ratio'))}。",
            "so_what": "可用於比較市場定價與明示資產，但完整負債、優先股與或有項目未扣除前，不把折價直接視為安全邊際。",
            "study_note": "這是大倉 4:1 配置中最重要的新治理規則：MSTR 用 net-to-common 框架，BMNR 暫用 gross-asset 框架，兩者不可用同一個 mNAV 名稱混算。",
            "confidence": confidence,
        },
    ]


def tomorrow_watch(snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot["metrics"]["mstr_metrics"]
    return {
        "date": (date.fromisoformat(snapshot["date"]) + timedelta(days=1)).isoformat(),
        "type": "tomorrow_watch",
        "title": "明日觀察清單，不是未來資料",
        "watch_items": [
            "BTC 與 MSTR 是否同向修復，避免只有 MSTR 反身性反彈",
            f"STRC 折價是否回到 5% 以下，目前 {pct(metrics.get('strc_discount'))}",
            f"週賣幣比值是否跌回 2 以下，目前 {fmt(metrics.get('sale_ratio'), 1)}x",
        ],
    }


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    verification = load_json(VERIFY_PATH)
    existing = load_json(EXTENSIONS_PATH, {"schema": 1, "items": [], "archive": []})
    today_item = {
        "date": snapshot["date"],
        "generated_at": now_iso(),
        "type": "daily_extension",
        "verification_status": verification.get("status"),
        "verification_warnings": verification.get("warnings", []),
        "decision_state": decision_label(snapshot.get("decision", {})),
        "decision_state_code": snapshot.get("decision", {}).get("state_code"),
        "viewpoints": build_viewpoints(snapshot, verification),
        "wiki_study_inputs": wiki_signals(),
    }

    items = [item for item in existing.get("items", []) if item.get("date") != today_item["date"] or item.get("type") != "daily_extension"]
    items.append(today_item)
    items.sort(key=lambda item: item.get("date", ""))
    cutoff = date.fromisoformat(snapshot["date"]) - timedelta(days=1)
    visible_dates = {cutoff.isoformat(), snapshot["date"]}
    visible = [item for item in items if item.get("date") in visible_dates]
    archive_candidates = existing.get("archive", []) + [item for item in items if item.get("date") not in visible_dates]
    archive_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in archive_candidates:
        archive_by_key[(str(item.get("date")), str(item.get("type")))] = item
    archive = sorted(archive_by_key.values(), key=lambda item: item.get("date", ""))
    tomorrow = tomorrow_watch(snapshot)
    output = {
        "schema": 1,
        "updated_at": now_iso(),
        "current_date": snapshot["date"],
        "visible_window": visible + [tomorrow],
        "items": visible,
        "archive": archive[-730:],
    }
    write_json(EXTENSIONS_PATH, output)
    print(json.dumps({"visible": len(output["visible_window"]), "archive": len(output["archive"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
