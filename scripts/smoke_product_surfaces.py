#!/usr/bin/env python3
"""Render every active page and verify live, stale, and failed-analysis states."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError as error:
    raise SystemExit("缺少 browser smoke 依賴；請先執行 pip install -r requirements-smoke.txt") from error

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "market-intelligence.html": "八個研究桌",
    "market-monitor.html": "先看四個市場結論",
    "x-intelligence.html": "今天真正改變了什麼",
    "wiki.html": "最後驗證",
    "site-overview.html": "頁面程式",
}
STATUS_PAGES = {"market-intelligence.html", "market-monitor.html", "x-intelligence.html"}
RETIRED_PAGES = {"analytics.html", "dashboard.html", "daily-extensions.html"}
TIMESCALE_ARTIFACTS = {
    "price": "data/daily/timescale_price_history.json",
    "data_verification": "data/daily/timescale_data_verification.json",
    "analysis": "data/daily/timescale_intelligence.json",
    "history": "data/daily/timescale_intelligence_history.json",
    "analysis_verification": "data/daily/timescale_intelligence_verification.json",
}
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
                analysisCardCount: document.querySelectorAll('[data-analysis-card="true"]').length,
                evidenceMetricCount: document.querySelectorAll('details[data-evidence-metric]').length,
                evidenceMetricUniqueCount: new Set(
                    [...document.querySelectorAll('details[data-evidence-metric]')].map(item => item.dataset.evidenceMetric)
                ).size,
                evidenceSourceLinkCount: document.querySelectorAll('.evidence-source a[href]').length,
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
                currentPath: location.pathname,
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


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "product-surface-smoke"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def validate_timescale_artifacts(base_url: str) -> None:
    payloads = {name: fetch_json(f"{base_url}/{path}") for name, path in TIMESCALE_ARTIFACTS.items()}
    if any(payload.get("schema") != 1 for payload in payloads.values()):
        raise RuntimeError("四週期後端 artifact schema 不相容")
    price = payloads["price"]
    data_verification = payloads["data_verification"]
    analysis = payloads["analysis"]
    history = payloads["history"]
    analysis_verification = payloads["analysis_verification"]
    if data_verification.get("status") != "pass" or analysis_verification.get("status") != "pass":
        raise RuntimeError("四週期後端 verifier 未通過")
    if price.get("generated_at") != data_verification.get("history_generated_at") or price.get("snapshot_generated_at") != data_verification.get("snapshot_generated_at"):
        raise RuntimeError("四週期價格 artifact 與 verifier 批次未綁定")
    if analysis.get("generated_at") != analysis_verification.get("analysis_generated_at") or analysis.get("snapshot_generated_at") != analysis_verification.get("snapshot_generated_at"):
        raise RuntimeError("四週期分析 artifact 與 verifier 批次未綁定")
    if not analysis_verification.get("lineage") or not all(analysis_verification["lineage"].values()):
        raise RuntimeError("四週期分析 lineage 或新鮮度未全部通過")
    if set(analysis.get("technical_horizons", {})) != {"weekly", "monthly"}:
        raise RuntimeError("完成週 K／月 K 技術層缺漏")
    required_checks = set()
    for timeframe in ("weekly", "monthly"):
        technical = analysis["technical_horizons"][timeframe]
        sentiment = analysis.get("news_sentiment", {}).get(timeframe, {})
        if technical.get("bar_basis") != f"completed_{timeframe}_candles" or int(technical.get("bars", 0)) < 35 or int(technical.get("source_count", 0)) < 2:
            raise RuntimeError(f"{timeframe} 完成 K 技術契約不完整")
        if len(sentiment.get("evidence", [])) < 4:
            raise RuntimeError(f"{timeframe} 消息情緒證據不足")
        required_checks.update({f"BTC_{timeframe}_rsi_14", f"BTC_{timeframe}_macd_histogram", f"BTC_{timeframe}_atr_14", f"BTC_{timeframe}_obv"})
    passed_checks = {item.get("name") for item in analysis_verification.get("checks", []) if item.get("status") == "pass"}
    if not required_checks.issubset(passed_checks):
        raise RuntimeError("完成週 K／月 K 核心指標缺少獨立重算")
    history_items = history.get("items", [])
    if history.get("updated_at") != analysis.get("generated_at") or not history_items or history_items[-1].get("generated_at") != analysis.get("generated_at"):
        raise RuntimeError("四週期分析與 append-only history 最新 revision 未綁定")


def validate_retired_pages(base_url: str) -> None:
    for page_name in RETIRED_PAGES:
        try:
            with urlopen(f"{base_url}/{page_name}", timeout=10) as response:
                status = response.status
        except HTTPError as error:
            if error.code == 404:
                continue
            raise RuntimeError(f"退場頁 {page_name} 回傳 HTTP {error.code}") from error
        raise RuntimeError(f"退場頁 {page_name} 仍可公開存取：HTTP {status}")


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
    if page_name == "market-monitor.html":
        status = render_status(dom)
        if status not in {"pass", "degraded"} or 'data-conclusions-visible="true"' not in dom:
            raise RuntimeError("即時市場品質契約未通過")
        if 'data-core-checks="14/14"' not in dom or 'data-page-overflow="false"' not in dom:
            raise RuntimeError("即時市場核心欄位或版面契約未通過")
        if 'data-evidence-complete="true"' not in dom or 'data-evidence-cards="30"' not in dom:
            raise RuntimeError("即時市場逐卡來源證據未完整載入")
        if (
            layout.get("analysisCardCount") != 30
            or layout.get("evidenceMetricCount") != 30
            or layout.get("evidenceMetricUniqueCount") != 30
            or layout.get("evidenceSourceLinkCount", 0) < 30
            or not layout.get("evidenceTextComplete")
            or not layout.get("sourceTimingComplete")
            or layout.get("minEvidenceLinkHeight", 0) < 44
            or layout.get("minEvidenceLinkFontPx", 0) < 12
        ):
            raise RuntimeError("即時市場來源證據數與分析卡數不一致")
        if "來源與驗證" not in body or "ETF 不是盤中即時資料" not in body:
            raise RuntimeError("即時市場未清楚揭露來源或 ETF 更新頻率")
    if page_name == "market-intelligence.html":
        status = render_status(dom)
        if status not in {"pass", "degraded"} or 'data-conclusions-visible="true"' not in dom:
            raise RuntimeError("市場總編 analysis-only 品質契約未通過")
        if 'data-desk-count="8"' not in dom or 'data-lead-visible="true"' not in dom:
            raise RuntimeError("市場總編主文或八個研究桌未完整載入")
        if not re.search(r'data-timescale-status="(?:pass|degraded)"', dom):
            raise RuntimeError("市場總編四週期與修訂證據未通過")
        if "週線／月線頂底判讀" not in body or "RSI 14" not in body or not re.search(r'<section(?=[^>]*id="technicalPulse")(?![^>]*class="[^"]*hidden)[^>]*>', dom):
            raise RuntimeError("市場總編週線／月線技術雙卡未完整顯示")
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
    market = json.loads((ROOT / "data/daily/market_universe.json").read_text(encoding="utf-8-sig"))
    ai = json.loads((ROOT / "data/daily/ai_intelligence.json").read_text(encoding="utf-8-sig"))
    ai_verification = json.loads((ROOT / "data/daily/ai_intelligence_verification.json").read_text(encoding="utf-8-sig"))
    market_editorial = json.loads((ROOT / "data/daily/market_editorial.json").read_text(encoding="utf-8-sig"))
    market_editorial_verification = json.loads((ROOT / "data/daily/market_editorial_verification.json").read_text(encoding="utf-8-sig"))
    timescale_verification = json.loads((ROOT / "data/daily/timescale_intelligence_verification.json").read_text(encoding="utf-8-sig"))
    lead_brief = next(item for item in ai["editorial_digest"]["briefs"] if item["id"] == ai["editorial_digest"]["lead_brief_id"])
    market_lead = next(item for item in market_editorial["desks"] if item["id"] == market_editorial["editorial_digest"]["lead_desk_id"])
    live_values = {
        "market-intelligence.html": [market_lead["headline"], market_lead["conclusion"], market_lead["evidence"][0]["display"]],
        "market-monitor.html": [browser_money(market["assets"]["BTC"]["price_usd"]), browser_money(market["assets"]["ETH"]["price_usd"])],
        "x-intelligence.html": [lead_brief["headline"], lead_brief["variant_view"], lead_brief["what_changed"], lead_brief["evidence"][0]["source_label"]],
        "site-overview.html": ["四週期價格與來源對帳", "四週期分析與修訂紀錄"],
    }
    with server() as base_url:
        try:
            validate_timescale_artifacts(base_url)
            results.append({"viewport": "server", "page": "timescale-backend-products", "status": "pass"})
        except (RuntimeError, OSError, json.JSONDecodeError) as error:
            failures.append({"viewport": "server", "page": "timescale-backend-products", "error": str(error)})
        try:
            validate_retired_pages(base_url)
            results.append({"viewport": "server", "page": "retired-pages-404", "status": "pass"})
        except (RuntimeError, OSError) as error:
            failures.append({"viewport": "server", "page": "retired-pages-404", "error": str(error)})
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

            try:
                body, _, layout = renderer.render(f"{base_url}/", width, height)
                assert_no_horizontal_overflow(layout, f"{viewport} root-redirect")
                if not str(layout.get("currentPath", "")).endswith("/market-intelligence.html"):
                    raise RuntimeError(f"根網址未導向市場總編：{layout.get('currentPath')}")
                if market_lead["headline"] not in body:
                    raise RuntimeError("根網址轉址後未載入市場總編主文")
                results.append({"viewport": viewport, "page": "root→market-intelligence.html", "status": "pass"})
            except (RuntimeError, PlaywrightError) as error:
                failures.append({"viewport": viewport, "page": "root→market-intelligence.html", "error": str(error)})

    failed_ai_verification = {**ai_verification, "status": "fail", "failures": ["fixture failure"]}
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

    failed_market_editorial_verification = {**market_editorial_verification, "status": "fail", "failures": ["fixture failure"]}
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

    failed_timescale_verification = {**timescale_verification, "status": "fail", "failures": ["fixture failure"]}
    with server({"/data/daily/timescale_intelligence_verification.json": failed_timescale_verification}) as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            try:
                body, dom, layout = renderer.render(f"{base_url}/market-intelligence.html", width, height)
                assert_no_horizontal_overflow(layout, f"{viewport} market-intelligence.html:timescale-verification-fail")
                if 'data-timescale-status="fail"' not in dom or "四週期證據已封鎖" not in body:
                    raise RuntimeError("四週期 verification-fail 未封鎖研究證據")
                if not re.search(r'<div(?=[^>]*id="timescaleContent")(?=[^>]*class="hidden")[^>]*>', dom):
                    raise RuntimeError("四週期 verification-fail 仍顯示舊結論")
                results.append({"viewport": viewport, "page": "market-intelligence.html:timescale-verification-fail", "status": "pass"})
            except (RuntimeError, PlaywrightError) as error:
                failures.append({"viewport": viewport, "page": "market-intelligence.html:timescale-verification-fail", "error": str(error)})
    renderer.close()
    print(json.dumps({"browser": renderer.executable_path, "checks": len(results), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
