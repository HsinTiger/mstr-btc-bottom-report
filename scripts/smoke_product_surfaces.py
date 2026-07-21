#!/usr/bin/env python3
"""Render active product pages in Chrome before deployment and reject broken surfaces."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "index.html": "今天只看四件事",
    "market-monitor.html": "先看四個市場結論",
    "x-intelligence.html": "三類情報，一頁看完",
    "analytics.html": "多視角證據矩陣",
    "dashboard.html": "邏輯規格",
    "daily-extensions.html": "今天的三個延伸觀點",
    "wiki.html": "最後驗證",
    "site-overview.html": "頁面程式",
}
VIEWPORTS = {"desktop": (1440, 1000), "mobile": (390, 844)}
CRASH_MARKERS = (
    "Cannot read properties",
    "治理資料失敗",
    "知識庫載入失敗",
    "ReferenceError",
    "SyntaxError",
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class BodyText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def browser_path() -> str:
    candidates = [
        shutil.which(name)
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
    ]
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
    browser = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if not browser:
        raise SystemExit("Chrome/Chromium executable not found")
    return browser


@contextmanager
def server() -> Iterator[str]:
    handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(ROOT), **kwargs)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def rendered_body(browser: str, profile: str, url: str, width: int, height: int) -> tuple[str, str]:
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--no-first-run",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--virtual-time-budget=5000",
        "--dump-dom",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    if result.returncode:
        raise RuntimeError(f"Chrome exit {result.returncode}: {result.stderr[-500:]}")
    parser = BodyText()
    parser.feed(result.stdout)
    return parser.text(), result.stdout


def main() -> int:
    browser = browser_path()
    failures: list[dict[str, str]] = []
    results: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="product-smoke-") as profile, server() as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            for page, expected in PAGES.items():
                try:
                    body = ""
                    for attempt in range(2):
                        page_profile = Path(profile) / f"{viewport}-{page}-{attempt}"
                        page_profile.mkdir(parents=True, exist_ok=True)
                        try:
                            body, dom = rendered_body(browser, str(page_profile), f"{base_url}/{page}", width, height)
                            break
                        except subprocess.TimeoutExpired:
                            if attempt:
                                raise
                    markers = [marker for marker in CRASH_MARKERS if marker in body]
                    if expected not in body:
                        raise RuntimeError(f"必要畫面文字缺漏：{expected}")
                    if markers:
                        raise RuntimeError(f"發現崩潰文字：{', '.join(markers)}")
                    if page == "x-intelligence.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|unconfigured|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("X 情報缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        if 'data-page-overflow="false"' not in dom:
                            raise RuntimeError("X 情報發生頁面水平溢位")
                        if status != "fail" and 'data-category-count="3"' not in dom:
                            raise RuntimeError("X 情報三分類未完整載入")
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-feed-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("X 情報品質狀態與消息可見性不一致")
                    results.append({"viewport": viewport, "page": page, "status": "pass"})
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    failures.append({"viewport": viewport, "page": page, "error": str(error)})
    print(json.dumps({"browser": browser, "checks": len(results), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
