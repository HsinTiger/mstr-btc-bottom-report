#!/usr/bin/env python3
"""Prevent hourly and editorial writers from taking ownership of daily artifacts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8-sig")


def staged_paths(content: str) -> str:
    captured: list[str] = []
    continuing = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("git add "):
            continuing = True
        if continuing:
            captured.append(stripped)
            continuing = stripped.endswith("\\")
    return " ".join(captured)


def main() -> int:
    hourly = workflow("market-universe.yml")
    daily = workflow("daily-data.yml")
    editorial = workflow("market-editorial.yml")

    if 'cron: "17,47 * * * *"' not in hourly:
        raise AssertionError("hourly market workflow must run twice per hour")
    for name, content in (("hourly", hourly), ("editorial", editorial)):
        if "verify_daily_data.py" in content:
            raise AssertionError(f"{name} workflow must not run the daily verifier")
        if "agent_verification_report.json" in staged_paths(content):
            raise AssertionError(f"{name} workflow must not write the daily verifier artifact")
    if "verify_daily_data.py" not in daily or "agent_verification_report.json" not in daily:
        raise AssertionError("daily workflow must remain the sole daily verifier writer")
    if "verify_market_universe.py" not in hourly or "market_universe_verification.json" not in hourly:
        raise AssertionError("hourly workflow must verify and publish its own market artifact")

    print("workflow cadence tests: PASS (7/7)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
