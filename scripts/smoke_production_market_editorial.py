#!/usr/bin/env python3
"""Read back the deployed market editorial JSON and rendered desktop/mobile page."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
import urllib.request
from pathlib import Path
from typing import Any

try:
    from build_deployment_manifest import (
        CRITICAL_ARTIFACTS,
        DAILY_EVIDENCE_ARTIFACTS,
        HOURLY_EVIDENCE_ARTIFACTS,
        MARKET_EVIDENCE_ARTIFACTS,
        TIMESCALE_ARTIFACTS,
    )
    from verify_market_universe import evidence_ledger_errors
except ModuleNotFoundError:
    from scripts.build_deployment_manifest import (
        CRITICAL_ARTIFACTS,
        DAILY_EVIDENCE_ARTIFACTS,
        HOURLY_EVIDENCE_ARTIFACTS,
        MARKET_EVIDENCE_ARTIFACTS,
        TIMESCALE_ARTIFACTS,
    )
    from scripts.verify_market_universe import evidence_ledger_errors

PAGES = {
    "market-intelligence.html": "八個研究桌",
    "market-monitor.html": "先看四個市場結論",
    "x-intelligence.html": "今天真正改變了什麼",
    "wiki.html": "幣圈投資 Wiki",
    "site-overview.html": "四週期價格與來源對帳",
}
STATUS_PAGES = {"market-intelligence.html", "market-monitor.html", "x-intelligence.html"}
CRASH_MARKERS = ("Cannot read properties", "治理資料失敗", "知識庫載入失敗", "ReferenceError", "SyntaxError")


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mstr-btc-bottom-report/production-canary"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def browser_path() -> str:
    candidates = [shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")]
    if os.name == "nt":
        candidates.extend(
            str(path)
            for path in (
                Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            )
            if path.is_file()
        )
    result = next((item for item in candidates if item and Path(item).is_file()), None)
    if not result:
        raise RuntimeError("Chrome/Chromium executable not found")
    return result


def validate_json_binding(
    manifest: dict[str, Any],
    editorial: dict[str, Any],
    verification: dict[str, Any],
    expected_commit: str | None = None,
    expected_editorial_hash: str | None = None,
) -> None:
    if expected_commit and manifest.get("commit") != expected_commit:
        raise RuntimeError(f"production commit {manifest.get('commit')} != {expected_commit}")
    if expected_editorial_hash and manifest.get("editorial_hash") != expected_editorial_hash:
        raise RuntimeError(f"production manifest editorial {manifest.get('editorial_hash')} != {expected_editorial_hash}")
    if manifest.get("editorial_hash") != editorial.get("editorial_hash"):
        raise RuntimeError("production manifest/editorial hash mismatch")
    if verification.get("source_hash") != editorial.get("editorial_hash") or verification.get("source_generated_at") != editorial.get("generated_at"):
        raise RuntimeError("production editorial/verifier hash binding mismatch")
    if verification.get("status") != "pass" or len(editorial.get("desks", [])) != 8:
        raise RuntimeError("production editorial verification or desk count failed")


def validate_timescale_artifacts(manifest: dict[str, Any], artifact_bytes: dict[str, bytes]) -> None:
    if manifest.get("schema") != 2:
        raise RuntimeError(f"production manifest schema {manifest.get('schema')} != 2")
    manifest_artifacts = manifest.get("artifacts", {})
    for path in TIMESCALE_ARTIFACTS:
        record = manifest_artifacts.get(path, {})
        payload = artifact_bytes.get(path, b"")
        if record.get("sha256") != hashlib.sha256(payload).hexdigest() or record.get("bytes") != len(payload):
            raise RuntimeError(f"production artifact hash mismatch: {path}")

    payloads = {path: json.loads(artifact_bytes[path].decode("utf-8")) for path in TIMESCALE_ARTIFACTS}
    price = payloads["data/daily/timescale_price_history.json"]
    data_verification = payloads["data/daily/timescale_data_verification.json"]
    analysis = payloads["data/daily/timescale_intelligence.json"]
    history = payloads["data/daily/timescale_intelligence_history.json"]
    analysis_verification = payloads["data/daily/timescale_intelligence_verification.json"]
    if any(payload.get("schema") != 1 for payload in payloads.values()):
        raise RuntimeError("production timescale artifact schema failed")
    if data_verification.get("status") != "pass" or analysis_verification.get("status") != "pass":
        raise RuntimeError("production timescale verifier failed")
    if price.get("generated_at") != data_verification.get("history_generated_at") or price.get("snapshot_generated_at") != data_verification.get("snapshot_generated_at"):
        raise RuntimeError("production timescale price/verifier binding mismatch")
    if analysis.get("generated_at") != analysis_verification.get("analysis_generated_at") or analysis.get("snapshot_generated_at") != analysis_verification.get("snapshot_generated_at"):
        raise RuntimeError("production timescale analysis/verifier binding mismatch")
    items = history.get("items", [])
    if history.get("updated_at") != analysis.get("generated_at") or not items or items[-1].get("generated_at") != analysis.get("generated_at"):
        raise RuntimeError("production timescale history binding mismatch")


def validate_market_evidence_artifacts(
    manifest: dict[str, Any],
    artifact_bytes: dict[str, bytes],
    *,
    now: datetime | None = None,
) -> None:
    reference_now = now or datetime.now(timezone.utc)
    manifest_artifacts = manifest.get("artifacts", {})
    for path in MARKET_EVIDENCE_ARTIFACTS:
        record = manifest_artifacts.get(path, {})
        payload = artifact_bytes.get(path, b"")
        if record.get("sha256") != hashlib.sha256(payload).hexdigest() or record.get("bytes") != len(payload):
            raise RuntimeError(f"production market evidence artifact hash mismatch: {path}")
    market = json.loads(artifact_bytes["data/daily/market_universe.json"].decode("utf-8"))
    snapshot = json.loads(artifact_bytes["data/daily/latest_snapshot.json"].decode("utf-8"))
    daily_verification = json.loads(artifact_bytes["data/daily/agent_verification_report.json"].decode("utf-8"))
    verification = json.loads(artifact_bytes["data/daily/market_universe_verification.json"].decode("utf-8"))
    try:
        daily_verified_at = datetime.fromisoformat(str(daily_verification.get("verified_at")).replace("Z", "+00:00"))
        snapshot_generated_at = datetime.fromisoformat(str(snapshot.get("generated_at")).replace("Z", "+00:00"))
        market_generated_at = datetime.fromisoformat(str(market.get("generated_at")).replace("Z", "+00:00"))
        market_verified_at = datetime.fromisoformat(str(verification.get("verified_at")).replace("Z", "+00:00"))
        if daily_verified_at.tzinfo is None:
            daily_verified_at = daily_verified_at.replace(tzinfo=timezone.utc)
        if snapshot_generated_at.tzinfo is None:
            snapshot_generated_at = snapshot_generated_at.replace(tzinfo=timezone.utc)
        if market_generated_at.tzinfo is None:
            market_generated_at = market_generated_at.replace(tzinfo=timezone.utc)
        if market_verified_at.tzinfo is None:
            market_verified_at = market_verified_at.replace(tzinfo=timezone.utc)
        daily_verifier_lag_hours = (daily_verified_at - snapshot_generated_at).total_seconds() / 3600
        daily_verifier_age_hours = (reference_now - daily_verified_at).total_seconds() / 3600
        market_verifier_lag_hours = (market_verified_at - market_generated_at).total_seconds() / 3600
        market_age_hours = (reference_now - market_generated_at).total_seconds() / 3600
    except (TypeError, ValueError):
        daily_verifier_lag_hours = None
        daily_verifier_age_hours = None
        market_verifier_lag_hours = None
        market_age_hours = None
    if verification.get("status") not in {"pass", "degraded"} or verification.get("failures"):
        raise RuntimeError("production market evidence verifier failed")
    if verification.get("market_generated_at") != market.get("generated_at") or verification.get("market_date") != market.get("date"):
        raise RuntimeError("production market evidence/verifier batch mismatch")
    if (
        daily_verification.get("schema") != 2
        or daily_verification.get("status") not in {"pass", "degraded"}
        or daily_verification.get("failures")
        or daily_verification.get("status_scope") != "verified_market_inputs_only"
        or daily_verification.get("date") != snapshot.get("date")
        or daily_verification.get("snapshot_generated_at") != snapshot.get("generated_at")
        or daily_verification.get("batch_id") != snapshot.get("batch_id")
        or daily_verifier_lag_hours is None
        or daily_verifier_age_hours is None
        or daily_verifier_lag_hours < 0
        or daily_verifier_lag_hours > 1
        or daily_verifier_age_hours < -0.25
        or daily_verifier_age_hours > 30
    ):
        raise RuntimeError("production daily independent verifier binding failed")
    if (
        market_verifier_lag_hours is None
        or market_age_hours is None
        or market_verifier_lag_hours < 0
        or market_verifier_lag_hours > 1
        or market_age_hours < -0.25
        or market_age_hours > 3
    ):
        raise RuntimeError("production hourly market verifier freshness failed")
    errors = evidence_ledger_errors(market)
    if errors:
        raise RuntimeError(f"production evidence ledger failed: {errors[0]}")


def validate_retired_pages(base_url: str) -> None:
    for page_name in ("analytics.html", "dashboard.html", "daily-extensions.html"):
        try:
            fetch_bytes(f"{base_url}/{page_name}?v={time.time_ns()}")
        except HTTPError as error:
            if error.code == 404:
                continue
            raise RuntimeError(f"production retired page {page_name} HTTP {error.code}") from error
        raise RuntimeError(f"production retired page remains published: {page_name}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    base_url = os.environ.get("BASE_URL", "https://hsintiger.github.io/mstr-btc-bottom-report").rstrip("/")
    expected_commit = os.environ.get("EXPECTED_COMMIT")
    expected_editorial_hash = os.environ.get("EXPECTED_EDITORIAL_HASH")
    manifest: dict[str, Any] = {}
    editorial: dict[str, Any] = {}
    verification: dict[str, Any] = {}
    artifact_bytes: dict[str, bytes] = {}
    last_error: Exception | None = None
    for _ in range(18):
        try:
            manifest = fetch_json(f"{base_url}/deployment-manifest.json?v={time.time_ns()}")
            editorial = fetch_json(f"{base_url}/data/daily/market_editorial.json?v={time.time_ns()}")
            verification = fetch_json(f"{base_url}/data/daily/market_editorial_verification.json?v={time.time_ns()}")
            artifact_bytes = {path: fetch_bytes(f"{base_url}/{path}?v={time.time_ns()}") for path in CRITICAL_ARTIFACTS}
            validate_json_binding(manifest, editorial, verification, expected_commit, expected_editorial_hash)
            validate_timescale_artifacts(manifest, artifact_bytes)
            validate_market_evidence_artifacts(manifest, artifact_bytes)
            validate_retired_pages(base_url)
            break
        except Exception as error:
            last_error = error
            time.sleep(10)
    else:
        raise RuntimeError(f"production JSON readback failed: {last_error}")

    lead = next(item for item in editorial["desks"] if item["id"] == editorial["editorial_digest"]["lead_desk_id"])
    executable = browser_path()
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        for name, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
            for page_name, expected_text in PAGES.items():
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.goto(f"{base_url}/{page_name}?v={time.time_ns()}", wait_until="networkidle", timeout=60_000)
                if not response or not response.ok:
                    raise RuntimeError(f"{name} {page_name} HTTP render failed")
                if page_name in STATUS_PAGES:
                    page.wait_for_function(
                        "() => ['pass','degraded','fail'].includes(document.body.dataset.renderStatus || document.documentElement.dataset.renderStatus)",
                        timeout=20_000,
                    )
                else:
                    page.wait_for_timeout(500)
                body = page.locator("body").inner_text()
                layout = page.evaluate("""() => ({
                    client: document.documentElement.clientWidth,
                    scroll: document.documentElement.scrollWidth,
                    activeNavVisible: [...document.querySelectorAll('nav a[aria-current="page"]')].some(active => {
                        const nav = active.closest('nav');
                        if (!nav || active.offsetParent === null || nav.offsetParent === null) return false;
                        const activeRect = active.getBoundingClientRect();
                        const navRect = nav.getBoundingClientRect();
                        return activeRect.left >= navRect.left - 1 && activeRect.right <= navRect.right + 1;
                    }),
                    evidenceTextComplete: [...document.querySelectorAll('details[data-evidence-metric]')].every(item => {
                        const text = item.textContent || '';
                        return ['資料截至','更新節奏','怎麼驗','新鮮度','驗證報告','限制'].every(label => text.includes(label));
                    }),
                    sourceTimingComplete: [...document.querySelectorAll('.evidence-source small')].every(item => {
                        const text = item.textContent || '';
                        return text.includes('觀測 ') && text.includes('抓取 ');
                    }),
                    minEvidenceLinkHeight: Math.min(...[...document.querySelectorAll('.evidence-source a[href]')].map(item => item.getBoundingClientRect().height)),
                    minEvidenceLinkFontPx: Math.min(...[...document.querySelectorAll('.evidence-source a[href]')].map(item => parseFloat(getComputedStyle(item).fontSize))),
                })""")
                markers = [marker for marker in CRASH_MARKERS if marker in body]
                if errors or expected_text not in body or markers:
                    raise RuntimeError(f"{name} {page_name} render failed errors={errors} markers={markers}")
                if layout["scroll"] - layout["client"] > 1 or not layout["activeNavVisible"]:
                    raise RuntimeError(f"{name} {page_name} layout/navigation failed")
                status = page.evaluate("() => document.body.dataset.renderStatus || document.documentElement.dataset.renderStatus") if page_name in STATUS_PAGES else "pass"
                if page_name in STATUS_PAGES and status not in {"pass", "degraded"}:
                    raise RuntimeError(f"{name} {page_name} status={status}")
                if page_name == "market-intelligence.html":
                    desk_count = page.locator("body").get_attribute("data-desk-count")
                    lead_visible = page.locator("body").get_attribute("data-lead-visible")
                    timescale_status = page.locator("body").get_attribute("data-timescale-status")
                    if desk_count != "8" or lead_visible != "true" or timescale_status not in {"pass", "degraded"} or lead["headline"] not in body or lead["evidence"][0]["display"] not in body:
                        raise RuntimeError(f"{name} market editorial live values missing")
                if page_name == "market-monitor.html":
                    evidence_complete = page.locator("body").get_attribute("data-evidence-complete")
                    evidence_cards = page.locator("body").get_attribute("data-evidence-cards")
                    evidence_links = page.locator('.evidence-source a[href]').count()
                    if (
                        evidence_complete != "true"
                        or evidence_cards != "30"
                        or evidence_links < 30
                        or not layout["evidenceTextComplete"]
                        or not layout["sourceTimingComplete"]
                        or layout["minEvidenceLinkHeight"] < 44
                        or layout["minEvidenceLinkFontPx"] < 12
                        or "ETF 不是盤中即時資料" not in body
                    ):
                        raise RuntimeError(f"{name} market evidence surface incomplete")
                results.append({"viewport": name, "page": page_name, "status": status, "overflow": 0, "page_errors": 0})
                context.close()

            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.goto(f"{base_url}/?v={time.time_ns()}", wait_until="networkidle", timeout=60_000)
            if not page.url.split("?", 1)[0].endswith("/market-intelligence.html"):
                raise RuntimeError(f"{name} production root did not redirect to market editorial: {page.url}")
            results.append({"viewport": name, "page": "root→market-intelligence.html", "status": "pass", "overflow": 0, "page_errors": 0})
            context.close()
        browser.close()
    print(json.dumps({"base_url": base_url, "commit": manifest.get("commit"), "editorial_hash": editorial.get("editorial_hash"), "artifacts": len(artifact_bytes), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
