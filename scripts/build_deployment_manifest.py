#!/usr/bin/env python3
"""Bind the Pages artifact to its commit and critical published data files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIMESCALE_ARTIFACTS = (
    "data/daily/timescale_price_history.json",
    "data/daily/timescale_data_verification.json",
    "data/daily/timescale_intelligence.json",
    "data/daily/timescale_intelligence_history.json",
    "data/daily/timescale_intelligence_verification.json",
)

DAILY_EVIDENCE_ARTIFACTS = (
    "data/daily/raw_observations.json",
    "data/daily/latest_snapshot.json",
    "data/daily/agent_verification_report.json",
)

HOURLY_EVIDENCE_ARTIFACTS = (
    "data/daily/market_universe.json",
    "data/daily/market_universe_verification.json",
)

MARKET_EVIDENCE_ARTIFACTS = DAILY_EVIDENCE_ARTIFACTS + HOURLY_EVIDENCE_ARTIFACTS
CRITICAL_ARTIFACTS = TIMESCALE_ARTIFACTS + MARKET_EVIDENCE_ARTIFACTS


def artifact_record(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def build_manifest(site_root: Path, commit: str) -> dict[str, Any]:
    editorial = json.loads((site_root / "data/daily/market_editorial.json").read_text(encoding="utf-8-sig"))
    return {
        "schema": 2,
        "commit": commit,
        "editorial_hash": editorial.get("editorial_hash"),
        "artifacts": {path: artifact_record(site_root / path) for path in CRITICAL_ARTIFACTS},
        "retired_pages": ["analytics.html", "dashboard.html", "daily-extensions.html"],
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path("_site"))
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.site_root, args.commit)
    output_path = args.site_root / "deployment-manifest.json"
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as output:
            output.write(f"commit={manifest['commit']}\n")
            output.write(f"editorial_hash={manifest['editorial_hash']}\n")
    print(json.dumps({"path": str(output_path), "commit": manifest["commit"], "artifacts": len(manifest["artifacts"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
