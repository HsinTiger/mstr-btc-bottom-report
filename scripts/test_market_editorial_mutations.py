#!/usr/bin/env python3
"""Prove the market editorial verifier rejects content, lineage, and history mutations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import verify_market_editorial as verifier
from smoke_production_market_editorial import validate_json_binding

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "daily"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8-sig"))


BASE = {
    "source": load("market_editorial.json"),
    "history": load("market_editorial_history.json"),
    "context": load("market_context.json"),
    "context_verify": load("market_context_verification.json"),
    "market": load("market_universe.json"),
    "market_verify": load("market_universe_verification.json"),
    "timescale": load("timescale_intelligence.json"),
    "timescale_verify": load("timescale_intelligence_verification.json"),
    "snapshot": load("latest_snapshot.json"),
    "knowledge": load("knowledge_context.json"),
}


def run(source: dict, history: dict, *, market: dict | None = None, market_verify: dict | None = None) -> dict:
    return verifier.verify(
        source,
        history,
        BASE["context"],
        BASE["context_verify"],
        market if market is not None else BASE["market"],
        market_verify if market_verify is not None else BASE["market_verify"],
        BASE["timescale"],
        BASE["timescale_verify"],
        BASE["snapshot"],
        BASE["knowledge"],
    )


def rehash_source(source: dict) -> None:
    source["editorial_hash"] = verifier.canonical_hash(verifier.without(source, "editorial_hash"))


def rehash_brief(brief: dict) -> None:
    brief["brief_hash"] = verifier.canonical_hash(verifier.without(brief, "brief_hash"))


def sync_latest_history(source: dict, history: dict, brief_index: int | None = None) -> None:
    latest = history["runs"][-1]
    latest["editorial_hash"] = source["editorial_hash"]
    if brief_index is not None:
        brief = source["desks"][brief_index]
        compact = latest["briefs"][brief_index]
        compact.update({
            "headline": brief["headline"],
            "brief_hash": brief["brief_hash"],
            "resonance_state": brief["resonance"]["state"],
            "evidence_fingerprint": verifier.canonical_hash([
                {"metric_id": item["metric_id"], "value": item["value"], "as_of": item["as_of"]}
                for item in brief["evidence"]
            ]),
        })
    latest["run_hash"] = verifier.canonical_hash(verifier.without(latest, "run_hash"))
    history["head_hash"] = latest["run_hash"]


def expect_rejected(name: str, mutate) -> None:
    source = copy.deepcopy(BASE["source"])
    history = copy.deepcopy(BASE["history"])
    mutate(source, history)
    report = run(source, history)
    if report["status"] != "fail":
        raise AssertionError(f"{name} mutation unexpectedly passed")


def main() -> int:
    baseline = run(copy.deepcopy(BASE["source"]), copy.deepcopy(BASE["history"]))
    if baseline["status"] != "pass":
        raise AssertionError(f"baseline failed: {baseline['failures']}")

    mutations = []
    mutations.append(("headline", lambda source, history: source["desks"][0].__setitem__("headline", "竄改主文")))

    def mutate_evidence(source: dict, history: dict) -> None:
        source["desks"][0]["evidence"][0]["value"] += 0.1
        rehash_brief(source["desks"][0])
        rehash_source(source)
        sync_latest_history(source, history, 0)

    mutations.append(("rehash-evidence", mutate_evidence))

    def mutate_lineage(source: dict, history: dict) -> None:
        source["lineage"]["market_context_hash"] = "0" * 64
        rehash_source(source)
        sync_latest_history(source, history)

    mutations.append(("lineage", mutate_lineage))

    def mutate_scope(source: dict, history: dict) -> None:
        source["desks"][1]["editorial_scope"] = "source_endorses_hypothesis"
        rehash_brief(source["desks"][1])
        rehash_source(source)
        sync_latest_history(source, history, 1)

    mutations.append(("scope", mutate_scope))

    mutations.append(("history-chain", lambda source, history: history["runs"][-1].__setitem__("previous_run_hash", "tampered")))

    def remove_desk(source: dict, history: dict) -> None:
        source["desks"].pop()
        source["editorial_digest"]["desk_count"] = 7
        rehash_source(source)
        sync_latest_history(source, history)

    mutations.append(("missing-desk", remove_desk))

    def mutate_link(source: dict, history: dict) -> None:
        source["desks"][2]["evidence"][0]["sources"][0]["url"] = "http://unsafe.example"
        rehash_brief(source["desks"][2])
        rehash_source(source)
        sync_latest_history(source, history, 2)

    mutations.append(("source-link", mutate_link))

    def mutate_as_of(source: dict, history: dict) -> None:
        source["desks"][0]["evidence"][0]["as_of"] = "2000-01-01"
        rehash_brief(source["desks"][0])
        rehash_source(source)
        sync_latest_history(source, history, 0)

    mutations.append(("stale-as-of", mutate_as_of))

    def mutate_source_count(source: dict, history: dict) -> None:
        source["desks"][0]["evidence"][0]["source_count"] += 1
        rehash_brief(source["desks"][0])
        rehash_source(source)
        sync_latest_history(source, history, 0)

    mutations.append(("fake-source-count", mutate_source_count))

    def mutate_direction(source: dict, history: dict) -> None:
        evidence = source["desks"][0]["evidence"][0]
        evidence["direction"] = "positive" if evidence["direction"] != "positive" else "negative"
        rehash_brief(source["desks"][0])
        rehash_source(source)
        sync_latest_history(source, history, 0)

    mutations.append(("reversed-direction", mutate_direction))

    for name, mutate in mutations:
        expect_rejected(name, mutate)

    degraded_source = copy.deepcopy(BASE["source"])
    degraded_history = copy.deepcopy(BASE["history"])
    degraded_market = copy.deepcopy(BASE["market"])
    degraded_market_verify = copy.deepcopy(BASE["market_verify"])
    degraded_market["quality"]["status"] = "degraded"
    degraded_market_verify["status"] = "degraded"
    degraded_source["quality"]["upstream_statuses"]["market_universe"] = "degraded"
    degraded_source["quality"]["upstream_statuses"]["market_universe_verifier"] = "degraded"
    degraded_source["quality"]["status"] = "pass"
    degraded_source["editorial_digest"]["status"] = "pass"
    degraded_source["lineage"]["market_universe_hash"] = verifier.canonical_hash(degraded_market)
    rehash_source(degraded_source)
    sync_latest_history(degraded_source, degraded_history)
    degraded_report = run(degraded_source, degraded_history, market=degraded_market, market_verify=degraded_market_verify)
    if degraded_report["status"] != "fail":
        raise AssertionError("upstream-degraded-fake-pass mutation unexpectedly passed")

    manifest = {"commit": "abc123", "editorial_hash": BASE["source"]["editorial_hash"]}
    verification = {
        "status": "pass",
        "source_hash": BASE["source"]["editorial_hash"],
        "source_generated_at": BASE["source"]["generated_at"],
    }
    validate_json_binding(manifest, BASE["source"], verification, "abc123", BASE["source"]["editorial_hash"])
    tampered_manifest = dict(manifest, editorial_hash="0" * 64)
    try:
        validate_json_binding(tampered_manifest, BASE["source"], verification, "abc123", BASE["source"]["editorial_hash"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("manifest/editorial hash mismatch unexpectedly passed")

    total = len(mutations) + 2
    print(f"market editorial mutation tests: PASS ({total}/{total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
