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
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "index.html": "今天只看四件事",
    "market-monitor.html": "先看四個市場結論",
    "x-intelligence.html": "每天精進三件事",
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


def shift_datetime_strings(value: object, delta: timedelta) -> object:
    if isinstance(value, dict):
        return {key: shift_datetime_strings(item, delta) for key, item in value.items()}
    if isinstance(value, list):
        return [shift_datetime_strings(item, delta) for item in value]
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (parsed + delta).isoformat()
        except ValueError:
            return value
    return value


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
                    rendered_links = set(re.findall(r'href="([^"#?]+)', dom, flags=re.IGNORECASE))
                    missing_navigation = [target for target in PAGES if target not in rendered_links]
                    if missing_navigation:
                        raise RuntimeError(f"渲染後主要導航缺漏：{', '.join(missing_navigation)}")
                    if page == "index.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("今日決策缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-conclusions-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("今日決策品質狀態與結論可見性不一致")
                        if status in {"pass", "degraded"} and ("載入失敗" in body or "資料封鎖" in body):
                            raise RuntimeError("可讀資料被錯誤封鎖")
                    if page == "market-monitor.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("市場雷達缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-conclusions-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("市場雷達品質狀態與結論可見性不一致")
                        if status in {"pass", "degraded"} and ("載入失敗" in body or "停止顯示" in body or "資料封鎖" in body):
                            raise RuntimeError("市場雷達可讀資料被錯誤封鎖")
                        if status != "fail" and 'data-core-checks="13/13"' not in dom:
                            raise RuntimeError("市場雷達核心欄位未完整渲染")
                        if 'data-page-overflow="false"' not in dom:
                            raise RuntimeError("市場雷達發生頁面水平溢位")
                    if page == "dashboard.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("策略研究室缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-conclusions-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("策略研究室品質狀態與每日結論可見性不一致")
                        if status == "degraded" and 'data-execution-grade="false"' not in dom:
                            raise RuntimeError("策略研究室降級狀態未明示只供研究")
                    if page == "analytics.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("專業分析缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-conclusions-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("專業分析品質狀態與結論可見性不一致")
                        if status == "degraded" and ('data-execution-grade="false"' not in dom or "只供研究" not in body):
                            raise RuntimeError("專業分析降級狀態未阻止執行級解讀")
                    if page == "daily-extensions.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("每日延伸缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-conclusions-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("每日延伸品質狀態與觀點可見性不一致")
                        if status == "degraded" and 'data-execution-grade="false"' not in dom:
                            raise RuntimeError("每日延伸降級狀態未阻止執行級解讀")
                    if page == "x-intelligence.html":
                        status_match = re.search(r'data-render-status="(pass|degraded|unconfigured|fail)"', dom)
                        if not status_match:
                            raise RuntimeError("X 情報缺少可驗證的渲染狀態")
                        status = status_match.group(1)
                        if 'data-page-overflow="false"' not in dom:
                            raise RuntimeError("X 情報發生頁面水平溢位")
                        if status != "fail" and 'data-category-count="3"' not in dom:
                            raise RuntimeError("X 情報三分類未完整載入")
                        if status != "fail" and 'data-action-count="3"' not in dom:
                            raise RuntimeError("AI 情報每日三個精進動作未完整載入")
                        expected_visibility = "true" if status in {"pass", "degraded"} else "false"
                        if f'data-feed-visible="{expected_visibility}"' not in dom:
                            raise RuntimeError("X 情報品質狀態與消息可見性不一致")
                    results.append({"viewport": viewport, "page": page, "status": "pass"})
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    failures.append({"viewport": viewport, "page": page, "error": str(error)})
    market_universe = json.loads((ROOT / "data/daily/market_universe.json").read_text(encoding="utf-8-sig"))
    aged_market = shift_datetime_strings(deepcopy(market_universe), timedelta(hours=-2.5))
    with tempfile.TemporaryDirectory(prefix="market-freshness-window-") as profile, server({"/data/daily/market_universe.json": aged_market}) as base_url:
        for viewport, (width, height) in VIEWPORTS.items():
            try:
                page_profile = Path(profile) / viewport
                page_profile.mkdir(parents=True, exist_ok=True)
                body, dom = rendered_body(browser, str(page_profile), f"{base_url}/market-monitor.html", width, height)
                if 'data-render-status="degraded"' not in dom or 'data-conclusions-visible="true"' not in dom:
                    raise RuntimeError("市場雷達 2.5 小時更新窗被錯誤封鎖")
                if "載入失敗" in body or "停止顯示" in body or "資料封鎖" in body:
                    raise RuntimeError("市場雷達把批次內合格來源誤判為瀏覽時逾時")
                results.append({"viewport": viewport, "page": "market-monitor.html:freshness-window", "status": "pass"})
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                failures.append({"viewport": viewport, "page": "market-monitor.html:freshness-window", "error": str(error)})
    verification = json.loads((ROOT / "data/daily/agent_verification_report.json").read_text(encoding="utf-8-sig"))
    analytics = json.loads((ROOT / "data/daily/institutional_analytics.json").read_text(encoding="utf-8-sig"))
    logic = json.loads((ROOT / "data/daily/logic_audit.json").read_text(encoding="utf-8-sig"))
    snapshot = json.loads((ROOT / "data/daily/latest_snapshot.json").read_text(encoding="utf-8-sig"))
    extensions = json.loads((ROOT / "data/daily/daily_extensions.json").read_text(encoding="utf-8-sig"))
    fixture_now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    fixture_yesterday = str((datetime.now(timezone.utc) + timedelta(hours=8)).date() - timedelta(days=1))
    cross_day_snapshot = {**deepcopy(snapshot), "date": fixture_yesterday, "generated_at": fixture_now}
    cross_day_verification = {
        **deepcopy(verification),
        "date": fixture_yesterday,
        "verified_at": fixture_now,
        "snapshot_generated_at": fixture_now,
        "status": "pass",
        "failures": [],
        "degradations": [],
    }
    cross_day_logic = {**deepcopy(logic), "date": fixture_yesterday, "generated_at": fixture_now, "snapshot_generated_at": fixture_now, "status": "consistent"}
    cross_day_analytics = {
        **deepcopy(analytics),
        "date": fixture_yesterday,
        "generated_at": fixture_now,
        "snapshot_generated_at": fixture_now,
        "quality": {
            **deepcopy(analytics.get("quality", {})),
            "verification_status": "pass",
            "verification_date_matches_snapshot": True,
            "verification_bound_to_snapshot": True,
            "publication_mode": "full",
            "logic_audit_status": "consistent",
        },
        "logic_audit": {**deepcopy(analytics.get("logic_audit", {})), "status": "consistent"},
    }
    cross_day_extensions = {**deepcopy(extensions), "current_date": fixture_yesterday, "updated_at": fixture_now, "snapshot_generated_at": fixture_now}
    fixtures = [
        {
            "name": "degraded",
            "verification": {**verification, "status": "degraded", "failures": [], "degradations": ["測試：主要來源受限，備援來源已接手"]},
            "analytics": {**analytics, "quality": {**analytics.get("quality", {}), "verification_status": "degraded"}},
            "logic": logic,
            "expected_status": "degraded",
            "should_show": True,
        },
        {
            "name": "fail",
            "verification": {**verification, "status": "fail", "failures": [], "degradations": []},
            "analytics": {**analytics, "quality": {**analytics.get("quality", {}), "verification_status": "fail"}},
            "logic": logic,
            "expected_status": "fail",
            "should_show": False,
        },
        {
            "name": "logic-mismatch",
            "verification": verification,
            "analytics": analytics,
            "logic": {**logic, "status": "contradiction"},
            "expected_status": "fail",
            "should_show": False,
        },
        {
            "name": "cross-day-fresh",
            "verification": cross_day_verification,
            "analytics": cross_day_analytics,
            "logic": cross_day_logic,
            "expected_status": "degraded",
            "should_show": True,
            "expected_calendar_lag": "1",
            "extra_overrides": {
                "/data/daily/latest_snapshot.json": cross_day_snapshot,
                "/data/daily/daily_extensions.json": cross_day_extensions,
            },
        },
    ]
    for fixture in fixtures:
        fixture_name = fixture["name"]
        expected_status = fixture["expected_status"]
        should_show = fixture["should_show"]
        overrides = {
            "/data/daily/agent_verification_report.json": fixture["verification"],
            "/data/daily/institutional_analytics.json": fixture["analytics"],
            "/data/daily/logic_audit.json": fixture["logic"],
        }
        overrides.update(fixture.get("extra_overrides", {}))
        with tempfile.TemporaryDirectory(prefix=f"quality-gate-{fixture_name}-") as profile, server(overrides) as base_url:
            for viewport, (width, height) in VIEWPORTS.items():
                for page in ("index.html", "dashboard.html", "analytics.html", "daily-extensions.html"):
                    try:
                        page_profile = Path(profile) / viewport / page.replace(".html", "")
                        page_profile.mkdir(parents=True, exist_ok=True)
                        body, dom = rendered_body(browser, str(page_profile), f"{base_url}/{page}", width, height)
                        if f'data-render-status="{expected_status}"' not in dom:
                            raise RuntimeError(f"{fixture_name} fixture 未呈現預期狀態 {expected_status}")
                        if f'data-conclusions-visible="{str(should_show).lower()}"' not in dom:
                            raise RuntimeError(f"{fixture_name} fixture 結論可見性錯誤")
                        if should_show and ("載入失敗" in body or "資料封鎖" in body or "每日資料失敗" in body):
                            raise RuntimeError("可讀 fixture 被錯誤封鎖")
                        if fixture.get("expected_calendar_lag") and f'data-calendar-lag-days="{fixture["expected_calendar_lag"]}"' not in dom:
                            raise RuntimeError(f"{fixture_name} fixture 缺少跨日狀態標記")
                        if not should_show and page == "index.html" and ("資料封鎖" not in body or "全部交易封鎖" not in body):
                            raise RuntimeError("首頁故障 fixture 未封鎖交易結論")
                        if not should_show and page == "dashboard.html" and ("FAIL CLOSED" not in body or "所有交易動作封鎖" not in body or "已封鎖" not in body):
                            raise RuntimeError("策略研究室故障 fixture 未封鎖每日數字")
                        if not should_show and page == "analytics.html" and ("FAIL CLOSED" not in body or "所有交易動作封鎖" not in body):
                            raise RuntimeError("專業分析故障 fixture 未封鎖交易結論")
                        if not should_show and page == "daily-extensions.html" and ("FAIL CLOSED" not in body or "三個延伸觀點已封鎖" not in body):
                            raise RuntimeError("每日延伸故障 fixture 未封鎖研究觀點")
                        results.append({"viewport": viewport, "page": f"{page}:{fixture_name}", "status": "pass"})
                    except (RuntimeError, subprocess.TimeoutExpired) as error:
                        failures.append({"viewport": viewport, "page": f"{page}:{fixture_name}", "error": str(error)})
    print(json.dumps({"browser": browser, "checks": len(results), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
