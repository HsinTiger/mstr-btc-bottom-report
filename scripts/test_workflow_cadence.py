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
    required_hourly_order = [
        "collect_market_universe.py",
        "verify_market_universe.py",
        "generate_timescale_intelligence.py",
        "verify_timescale_intelligence.py",
        "generate_market_editorial.py",
        "verify_market_editorial.py",
    ]
    positions = [hourly.find(item) for item in required_hourly_order]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError("hourly market refresh must rebuild and verify every dependent artifact in order")
    hourly_staged = staged_paths(hourly)
    for artifact in (
        "timescale_intelligence.json",
        "timescale_intelligence_verification.json",
        "market_editorial.json",
        "market_editorial_verification.json",
    ):
        if artifact not in hourly_staged:
            raise AssertionError(f"hourly workflow must atomically publish rebuilt {artifact}")
    required_editorial_order = [
        "collect_market_universe.py",
        "verify_market_universe.py",
        "collect_market_context.py",
        "verify_market_context.py",
        "generate_timescale_intelligence.py",
        "verify_timescale_intelligence.py",
        "generate_market_editorial.py",
        "verify_market_editorial.py",
    ]
    positions = [editorial.find(item) for item in required_editorial_order]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError("editorial refresh must rebuild and verify timescale analysis before editorial output")
    editorial_staged = staged_paths(editorial)
    for artifact in (
        "timescale_intelligence.json",
        "timescale_intelligence_history.json",
        "timescale_intelligence_verification.json",
    ):
        if artifact not in editorial_staged:
            raise AssertionError(f"editorial workflow must publish rebuilt {artifact}")

    print("workflow cadence tests: PASS (17/17)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
