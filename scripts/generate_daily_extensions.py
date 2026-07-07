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


def wiki_signals() -> list[str]:
    if not WIKI_PATH.exists():
        return []
    text = WIKI_PATH.read_text(encoding="utf-8-sig")
    matches = re.findall(r"^- `([^`]+)`：([^\n]+)", text, flags=re.M)
    return [f"{name}: {desc}" for name, desc in matches[:6]]


def build_viewpoints(snapshot: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, str]]:
    metrics = snapshot["metrics"]["mstr_metrics"]
    prices = snapshot["metrics"]["prices"]
    decision = snapshot["decision"]
    verified = verification.get("status") == "pass"
    confidence = "高" if verified and not verification.get("warnings") else "中"

    return [
        {
            "title": "結構先於價格：今天不是看 MSTR 漲跌，而是看普通股是否重新拿回安全邊際",
            "lens": "M1/M2/M3",
            "claim": f"M1={fmt(metrics.get('equity_mnav'))}x、M2={fmt(metrics.get('enterprise_mnav'))}x、稀釋旗標={metrics.get('pref_dilution_flag')}；第二等份 gate={metrics.get('mnav_gate_ok')}。",
            "so_what": "小倉 2.5x 合約只在 gate 打開且紅燈解除時討論；否則最多是研究，不是行動訊號。",
            "study_note": "從 wiki_llm 的 mNAV 定義權風險延伸：公司口徑可被融資結構改寫，因此觸發條件必須使用自算口徑。",
            "confidence": confidence,
        },
        {
            "title": "流動性紅燈比便宜更重要：優先股折價是市場對融資機器的信任票",
            "lens": "M4/M5/M7",
            "claim": f"覆蓋月數={fmt(metrics.get('coverage_months'), 1)}、週賣幣比值={fmt(metrics.get('sale_ratio'), 1)}x、STRC 折價={pct(metrics.get('strc_discount'))}。",
            "so_what": f"今日狀態：{decision.get('state')}；若 M5 連續高於 2 或 M7 高於 5%，mNAV 回升也降權。",
            "study_note": "從 delayed pro-cyclical 類比延伸：結構性賣壓常先被 senior tranche 吸收，等信任票破裂才同步反映到普通股。",
            "confidence": confidence,
        },
        {
            "title": "大倉與小倉分離：MSTR/BMNR 現貨 4:1 看週期，合約只吃低頻確認後的波段",
            "lens": "Portfolio split",
            "claim": f"BTC={fmt(prices.get('btc_usd'), 0)}、MSTR={fmt(prices.get('mstr_usd'))}、BMNR={fmt(prices.get('bmnr_usd'))}；資料驗證={verification.get('status')}。",
            "so_what": "大倉用 1–4 年週期容忍波動；小倉用紅燈/綠燈避免在融資壓力區追多。",
            "study_note": "從 indicator regime change 延伸：Pi Cycle/MVRV 仍可當背景，但不可凌駕 ETF、公司融資與優先股折價。",
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
        "decision_state": snapshot.get("decision", {}).get("state"),
        "viewpoints": build_viewpoints(snapshot, verification),
        "wiki_study_inputs": wiki_signals(),
    }

    items = [item for item in existing.get("items", []) if item.get("date") != today_item["date"] or item.get("type") != "daily_extension"]
    items.append(today_item)
    items.sort(key=lambda item: item.get("date", ""))
    cutoff = date.fromisoformat(snapshot["date"]) - timedelta(days=1)
    visible_dates = {cutoff.isoformat(), snapshot["date"]}
    visible = [item for item in items if item.get("date") in visible_dates]
    archive = existing.get("archive", []) + [item for item in items if item.get("date") not in visible_dates]
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
