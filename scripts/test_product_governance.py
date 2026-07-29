#!/usr/bin/env python3
"""Prove backend-only data products cannot disappear behind a green page audit."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import audit_product_surfaces as audit
from build_deployment_manifest import CRITICAL_ARTIFACTS, MARKET_EVIDENCE_ARTIFACTS, TIMESCALE_ARTIFACTS, build_manifest
from smoke_production_market_editorial import validate_market_evidence_artifacts, validate_timescale_artifacts


def write_json(root: Path, name: str, payload: dict[str, object]) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp = now.isoformat()
    product = {
        "id": "timescale-price-ledger",
        "title": "四週期價格與來源對帳",
        "criticality": "high",
        "dependencies": [
            {"path": "data/price.json", "timestamp_field": "generated_at", "max_age_hours": 30, "required": True},
            {"path": "data/verification.json", "timestamp_field": "verified_at", "max_age_hours": 30, "required": True, "quality_field": "status", "fail_values": ["fail"]},
        ],
        "bindings": [
            {"left_path": "data/price.json", "left_field": "generated_at", "right_path": "data/verification.json", "right_field": "history_generated_at"}
        ],
    }
    with tempfile.TemporaryDirectory(prefix="product-governance-") as temp:
        root = Path(temp)
        original_root = audit.ROOT
        audit.ROOT = root
        try:
            write_json(root, "data/price.json", {"generated_at": timestamp})
            write_json(root, "data/verification.json", {"verified_at": timestamp, "history_generated_at": timestamp, "status": "pass"})
            passing = audit.audit_data_product(product, now, {})
            if passing["status"] != "pass":
                raise AssertionError(f"valid backend product failed: {passing}")

            write_json(root, "data/verification.json", {"verified_at": timestamp, "history_generated_at": "2000-01-01T00:00:00+00:00", "status": "pass"})
            mismatched = audit.audit_data_product(product, now, {})
            if mismatched["status"] != "fail":
                raise AssertionError("artifact binding mismatch produced false PASS")

            (root / "data/price.json").unlink()
            missing = audit.audit_data_product(product, now, {})
            if missing["status"] != "fail":
                raise AssertionError("missing backend artifact produced false PASS")
        finally:
            audit.ROOT = original_root

    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="deployment-binding-") as temp:
        site = Path(temp)
        for name in list(CRITICAL_ARTIFACTS) + ["data/daily/market_editorial.json"]:
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / name, target)
        manifest = build_manifest(site, "test-commit")
        artifacts = {name: (site / name).read_bytes() for name in CRITICAL_ARTIFACTS}
        validate_timescale_artifacts(manifest, artifacts)
        validate_market_evidence_artifacts(manifest, artifacts)
        first_path = TIMESCALE_ARTIFACTS[0]
        tampered = {**artifacts, first_path: artifacts[first_path] + b"\n"}
        try:
            validate_timescale_artifacts(manifest, tampered)
        except RuntimeError:
            pass
        else:
            raise AssertionError("tampered production artifact produced false PASS")
        market_path = MARKET_EVIDENCE_ARTIFACTS[0]
        tampered_market = {**artifacts, market_path: artifacts[market_path] + b"\n"}
        try:
            validate_market_evidence_artifacts(manifest, tampered_market)
        except RuntimeError:
            pass
        else:
            raise AssertionError("tampered market evidence artifact produced false PASS")
    print("product governance tests: PASS (6/6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
