#!/usr/bin/env python3
"""Prove the AI verifier rejects editorial and history mutations."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_ai_intelligence as verifier

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_case(
    root: Path,
    source: dict[str, Any],
    history: dict[str, Any],
    mutate: Callable[[dict[str, Any], dict[str, Any]], None] | None,
) -> int:
    local_source = copy.deepcopy(source)
    local_history = copy.deepcopy(history)
    if mutate:
        mutate(local_source, local_history)
    source_path = root / "source.json"
    history_path = root / "history.json"
    report_path = root / "report.json"
    write(source_path, local_source)
    write(history_path, local_history)
    verifier.OUTPUT_PATH = source_path
    verifier.HISTORY_PATH = history_path
    verifier.REPORT_PATH = report_path
    return verifier.main()


def main() -> int:
    source = read(ROOT / "data" / "daily" / "ai_intelligence.json")
    history = read(ROOT / "data" / "daily" / "ai_intelligence_history.json")
    checks = 0
    with tempfile.TemporaryDirectory(prefix="ai-verifier-mutations-") as directory:
        root = Path(directory)
        assert run_case(root, source, history, None) == 0
        checks += 1

        def mutate_excerpt(data: dict[str, Any], _: dict[str, Any]) -> None:
            data["editorial_digest"]["briefs"][0]["evidence"][0]["source_excerpt"] = "竄改的來源摘錄"

        assert run_case(root, source, history, mutate_excerpt) == 1
        checks += 1

        def mutate_relationship(data: dict[str, Any], _: dict[str, Any]) -> None:
            data["editorial_digest"]["briefs"][0]["evidence"][0]["relationship"] = "source_endorses_hypothesis"

        assert run_case(root, source, history, mutate_relationship) == 1
        checks += 1

        def mutate_history_item(_: dict[str, Any], ledger: dict[str, Any]) -> None:
            ledger["items"][0]["title"] = "竄改的歷史標題"

        assert run_case(root, source, history, mutate_history_item) == 1
        checks += 1

        def mutate_history_run(_: dict[str, Any], ledger: dict[str, Any]) -> None:
            ledger["editorial_runs"][0]["lead_brief_id"] = "tampered"

        assert run_case(root, source, history, mutate_history_run) == 1
        checks += 1

        def inject_non_object(_: dict[str, Any], ledger: dict[str, Any]) -> None:
            ledger["editorial_runs"].append("tampered")

        assert run_case(root, source, history, inject_non_object) == 1
        checks += 1

    print(f"AI verifier mutation tests: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
