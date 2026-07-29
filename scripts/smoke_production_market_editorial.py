#!/usr/bin/env python3
"""Read back the deployed market editorial JSON and rendered desktop/mobile page."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
from urllib.error import HTTPError
import urllib.request
from pathlib import Path
from typing import Any

from build_deployment_manifest import TIMESCALE_ARTIFACTS

PAGES = {
    "market-intelligence.html": "八個研究桌",
    "market-monitor.html": "先看四個市場結論",
    "x-intelligence.html": "今天真正改變了什麼",
    "wiki.html": "投資 LLM Wiki",
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
            artifact_bytes = {path: fetch_bytes(f"{base_url}/{path}?v={time.time_ns()}") for path in TIMESCALE_ARTIFACTS}
            validate_json_binding(manifest, editorial, verification, expected_commit, expected_editorial_hash)
            validate_timescale_artifacts(manifest, artifact_bytes)
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
                    page.wait_for_function("() => ['pass','degraded','fail'].includes(document.body.dataset.renderStatus)", timeout=20_000)
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
                })""")
                markers = [marker for marker in CRASH_MARKERS if marker in body]
                if errors or expected_text not in body or markers:
                    raise RuntimeError(f"{name} {page_name} render failed errors={errors} markers={markers}")
                if layout["scroll"] - layout["client"] > 1 or not layout["activeNavVisible"]:
                    raise RuntimeError(f"{name} {page_name} layout/navigation failed")
                status = page.locator("body").get_attribute("data-render-status") if page_name in STATUS_PAGES else "pass"
                if page_name in STATUS_PAGES and status not in {"pass", "degraded"}:
                    raise RuntimeError(f"{name} {page_name} status={status}")
                if page_name == "market-intelligence.html":
                    desk_count = page.locator("body").get_attribute("data-desk-count")
                    lead_visible = page.locator("body").get_attribute("data-lead-visible")
                    timescale_status = page.locator("body").get_attribute("data-timescale-status")
                    if desk_count != "8" or lead_visible != "true" or timescale_status not in {"pass", "degraded"} or lead["headline"] not in body or lead["evidence"][0]["display"] not in body:
                        raise RuntimeError(f"{name} market editorial live values missing")
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
