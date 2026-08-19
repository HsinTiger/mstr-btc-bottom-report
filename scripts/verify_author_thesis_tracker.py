#!/usr/bin/env python3
"""Independently re-derive the author-thesis tracker and fail closed on drift.

This verifier deliberately re-reads the inputs and the two upstream artifacts
itself rather than trusting anything the generator wrote: every claim value,
every falsifier verdict and the payload hash are recomputed from source. A
tracker that says a thesis is intact only publishes if the recomputation agrees.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
INPUT_PATH = ROOT / "data" / "inputs" / "author_theses.json"
CONTEXT_PATH = DATA_DIR / "market_context.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
SOURCE_PATH = DATA_DIR / "author_thesis_tracker.json"
OUTPUT_PATH = DATA_DIR / "author_thesis_tracker_verification.json"

MAX_AGE_HOURS = 30


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    import generate_author_thesis_tracker as generator  # recompute with the same rules

    source = load(SOURCE_PATH)
    inputs = load(INPUT_PATH)
    context = load(CONTEXT_PATH)
    market = load(MARKET_PATH)

    failures: list[str] = []
    degradations: list[str] = []

    def check(condition: bool, detail: str, *, hard: bool = True) -> None:
        if not condition:
            (failures if hard else degradations).append(detail)

    check(source.get("schema") == 1, "author thesis tracker schema 必須為 1")
    check(source.get("quality", {}).get("execution_gate_eligible") is False, "作者論點追蹤不得具備交易執行資格")
    check(source.get("quality", {}).get("scope") == "author_thesis_tracking_only", "作者論點追蹤 scope 不正確")
    check(source.get("context_generated_at") == context.get("generated_at"), "追蹤器未綁定目前這批 market context")
    check(source.get("context_payload_hash") == context.get("payload_hash"), "追蹤器綁定的 context payload hash 不一致")
    check(source.get("market_generated_at") == market.get("generated_at"), "追蹤器未綁定目前這批 market universe")
    check(source.get("input_updated_at") == inputs.get("updated_at"), "追蹤器未綁定目前這版論點輸入檔")

    generated = parse_time(source.get("generated_at"))
    age_hours = (datetime.now(timezone.utc) - generated).total_seconds() / 3600 if generated else None
    check(age_hours is not None and -1 <= age_hours <= MAX_AGE_HOURS, f"作者論點追蹤超過 {MAX_AGE_HOURS} 小時新鮮度契約")

    expected = generator.build(inputs, context, market)
    expected_by_id = {item["id"]: item for item in expected["theses"]}
    published_by_id = {item.get("id"): item for item in source.get("theses", [])}
    check(set(expected_by_id) == set(published_by_id), "追蹤器論點集合與輸入檔不一致")

    stated_by_id = {
        thesis.get("id"): {claim.get("id"): claim.get("stated_value") for claim in thesis.get("claims", [])}
        for thesis in inputs.get("theses", [])
    }
    for thesis_id, expected_thesis in expected_by_id.items():
        published = published_by_id.get(thesis_id)
        if not published:
            continue
        for name in ("claims", "falsifiers", "summary"):
            check(published.get(name) == expected_thesis[name], f"{thesis_id} 的 {name} 無法以相同規則重算")
        for claim in published.get("claims", []):
            original = stated_by_id.get(thesis_id, {}).get(claim.get("id"))
            check(
                original is None or float(claim.get("stated_value")) == float(original),
                f"{thesis_id}／{claim.get('id')}：文章原始數字被改寫",
            )
            check(
                claim.get("status") != "consistent" or claim.get("current_value") is not None,
                f"{thesis_id}／{claim.get('id')}：沒有現值卻標成一致",
                hard=True,
            )
        for falsifier in published.get("falsifiers", []):
            check(
                falsifier.get("status") != "untracked" or not falsifier.get("metric"),
                f"{thesis_id}／{falsifier.get('id')}：有指標卻標成未追蹤",
            )
            check(
                falsifier.get("status") not in {"triggered", "not_triggered"} or falsifier.get("current_value") is not None,
                f"{thesis_id}／{falsifier.get('id')}：沒有現值卻給出判定",
            )
        untracked = sum(item["status"] == "untracked" for item in published.get("falsifiers", []))
        check(
            untracked < len(published.get("falsifiers", []) or [1]),
            f"{thesis_id}：所有 falsifier 都無法追蹤，不應對外呈現為可追蹤論點",
            hard=False,
        )

    expected_hash = hashlib.sha256(
        json.dumps(
            {key: value for key, value in source.items() if key not in {"generated_at", "tracker_hash"}},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    check(source.get("tracker_hash") == expected_hash, "tracker_hash 與內容不一致")

    report = {
        "schema": 1,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_generated_at": source.get("generated_at"),
        "source_hash": source.get("tracker_hash"),
        "status": "fail" if failures else "degraded" if degradations else "pass",
        "failures": failures,
        "degradations": degradations,
        "theses_verified": len(published_by_id),
        "scope": "author_thesis_tracking_only",
        "execution_gate_eligible": False,
    }
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "path": str(OUTPUT_PATH),
        "status": report["status"],
        "failures": failures,
        "degradations": degradations,
    }, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
