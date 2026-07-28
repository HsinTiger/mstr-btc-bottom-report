#!/usr/bin/env python3
"""Deterministic tests for topic-context AI editorial hypotheses."""

from __future__ import annotations

from typing import Any

from collect_ai_intelligence import (
    canonical_hash,
    editorial_brief,
    editorial_digest,
    editorial_evidence,
    editorial_run_payload,
    editorial_theme,
    update_history,
    with_item_integrity,
)


def item(category_id: str, source_key: str, title: str, text: str, score: float) -> dict[str, Any]:
    return with_item_integrity({
        "id": f"{source_key}:{title}",
        "url": f"https://example.com/{source_key}/{title}",
        "created_at": "2026-07-28T00:00:00+00:00",
        "title": title,
        "text": text,
        "source_key": source_key,
        "category_id": category_id,
        "source_label": source_key.upper(),
        "source_type": "official_feed",
        "ranking_score_0_100": score,
    })


def category(category_id: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": category_id, "title": title, "items": items}


def main() -> int:
    checks = 0
    app_items = [
        item("ai-application-monetization", "alpha", "Enterprise deployment", "enterprise customer pricing deployment adoption", 91.0),
        item("ai-application-monetization", "beta", "Paid rollout", "enterprise customer revenue business adoption", 86.0),
        item("ai-application-monetization", "alpha", "Workflow update", "workflow integration tool", 82.0),
    ]
    theme = editorial_theme("ai-application-monetization", app_items)
    evidence = editorial_evidence(app_items, theme)
    assert [value["source_key"] for value in evidence] == ["beta", "alpha"]
    assert all(value["relationship"] == "topic_context_not_endorsement" for value in evidence)
    assert all(value["source_excerpt"] and value["matched_terms"] for value in evidence)
    checks += 3

    app_brief = editorial_brief(category("ai-application-monetization", "AI 應用與變現", app_items))
    assert app_brief["theme_id"] == "enterprise-proof"
    assert app_brief["status"] == "pass" and app_brief["evidence_source_count"] == 2
    assert "Alpha" not in app_brief["lede"] and "ALPHA" in app_brief["lede"]
    assert app_brief["falsifier"] and app_brief["variant_view"] and app_brief["second_order_effect"]
    checks += 4

    one_source = editorial_brief(category("ai-application-monetization", "AI 應用與變現", app_items[:1]))
    assert one_source["status"] == "degraded"
    assert "不宣稱形成跨來源訊號" in one_source["lede"]
    checks += 2

    engineering = category(
        "engineering-methods",
        "工程方法",
        [
            item("engineering-methods", "gamma", "Faster inference", "inference throughput latency gpu performance", 94.0),
            item("engineering-methods", "delta", "Serving engine", "inference performance memory latency", 89.0),
        ],
    )
    model = category(
        "model-progress",
        "模型進展",
        [
            item("model-progress", "epsilon", "Reasoning benchmark", "benchmark evaluation reasoning score accuracy", 90.0),
            item("model-progress", "zeta", "Evaluation study", "eval benchmark accuracy", 84.0),
        ],
    )
    digest = editorial_digest([category("ai-application-monetization", "AI 應用與變現", app_items), engineering, model])
    assert digest["status"] == "pass"
    assert len(digest["briefs"]) == 3
    assert digest["lead_brief_id"] in {brief["id"] for brief in digest["briefs"]}
    assert all(brief["editorial_scope"] == "editorial_hypothesis_not_source_claim" for brief in digest["briefs"])
    assert all(brief["novelty_status"] == "baseline" for brief in digest["briefs"])
    checks += 5

    previous = {
        "briefs": [
            {
                "category_id": brief["category_id"],
                "theme_id": brief["theme_id"],
                "headline": brief["headline"],
                "evidence_refs": [
                    {"item_id": value["item_id"], "item_integrity_hash": value["item_integrity_hash"]}
                    for value in brief["evidence"]
                ],
            }
            for brief in digest["briefs"]
        ]
    }
    unchanged = editorial_digest([category("ai-application-monetization", "AI 應用與變現", app_items), engineering, model], previous)
    assert all(brief["novelty_status"] == "unchanged" for brief in unchanged["briefs"])
    assert all("不製造新故事" in brief["what_changed"] for brief in unchanged["briefs"])
    checks += 2

    revised_previous = {"briefs": [dict(value) for value in previous["briefs"]]}
    revised_previous["briefs"][0] = {**revised_previous["briefs"][0], "evidence_refs": [dict(value) for value in revised_previous["briefs"][0]["evidence_refs"]]}
    revised_previous["briefs"][0]["evidence_refs"][0]["item_integrity_hash"] = "0" * 64
    revised = editorial_digest([category("ai-application-monetization", "AI 應用與變現", app_items), engineering, model], revised_previous)
    assert revised["briefs"][0]["novelty_status"] == "revised"
    checks += 1

    refreshed_items = app_items[1:] + [item("ai-application-monetization", "theta", "New enterprise proof", "enterprise customer deployment revenue", 88.0)]
    refreshed = editorial_digest([category("ai-application-monetization", "AI 應用與變現", refreshed_items), engineering, model], previous)
    assert refreshed["briefs"][0]["novelty_status"] == "refreshed"
    checks += 1

    output = {
        "generated_at": "2026-07-28T08:00:00+00:00",
        "quality": {"status": "pass"},
        "categories": [category("ai-application-monetization", "AI 應用與變現", app_items), engineering, model],
        "editorial_digest": digest,
    }
    first_history = update_history(output, {"schema": 2, "items": [], "editorial_runs": []})
    output["generated_at"] = "2026-07-28T08:05:00+00:00"
    second_history = update_history(output, first_history)
    assert [run["revision"] for run in second_history["editorial_runs"]] == [1, 2]
    assert second_history["editorial_runs"][1]["supersedes_generated_at"] == "2026-07-28T08:00:00+00:00"
    assert second_history["editorial_runs"][1]["previous_run_hash"] == second_history["editorial_runs"][0]["run_hash"]
    assert all(run["run_hash"] == canonical_hash(editorial_run_payload(run)) for run in second_history["editorial_runs"])
    checks += 4

    tampered = {**second_history, "editorial_runs": [dict(run) for run in second_history["editorial_runs"]]}
    tampered["editorial_runs"][0]["lead_brief_id"] = "tampered"
    try:
        update_history({**output, "generated_at": "2026-07-28T08:10:00+00:00"}, tampered)
    except ValueError:
        checks += 1
    else:
        raise AssertionError("tampered editorial history must fail closed")

    print(f"AI editorial tests: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
