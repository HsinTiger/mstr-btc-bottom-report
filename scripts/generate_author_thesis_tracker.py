#!/usr/bin/env python3
"""Track the author's published Substack theses against verified site data.

The input file records what the article actually said, including the number and
the date it was said on. Nothing here rewrites those numbers; the tracker only
recomputes the same metric from this site's already-verified artifacts and
reports whether the stated reading still holds, has drifted, or was never
trackable here in the first place.

Falsifiers are the article's own "what would change my mind" signals turned
into evaluable conditions. A falsifier with no matching site metric stays
``untracked`` — it is never quietly dropped, because the gaps are the honest
part of the picture.

Research only. This artifact must never gate execution.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
INPUT_PATH = ROOT / "data" / "inputs" / "author_theses.json"
CONTEXT_PATH = DATA_DIR / "market_context.json"
CONTEXT_VERIFY_PATH = DATA_DIR / "market_context_verification.json"
MARKET_PATH = DATA_DIR / "market_universe.json"
MARKET_VERIFY_PATH = DATA_DIR / "market_universe_verification.json"
OUTPUT_PATH = DATA_DIR / "author_thesis_tracker.json"

OPERATORS = {
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve(roots: dict[str, Any], pointer: str) -> tuple[float | None, str | None]:
    """Walk a dotted pointer such as ``context.onchain.BTC.valuation.mvrv.value``.

    Returns the value plus the nearest ``as_of`` found on the way out, so a
    metric always travels with its own observation date instead of borrowing
    the batch time.
    """
    parts = pointer.split(".")
    node: Any = roots
    as_of: str | None = None
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None, None
        if isinstance(node.get("as_of"), str):
            as_of = node["as_of"]
        node = node[part]
    if isinstance(node, dict):
        if isinstance(node.get("as_of"), str):
            as_of = node["as_of"]
        return None, as_of
    return finite(node), as_of


def evaluate_claim(claim: dict[str, Any], roots: dict[str, Any]) -> dict[str, Any]:
    stated = finite(claim.get("stated_value"))
    pointer = claim.get("metric")
    current, as_of = resolve(roots, pointer) if pointer else (None, None)
    tolerance = finite(claim.get("tolerance"))
    tolerance = 0.05 if tolerance is None else tolerance
    drift = None
    if stated not in (None, 0) and current is not None:
        drift = current / stated - 1
    if current is None:
        status = "unavailable"
    elif drift is None:
        status = "unavailable"
    elif abs(drift) <= tolerance:
        status = "consistent"
    else:
        status = "drifted"
    return {
        "id": claim.get("id"),
        "label": claim.get("label"),
        "unit": claim.get("unit"),
        "metric": pointer,
        "stated_value": stated,
        "stated_as_of": claim.get("stated_as_of"),
        "current_value": current,
        "current_as_of": as_of,
        "tolerance": tolerance,
        "drift": drift,
        "status": status,
    }


def evaluate_falsifier(falsifier: dict[str, Any], roots: dict[str, Any]) -> dict[str, Any]:
    pointer = falsifier.get("metric")
    operator = falsifier.get("operator")
    threshold = finite(falsifier.get("threshold"))
    result = {
        "id": falsifier.get("id"),
        "label": falsifier.get("label"),
        "meaning": falsifier.get("meaning"),
        "metric": pointer,
        "operator": operator,
        "threshold": threshold,
        "current_value": None,
        "current_as_of": None,
        "status": "untracked",
    }
    if not pointer:
        return result
    if operator not in OPERATORS or threshold is None:
        result["status"] = "unavailable"
        return result
    current, as_of = resolve(roots, pointer)
    result["current_value"] = current
    result["current_as_of"] = as_of
    if current is None:
        result["status"] = "unavailable"
    else:
        result["status"] = "triggered" if OPERATORS[operator](current, threshold) else "not_triggered"
    return result


def summarize(claims: list[dict[str, Any]], falsifiers: list[dict[str, Any]]) -> dict[str, Any]:
    tracked = [item for item in claims if item["status"] != "unavailable"]
    consistent = [item for item in tracked if item["status"] == "consistent"]
    triggered = [item for item in falsifiers if item["status"] == "triggered"]
    evaluable = [item for item in falsifiers if item["status"] in {"triggered", "not_triggered"}]
    if not tracked:
        standing = "untracked"
    elif triggered:
        standing = "falsifier_triggered"
    elif len(consistent) == len(tracked):
        standing = "intact"
    else:
        standing = "numbers_drifted"
    return {
        "standing": standing,
        "claims_total": len(claims),
        "claims_tracked": len(tracked),
        "claims_consistent": len(consistent),
        "claims_drifted": len(tracked) - len(consistent),
        "falsifiers_total": len(falsifiers),
        "falsifiers_evaluable": len(evaluable),
        "falsifiers_triggered": len(triggered),
        "triggered_ids": [item["id"] for item in triggered],
    }


def build(source: dict[str, Any], context: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    roots = {"context": context, "universe": market}
    theses: list[dict[str, Any]] = []
    for thesis in source.get("theses", []):
        claims = [evaluate_claim(claim, roots) for claim in thesis.get("claims", [])]
        falsifiers = [evaluate_falsifier(item, roots) for item in thesis.get("falsifiers", [])]
        theses.append({
            "id": thesis.get("id"),
            "asset": thesis.get("asset"),
            "authored_on": thesis.get("authored_on"),
            "title": thesis.get("title"),
            "subtitle": thesis.get("subtitle"),
            "stance": thesis.get("stance"),
            "core_argument": thesis.get("core_argument"),
            "source": thesis.get("source", {}),
            "self_reported_audit_warnings": thesis.get("self_reported_audit_warnings", []),
            "untracked_claims": thesis.get("untracked_claims", []),
            "claims": claims,
            "falsifiers": falsifiers,
            "summary": summarize(claims, falsifiers),
        })
    payload = {
        "schema": 1,
        "date": context.get("date"),
        "generated_at": now_iso(),
        "input_updated_at": source.get("updated_at"),
        "context_generated_at": context.get("generated_at"),
        "context_payload_hash": context.get("payload_hash"),
        "market_generated_at": market.get("generated_at"),
        "market_source_batch_id": market.get("source_batch_id"),
        "quality": {
            "status": "pass",
            "scope": "author_thesis_tracking_only",
            "execution_gate_eligible": False,
            "method": "文章寫下的數字永遠保留原值；current 一律由本站已獨立驗證的 market_context / market_universe 重算，缺指標就標 unavailable 或 untracked，不補值、不換算、不猜。",
        },
        "theses": theses,
    }
    payload["tracker_hash"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in payload.items() if key != "generated_at"},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def main() -> int:
    source = load(INPUT_PATH)
    context = load(CONTEXT_PATH)
    market = load(MARKET_PATH)
    context_verification = load(CONTEXT_VERIFY_PATH)
    market_verification = load(MARKET_VERIFY_PATH)

    blocked: list[str] = []
    if context_verification.get("source_generated_at") != context.get("generated_at"):
        blocked.append("market_context verifier 未綁定目前這批 context")
    if context_verification.get("status") not in {"pass", "degraded"}:
        blocked.append("market_context 未通過獨立驗證")
    if market_verification.get("market_generated_at") != market.get("generated_at"):
        blocked.append("market_universe verifier 未綁定目前這批市場總表")
    if market_verification.get("status") not in {"pass", "degraded"}:
        blocked.append("market_universe 未通過獨立驗證")
    if blocked:
        print(json.dumps({"output": str(OUTPUT_PATH), "status": "fail", "failures": blocked}, ensure_ascii=False))
        return 1

    payload = build(source, context, market)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "status": payload["quality"]["status"],
        "theses": len(payload["theses"]),
        "standings": [item["summary"]["standing"] for item in payload["theses"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
