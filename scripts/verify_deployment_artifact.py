#!/usr/bin/env python3
"""Verify the assembled Pages artifact before it can be uploaded."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_deployment_manifest import CRITICAL_ARTIFACTS
from smoke_production_market_editorial import (
    validate_json_binding,
    validate_market_evidence_artifacts,
    validate_timescale_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path("_site"))
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    root = args.site_root
    manifest = json.loads((root / "deployment-manifest.json").read_text(encoding="utf-8-sig"))
    editorial = json.loads((root / "data/daily/market_editorial.json").read_text(encoding="utf-8-sig"))
    verification = json.loads((root / "data/daily/market_editorial_verification.json").read_text(encoding="utf-8-sig"))
    artifacts = {path: (root / path).read_bytes() for path in CRITICAL_ARTIFACTS}
    validate_json_binding(manifest, editorial, verification, args.commit, editorial.get("editorial_hash"))
    validate_timescale_artifacts(manifest, artifacts)
    validate_market_evidence_artifacts(manifest, artifacts)
    print(json.dumps({"status": "pass", "commit": args.commit, "artifacts": len(artifacts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
