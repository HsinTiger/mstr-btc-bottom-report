#!/usr/bin/env python3
"""Scoped desktop/mobile and fail-closed smoke for the AI editor page."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from smoke_product_surfaces import (
    BrowserRenderer,
    VIEWPORTS,
    assert_no_horizontal_overflow,
    browser_path,
    render_status,
    server,
)

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "data" / "daily" / name).read_text(encoding="utf-8-sig"))


def main() -> int:
    source = load("ai_intelligence.json")
    verification = load("ai_intelligence_verification.json")
    lead = next(
        brief for brief in source["editorial_digest"]["briefs"]
        if brief["id"] == source["editorial_digest"]["lead_brief_id"]
    )
    renderer = BrowserRenderer(browser_path())
    results: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="ai-editor-smoke-") as profile, server() as base_url:
            _ = profile
            for viewport, (width, height) in VIEWPORTS.items():
                try:
                    body, dom, layout = renderer.render(f"{base_url}/x-intelligence.html", width, height)
                    assert_no_horizontal_overflow(layout, f"{viewport} AI 總編")
                    if render_status(dom) not in {"pass", "degraded"}:
                        raise RuntimeError("AI 總編 live 狀態不可讀")
                    required = [lead["headline"], lead["variant_view"], lead["what_changed"], lead["evidence"][0]["source_excerpt"]]
                    if any(value not in body for value in required):
                        raise RuntimeError("AI 總編主文、假說、跨日差異或來源摘錄未渲染")
                    if 'data-editorial-count="3"' not in dom or 'data-lead-visible="true"' not in dom:
                        raise RuntimeError("AI 總編三篇短評或主文未完整載入")
                    if body.find("今日機構觀點") > body.find("今天先做這三件事"):
                        raise RuntimeError("AI 總編沒有維持結論先行")
                    results.append({"viewport": viewport, "state": "live"})
                except (RuntimeError, PlaywrightError) as error:
                    failures.append({"viewport": viewport, "state": "live", "error": str(error)})

        failed = {**deepcopy(verification), "status": "fail", "failures": ["fixture failure"]}
        with server({"/data/daily/ai_intelligence_verification.json": failed}) as base_url:
            for viewport, (width, height) in VIEWPORTS.items():
                try:
                    body, dom, layout = renderer.render(f"{base_url}/x-intelligence.html", width, height)
                    assert_no_horizontal_overflow(layout, f"{viewport} AI 總編 fail-closed")
                    if render_status(dom) != "fail" or 'data-lead-visible="false"' not in dom:
                        raise RuntimeError("AI 總編驗證失敗時未封鎖主文")
                    if lead["headline"] in body or "AI 情報已封鎖" not in body:
                        raise RuntimeError("AI 總編驗證失敗時沿用舊主文或缺少診斷")
                    results.append({"viewport": viewport, "state": "fail-closed"})
                except (RuntimeError, PlaywrightError) as error:
                    failures.append({"viewport": viewport, "state": "fail-closed", "error": str(error)})
    finally:
        renderer.close()
    print(json.dumps({"browser": renderer.executable_path, "checks": len(results), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
