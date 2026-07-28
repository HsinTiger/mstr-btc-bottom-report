#!/usr/bin/env python3
"""Render every active page and verify live, stale, and failed-analysis states."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError as error:
    raise SystemExit("缺少 browser smoke 依賴；請先執行 pip install -r requirements-smoke.txt") from error

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "index.html": "日／週／月／季一眼看懂",
    "market-intelligence.html": "八個研究桌",
    "market-monitor.html": "先看四個市場結論",
    "analytics.html": "週期證據矩陣",
    "dashboard.html": "近一年標準化價格",
    "daily-extensions.html": "今天最值得追蹤的觀點",
    "x-intelligence.html": "今天真正改變了什麼",
    "wiki.html": "最後驗證",
    "site-overview.html": "頁面程式",
}
ANALYSIS_PAGES = {"index.html", "analytics.html", "dashboard.html", "daily-extensions.html"}
STATUS_PAGES = ANALYSIS_PAGES | {"market-intelligence.html", "market-monitor.html", "x-intelligence.html"}
VIEWPORTS = {"desktop": (1440, 1000), "mobile": (390, 844)}
CRASH_MARKERS = ("Cannot read properties", "治理資料失敗", "知識庫載入失敗", "ReferenceError", "SyntaxError")


def browser_money(value: float, digits: int = 2) -> str:
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"${rounded:,.{digits}f}"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def server(overrides: dict[str, object] | None = None) -> Iterator[str]:
    fixture_overrides = overrides or {}

    class FixtureHandler(QuietHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in fixture_overrides:
                payload = json.dumps(fixture_overrides[path], ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            super().do_GET()

    handler = lambda *args, **kwargs: FixtureHandler(*args, directory=str(ROOT), **kwargs)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


class BrowserRenderer:
    def __init__(self, executable_path: str) -> None:
        self.executable_path = executable_path
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            executable_path=executable_path,
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-background-networking", "--disable-extensions"],
        )

    def close(self) -> None:
        self.browser.close()
        self.playwright.stop()

    def render(self, url: str, width: int, height: int) -> tuple[str, str, dict[str, Any]]:
        context = self.browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
            page_name = Path(urlsplit(url).path).name
            if page_name in STATUS_PAGES:
                page.wait_for_function(
                    """() => ['pass','degraded','fail'].includes(
                        document.body.dataset.renderStatus || document.documentElement.dataset.renderStatus
                    )""",
                    timeout=12_000,
                )
            else:
                page.wait_for_timeout(300)
            if page_errors:
                raise RuntimeError(f"瀏覽器 JavaScript 錯誤：{page_errors[-1]}")
            layout = page.evaluate("""() => ({
                documentClientWidth: document.documentElement.clientWidth,
                documentScrollWidth: document.documentElement.scrollWidth,
                bodyClientWidth: document.body.clientWidth,
                bodyScrollWidth: document.body.scrollWidth,
                activeNavVisible: (() => {
                    const activeLinks = [...document.querySelectorAll('nav a[aria-current="page"]')];
                    return activeLinks.some(active => {
                        const nav = active.closest('nav');
                        if (!nav || active.offsetParent === null || nav.offsetParent === null) return false;
                        const activeRect = active.getBoundingClientRect();
                        const navRect = nav.getBoundingClientRect();
                        return activeRect.left >= navRect.left - 1 && activeRect.right <= navRect.right + 1;
                    });
                })(),
            })""")
            return page.locator("body").inner_text(), page.content(), layout
        finally:
            context.close()


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
    executable = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if not executable:
        raise SystemExit("Chrome/Chromium executable not found")
    return executable


def assert_no_horizontal_overflow(layout: dict[str, int], label: str) -> None:
    document_overflow = layout["documentScrollWidth"] - layout["documentClientWidth"]
    body_overflow = layout["bodyScrollWidth"] - layout["bodyClientWidth"]
    if document_overflow > 1 or body_overflow > 1:
        raise RuntimeError(f"{label} 水平溢位：document +{document_overflow}px、body +{body_overflow}px")


def render_status(dom: str) -> str | None:
    match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
    return match.group(1) if match else None


def shift_time(value: str, hours: float) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(hours=hours)).isoformat()


def validate_base_page(
    page_name: str,
    expected_text: str,
    body: str,
    dom: str,
    layout: dict[str, Any],
    live_values: list[str],
    viewport: str,
) -> None:
    if expected_text not in body:
        raise RuntimeError(f"必要畫面文字缺漏：{expected_text}")
    markers = [marker for marker in CRASH_MARKERS if marker in body]
    if markers:
        raise RuntimeError(f"發現崩潰文字：{', '.join(markers)}")
    missing_values = [value for value in live_values if value not in body]
    if missing_values:
        raise RuntimeError(f"資料未渲染：{', '.join(missing_values)}")
    assert_no_horizontal_overflow(layout, f"{viewport} {page_name}")
    if viewport == "mobile" and not layout.get("activeNavVisible"):
        raise RuntimeError(f"{page_name} 手機目前頁導覽不在初始可視區")
    links = set(re.findall(r'href="([^"#?]+)', dom, flags=re.IGNORECASE))
    missing_navigation = [target for target in PAGES if target not in links]
    if missing_navigation:
        raise RuntimeError(f"主要導航缺漏：{', '.join(missing_navigation)}")
    if page_name in ANALYSIS_PAGES:
        status = render_status(dom)
        if status not in {"pass", "degraded"}:
            raise RuntimeError(f"分析頁狀態不可讀：{status}")
        if 'data-conclusions-visible="true"' not in dom or 'data-execution-grade="false"' not in dom:
            raise RuntimeError("分析頁未維持 analysis-only 可讀契約")
    if page_name == "market-monitor.html":
        status = render_status(dom)
        if status not in {"pass", "degraded"} or 'data-conclusions-visible="true"' not in dom:
            raise RuntimeError("即時市場品質契約未通過")
        if 'data-core-checks="14/14"' not in dom or 'data-page-overflow="false"' not in dom:
            raise RuntimeError("即時市場核心欄位或版面契約未通過")
    if page_name == "market-intelligence.html":
        status = render_status(dom)
        if status not in {"pass", "degraded"} or 'data-conclusions-visible="true"' not in dom:
            raise RuntimeError("市場總編 analysis-only 品質契約未通過")
        if 'data-desk-count="8"' not in dom or 'data-lead-visible="true"' not in dom:
            raise RuntimeError("市場總編主文或八個研究桌未完整載入")
        if 'data-page-overflow="false"' not in dom:
            raise RuntimeError("市場總編版面發生水平溢位")
    if page_name == "x-intelligence.html":
        status = render_status(dom)
        if status not in {"pass", "degraded"}:
            raise RuntimeError("AI 情報狀態不可讀")
        if 'data-category-count="3"' not in dom or 'data-action-count="3"' not in dom:
            raise RuntimeError("AI 情報三分類或三個行動未完整載入")
        if 'data-editorial-count="3"' not in dom or 'data-lead-visible="true"' not in dom:
            raise RuntimeError("AI 情報主文或三篇機構觀點未完整載入")
        if 'data-page-overflow="false"' not in dom:
            raise RuntimeError("AI 情報版面發生水平溢位")


def main() -> int:
    renderer = BrowserRenderer(browser_path())
    failures: list[dict[str, str]] = []
    results: list[dict[str, str]] = []
    analysis = json.loads((ROOT / "data/daily/timescale_intelligence.json").read_text(encoding="utf-8-sig"))
    analysis_verification = json.loads((ROOT / "data/daily/timescale_intelligence_verification.json").read_text(encoding="utf-8-sig"))
    market = json.loads((ROOT / "data/daily/market_universe.json").read_text(encoding="utf-8-sig"))
    ai = json.loads((ROOT / "data/daily/ai_intelligence.json").read_text(encoding="utf-8-sig"))
    ai_verification = json.loads((ROOT / "data/daily/ai_intelligence_verification.json").read_text(encoding="utf-8-sig"))
    market_editorial = json.loads((ROOT / "data/daily/market_editorial.json").read_text(encoding="utf-8-sig"))
    market_editorial_verification = json.loads((ROOT / "data/daily/market_editorial_verification.json").read_text(encoding="utf-8-sig"))
    daily_key = analysis["horizons"]["daily"]["key_number"]
    first_insight = analysis["exclusive_insights"][0]["title"]
    lead_brief = next(item for item in ai["editorial_digest"]["briefs"] if item["id"] == ai["editorial_digest"]["lead_brief_id"])
    market_lead = next(item for item in market_editorial["desks"] if item["id"] == market_editorial["editorial_digest"]["lead_desk_id"])
    live_values = {
        "index.html": [analysis["date"], daily_key, first_insight],
        "market-intelligence.html": [market_lead["headline"], market_lead["conclusion"], market_lead["evidence"][0]["display"]],
        "market-monitor.html": [browser_money(market["assets"]["BTC"]["price_usd"]), browser_money(market["assets"]["ETH"]["price_usd"])],
        "analytics.html": [analysis["date"], daily_key, first_insight],
        "dashboard.html": [analysis["date"], str(analysis["record_advantage"]["observations"])],
        "daily-extensions.html": [analysis["date"], first_insight],
        "x-intelligence.html": [lead_brief["headline"], lead_brief["variant_view"], lead_brief["what_changed"], lead_brief["evidence"][0]["source_label"]],
    }
    with tempfile.TemporaryDirectory(prefix="product-smoke-") as profile, server() as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            for page_name, expected_text in PAGES.items():
                try:
                    for attempt in range(2):
                        try:
                            body, dom, layout = renderer.render(f"{base_url}/{page_name}", width, height)
                            break
                        except PlaywrightTimeoutError:
                            if attempt:
                                raise
                    validate_base_page(page_name, expected_text, body, dom, layout, live_values.get(page_name, []), viewport)
                    results.append({"viewport": viewport, "page": page_name, "status": "pass"})
                except (RuntimeError, PlaywrightError) as error:
                    failures.append({"viewport": viewport, "page": page_name, "error": str(error)})

    failed_verification = {**deepcopy(analysis_verification), "status": "fail", "failures": ["fixture failure"]}
    stale_analysis = deepcopy(analysis)
    stale_analysis["generated_at"] = shift_time(analysis["generated_at"], -31)
    stale_verification = {**deepcopy(analysis_verification), "analysis_generated_at": stale_analysis["generated_at"]}
    fixtures = [
        ("verification-fail", {
            "/data/daily/timescale_intelligence_verification.json": failed_verification,
        }),
        ("stale-analysis", {
            "/data/daily/timescale_intelligence.json": stale_analysis,
            "/data/daily/timescale_intelligence_verification.json": stale_verification,
        }),
    ]
    for fixture_name, overrides in fixtures:
        with server(overrides) as base_url:
            for viewport, (width, height) in VIEWPORTS.items():
                for page_name in ANALYSIS_PAGES:
                    try:
                        body, dom, layout = renderer.render(f"{base_url}/{page_name}", width, height)
                        assert_no_horizontal_overflow(layout, f"{viewport} {page_name}:{fixture_name}")
                        if render_status(dom) != "fail" or 'data-conclusions-visible="false"' not in dom:
                            raise RuntimeError(f"{fixture_name} 未 fail closed")
                        if "封鎖" not in body and "不可用" not in body:
                            raise RuntimeError(f"{fixture_name} 未顯示清楚診斷")
                        results.append({"viewport": viewport, "page": f"{page_name}:{fixture_name}", "status": "pass"})
                    except (RuntimeError, PlaywrightError) as error:
                        failures.append({"viewport": viewport, "page": f"{page_name}:{fixture_name}", "error": str(error)})

    failed_ai_verification = {**deepcopy(ai_verification), "status": "fail", "failures": ["fixture failure"]}
    with server({"/data/daily/ai_intelligence_verification.json": failed_ai_verification}) as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            try:
                body, dom, layout = renderer.render(f"{base_url}/x-intelligence.html", width, height)
                assert_no_horizontal_overflow(layout, f"{viewport} x-intelligence.html:verification-fail")
                if render_status(dom) != "fail" or 'data-lead-visible="false"' not in dom:
                    raise RuntimeError("AI verification-fail 未封鎖主文")
                if lead_brief["headline"] in body or "AI 情報已封鎖" not in body:
                    raise RuntimeError("AI verification-fail 沿用舊主文或缺少診斷")
                results.append({"viewport": viewport, "page": "x-intelligence.html:verification-fail", "status": "pass"})
            except (RuntimeError, PlaywrightError) as error:
                failures.append({"viewport": viewport, "page": "x-intelligence.html:verification-fail", "error": str(error)})

    failed_market_editorial_verification = {**deepcopy(market_editorial_verification), "status": "fail", "failures": ["fixture failure"]}
    with server({"/data/daily/market_editorial_verification.json": failed_market_editorial_verification}) as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            try:
                body, dom, layout = renderer.render(f"{base_url}/market-intelligence.html", width, height)
                assert_no_horizontal_overflow(layout, f"{viewport} market-intelligence.html:verification-fail")
                if render_status(dom) != "fail" or 'data-lead-visible="false"' not in dom or 'data-conclusions-visible="false"' not in dom:
                    raise RuntimeError("市場總編 verification-fail 未封鎖主文")
                if market_lead["headline"] in body or "市場總編已封鎖" not in body:
                    raise RuntimeError("市場總編 verification-fail 沿用舊主文或缺少診斷")
                results.append({"viewport": viewport, "page": "market-intelligence.html:verification-fail", "status": "pass"})
            except (RuntimeError, PlaywrightError) as error:
                failures.append({"viewport": viewport, "page": "market-intelligence.html:verification-fail", "error": str(error)})
    renderer.close()
    print(json.dumps({"browser": renderer.executable_path, "checks": len(results), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
