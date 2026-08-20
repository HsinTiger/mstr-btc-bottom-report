#!/usr/bin/env python3
"""Prove the author-thesis verifier rejects the mutations that would matter.

The failure mode this guards against is not a crash but a flattering artifact:
an article's number quietly edited to match today's data, a drifted claim
relabelled consistent, or an untracked signal presented as if it had been
evaluated. Each case below must fail closed.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "data" / "daily" / "author_thesis_tracker.json"
VERIFIER = ROOT / "scripts" / "verify_author_thesis_tracker.py"


def run_verifier() -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(VERIFIER)], capture_output=True, text=True, cwd=str(ROOT))
    line = (result.stdout or "").strip().splitlines()
    payload = json.loads(line[-1]) if line else {}
    return result.returncode, payload


def main() -> int:
    original = TRACKER.read_text(encoding="utf-8")
    baseline_code, baseline = run_verifier()
    if baseline_code != 0:
        raise AssertionError(f"baseline failed: {baseline.get('failures')}")

    source = json.loads(original)
    mutations = {
        "rewrite_article_number": lambda item: item["theses"][0]["claims"][0].update({"stated_value": 9.99}),
        "relabel_drift_as_consistent": lambda item: item["theses"][0]["claims"][0].update({"status": "consistent", "current_value": None}),
        "fake_untracked_verdict": lambda item: [
            falsifier.update({"status": "not_triggered"})
            for thesis in item["theses"]
            for falsifier in thesis["falsifiers"]
            if falsifier["status"] == "untracked"
        ],
        "forge_summary": lambda item: item["theses"][0]["summary"].update({"standing": "intact", "falsifiers_triggered": 99}),
        "grant_execution_gate": lambda item: item["quality"].update({"execution_gate_eligible": True}),
        "unbind_context": lambda item: item.update({"context_generated_at": "1999-01-01T00:00:00+00:00"}),
    }

    try:
        for name, mutate in mutations.items():
            mutated = copy.deepcopy(source)
            mutate(mutated)
            TRACKER.write_text(json.dumps(mutated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            code, report = run_verifier()
            if code == 0:
                raise AssertionError(f"mutation {name} was not rejected")
            print(json.dumps({"mutation": name, "rejected": True, "first_failure": report.get("failures", [])[:1]}, ensure_ascii=False))
    finally:
        # Every mutation run overwrites the verification artifact, so the last
        # rejection would otherwise be left on disk as if it were the real
        # verdict — and the page reads that file to decide whether to publish.
        # Restore the tracker and re-verify so the artifact matches reality.
        TRACKER.write_text(original, encoding="utf-8")
        restored_code, restored = run_verifier()

    if restored_code != 0:
        raise AssertionError(f"restored tracker no longer verifies: {restored.get('failures')}")

    print(json.dumps({"status": "pass", "mutations": len(mutations), "restored_verification": restored.get("status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
