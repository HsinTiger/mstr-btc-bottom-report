#!/usr/bin/env python3
"""Read back the deployed market editorial JSON and rendered desktop/mobile page."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any

def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "mstr-btc-bottom-report/production-canary"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


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


def main() -> int:
    from playwright.sync_api import sync_playwright

    base_url = os.environ.get("BASE_URL", "https://hsintiger.github.io/mstr-btc-bottom-report").rstrip("/")
    expected_commit = os.environ.get("EXPECTED_COMMIT")
    expected_editorial_hash = os.environ.get("EXPECTED_EDITORIAL_HASH")
    manifest: dict[str, Any] = {}
    editorial: dict[str, Any] = {}
    verification: dict[str, Any] = {}
    last_error: Exception | None = None
    for _ in range(18):
        try:
            manifest = fetch_json(f"{base_url}/deployment-manifest.json?v={time.time_ns()}")
            editorial = fetch_json(f"{base_url}/data/daily/market_editorial.json?v={time.time_ns()}")
            verification = fetch_json(f"{base_url}/data/daily/market_editorial_verification.json?v={time.time_ns()}")
            validate_json_binding(manifest, editorial, verification, expected_commit, expected_editorial_hash)
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
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{base_url}/market-intelligence.html?v={time.time_ns()}", wait_until="networkidle", timeout=60_000)
            page.wait_for_function("() => ['pass','degraded','fail'].includes(document.body.dataset.renderStatus)", timeout=20_000)
            status = page.locator("body").get_attribute("data-render-status")
            desk_count = page.locator("body").get_attribute("data-desk-count")
            lead_visible = page.locator("body").get_attribute("data-lead-visible")
            body = page.locator("body").inner_text()
            layout = page.evaluate("""() => ({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})""")
            if errors or status not in {"pass", "degraded"} or desk_count != "8" or lead_visible != "true":
                raise RuntimeError(f"{name} production render failed status={status} desks={desk_count} errors={errors}")
            if layout["scroll"] - layout["client"] > 1:
                raise RuntimeError(f"{name} production horizontal overflow +{layout['scroll'] - layout['client']}px")
            if lead["headline"] not in body or lead["evidence"][0]["display"] not in body:
                raise RuntimeError(f"{name} production live values missing")
            results.append({"viewport": name, "status": status, "desk_count": desk_count, "overflow": 0, "page_errors": 0})
            context.close()
        browser.close()
    print(json.dumps({"base_url": base_url, "commit": manifest.get("commit"), "editorial_hash": editorial.get("editorial_hash"), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
