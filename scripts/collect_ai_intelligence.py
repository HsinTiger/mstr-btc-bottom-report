#!/usr/bin/env python3
"""Collect resilient AI intelligence from official feeds, GitHub releases, and arXiv."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "daily" / "ai_intelligence.json"
HISTORY_PATH = ROOT / "data" / "daily" / "ai_intelligence_history.json"
WINDOW_HOURS = 14 * 24
MAX_ITEMS_PER_CATEGORY = 8
MAX_RESPONSE_BYTES = 5_000_000

CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "ai-application-monetization",
        "title": "AI 應用與變現",
        "purpose": "追蹤可實際採用的產品、代理、工作流、API、定價與企業變現證據。",
        "terms": ["agent", "api", "app", "product", "enterprise", "customer", "pricing", "launch", "release", "developer", "codex", "chatgpt", "business", "workflow", "tool", "slack"],
    },
    {
        "id": "engineering-model-progress",
        "title": "工程方法與模型進展",
        "purpose": "追蹤模型、評測、推論、訓練、框架與可重現的開源工程進展。",
        "terms": ["model", "benchmark", "eval", "inference", "training", "reasoning", "research", "framework", "library", "release", "gpu", "performance", "latency", "open source", "weights", "agent"],
    },
]

SOURCES: list[dict[str, Any]] = [
    {"key": "openai-news", "label": "OpenAI News", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://openai.com/news/rss.xml", "tier": "官方公司", "weight": 1.00},
    {"key": "google-ai-blog", "label": "Google AI Blog", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://blog.google/technology/ai/rss/", "tier": "官方公司", "weight": 0.98},
    {"key": "cursor-changelog", "label": "Cursor Changelog", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://www.cursor.com/changelog/rss.xml", "allowed_hosts": ["www.cursor.com", "cursor.com"], "tier": "官方產品", "weight": 0.96},
    {"key": "vercel-ai-releases", "label": "Vercel AI SDK Releases", "kind": "github_release", "category_id": "ai-application-monetization", "url": "https://api.github.com/repos/vercel/ai/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "deepmind-blog", "label": "Google DeepMind Blog", "kind": "official_feed", "category_id": "engineering-model-progress", "url": "https://deepmind.google/blog/rss.xml", "tier": "官方研究", "weight": 1.00},
    {"key": "nvidia-developer", "label": "NVIDIA Developer Blog", "kind": "official_feed", "category_id": "engineering-model-progress", "url": "https://developer.nvidia.com/blog/feed/", "tier": "官方工程", "weight": 0.97},
    {"key": "huggingface-blog", "label": "Hugging Face Blog", "kind": "official_feed", "category_id": "engineering-model-progress", "url": "https://huggingface.co/blog/feed.xml", "tier": "官方平台", "weight": 0.94},
    {"key": "vllm-releases", "label": "vLLM Releases", "kind": "github_release", "category_id": "engineering-model-progress", "url": "https://api.github.com/repos/vllm-project/vllm/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "transformers-releases", "label": "Transformers Releases", "kind": "github_release", "category_id": "engineering-model-progress", "url": "https://api.github.com/repos/huggingface/transformers/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "arxiv-ai", "label": "arXiv cs.AI 最新研究", "kind": "preprint_feed", "category_id": "engineering-model-progress", "url": "https://export.arxiv.org/api/query?search_query=cat%3Acs.AI&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending", "allowed_hosts": ["export.arxiv.org", "arxiv.org"], "tier": "未同儕審查研究", "weight": 0.72},
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().replace(microsecond=0).isoformat()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact_text(value: Any, limit: int = 700) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def term_matches(text: str, term: str) -> bool:
    normalized_text = re.sub(r"[-_]", " ", text.lower())
    normalized_term = re.sub(r"[-_]", " ", term.lower()).strip()
    escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
    plural = "" if normalized_term.endswith("s") else "s?"
    return re.search(rf"(?<![a-z0-9]){escaped}{plural}(?![a-z0-9])", normalized_text) is not None


def request_bytes(source: dict[str, Any]) -> bytes:
    headers = {
        "User-Agent": "mstr-btc-bottom-report-ai-intelligence/1.0",
        "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml;q=0.9, */*;q=0.5",
    }
    if source["kind"] == "github_release" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN'].strip()}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(source["url"], headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
    return body


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def first_text(entry: ET.Element, names: set[str]) -> str:
    for child in list(entry):
        if local_name(child.tag) in names:
            value = compact_text(" ".join(child.itertext()))
            if value:
                return value
    return ""


def entry_link(entry: ET.Element) -> str:
    for child in list(entry):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        relation = child.attrib.get("rel", "alternate")
        if href and relation in {"alternate", ""}:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_feed(body: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    entries = [element for element in root.iter() if local_name(element.tag) in {"item", "entry"}]
    parsed: list[dict[str, Any]] = []
    for entry in entries:
        title = first_text(entry, {"title"})
        link = entry_link(entry)
        published = first_text(entry, {"pubDate", "published", "updated", "date"})
        summary = first_text(entry, {"description", "summary", "content", "encoded"})
        if title and link and published:
            parsed.append({"title": title, "url": link, "published_at": published, "summary": summary})
    return parsed


def parse_github_releases(body: bytes) -> list[dict[str, Any]]:
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("GitHub releases response is not a list")
    return [
        {
            "title": compact_text(item.get("name") or item.get("tag_name")),
            "url": str(item.get("html_url") or ""),
            "published_at": item.get("published_at") or item.get("created_at"),
            "summary": compact_text(item.get("body")),
        }
        for item in data
        if not item.get("draft") and item.get("html_url")
    ]


def source_items(source: dict[str, Any], body: bytes) -> list[dict[str, Any]]:
    return parse_github_releases(body) if source["kind"] == "github_release" else parse_feed(body)


def relevance_score(category: dict[str, Any], text: str, source: dict[str, Any]) -> float:
    hits = sum(1 for term in category["terms"] if term_matches(text, term))
    baseline = 0.50 if source["kind"] in {"github_release", "preprint_feed"} else 0.0
    return max(baseline, min(1.0, hits / 4))


def why_it_matters(category_id: str, source_kind: str, text: str) -> str:
    if source_kind == "preprint_feed":
        return "這是前沿研究線索，不代表已被同儕審查或可直接部署；先找程式碼與獨立重現。"
    if source_kind == "github_release":
        return "這是可實際測試的正式版本變更；價值在功能、相容性、速度與成本，不在發布聲量。"
    if category_id == "ai-application-monetization":
        if any(term_matches(text, term) for term in ["pricing", "enterprise", "customer", "business"]):
            return "這是採用或付費線索；仍要區分產品宣布、客戶案例與已認列收入。"
        return "這可能改變日常 AI 工作流；先用真實任務比較品質、時間與成本，再決定是否採用。"
    if any(term_matches(text, term) for term in ["benchmark", "eval", "score"]):
        return "先核對評測集、基準線與限制；單一榜單分數不能代表全面能力。"
    return "這是模型或工程進展；只有在方法公開、可重現且改善實際瓶頸時才提高權重。"


def next_action(category_id: str, source_kind: str, text: str) -> str:
    if source_kind == "preprint_feed":
        return "只摘一個可驗證方法；找到程式碼或獨立重現前，先列入觀察清單。"
    if source_kind == "github_release":
        return "先讀 breaking changes，在隔離分支跑最小回歸；不要直接升級正式工作流。"
    if category_id == "ai-application-monetization":
        return "挑一個既有任務做 30 分鐘 A/B 測試，記錄品質、耗時與成本；三項至少改善一項才保留。"
    if any(term_matches(text, term) for term in ["benchmark", "eval", "reasoning"]):
        return "讀方法與限制段，確認基準、樣本與失敗案例，再決定是否納入你的評測集。"
    return "用 10 個你自己的真實案例做最小測試，保留失敗紀錄，不只看官方示範。"


def normalize_source(source: dict[str, Any], raw_items: list[dict[str, Any]], generated: datetime) -> list[dict[str, Any]]:
    category = next(item for item in CATEGORIES if item["id"] == source["category_id"])
    cutoff = generated - timedelta(hours=WINDOW_HOURS)
    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        created = parse_time(raw.get("published_at"))
        title = compact_text(raw.get("title"), 240)
        summary = compact_text(raw.get("summary"), 520)
        url = str(raw.get("url") or "").strip()
        if not created or created < cutoff or created > generated + timedelta(minutes=5) or not title or not url.startswith("https://"):
            continue
        text = compact_text(f"{title} — {summary}" if summary else title)
        relevance = relevance_score(category, text, source)
        if relevance <= 0:
            continue
        age_hours = max(0.0, (generated - created).total_seconds() / 3600)
        recency = max(0.0, 1 - age_hours / WINDOW_HOURS)
        score = round(100 * (0.50 * float(source["weight"]) + 0.30 * relevance + 0.20 * recency), 1)
        item_id = f"{source['key']}:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]}"
        normalized.append({
            "id": item_id,
            "url": url,
            "created_at": created.replace(microsecond=0).isoformat(),
            "title": title,
            "text": text,
            "source_key": source["key"],
            "category_id": category["id"],
            "source_label": source["label"],
            "source_type": source["kind"],
            "source_tier": source["tier"],
            "why_it_matters": why_it_matters(category["id"], source["kind"], text),
            "next_action": next_action(category["id"], source["kind"], text),
            "ranking_score_0_100": score,
            "ranking_components_0_100": {
                "source_quality": round(100 * float(source["weight"]), 1),
                "keyword_relevance": round(100 * relevance, 1),
                "recency": round(100 * recency, 1),
            },
            "decision_use": "learning_context_not_execution_gate",
        })
    return sorted(normalized, key=lambda item: (item["ranking_score_0_100"], item["created_at"]), reverse=True)


def select_items(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for item in sorted(candidates, key=lambda value: (value["ranking_score_0_100"], value["created_at"]), reverse=True):
        if source_counts.get(item["source_key"], 0) >= 3:
            continue
        selected.append(item)
        source_counts[item["source_key"]] = source_counts.get(item["source_key"], 0) + 1
        if len(selected) >= MAX_ITEMS_PER_CATEGORY:
            break
    return selected


def daily_actions(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [item for category in categories for item in category["items"]]
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    practical_terms = ["api", "agent", "workflow", "tool", "developer", "release", "model", "inference", "eval", "performance", "codex", "cursor", "sdk", "training"]
    low_action_terms = ["safety", "policy", "teens", "children", "scorecard"]

    def action_priority(item: dict[str, Any]) -> tuple[int, int, float]:
        text = str(item.get("text") or "")
        practical = sum(term_matches(text, term) for term in practical_terms)
        low_action = sum(term_matches(text, term) for term in low_action_terms)
        return practical - 2 * low_action, item["source_type"] != "preprint_feed", float(item["ranking_score_0_100"])

    ordered = sorted(
        candidates,
        key=action_priority,
        reverse=True,
    )

    for category in CATEGORIES:
        item = next((value for value in ordered if value["category_id"] == category["id"] and value["source_key"] not in used_sources), None)
        if item:
            selected.append({
                "item_id": item["id"],
                "title": item["title"],
                "action": item["next_action"],
                "source_label": item["source_label"],
                "url": item["url"],
            })
            used_sources.add(item["source_key"])

    for item in ordered:
        if len(selected) == 3:
            break
        if item["source_key"] in used_sources:
            continue
        selected.append({
            "item_id": item["id"],
            "title": item["title"],
            "action": item["next_action"],
            "source_label": item["source_label"],
            "url": item["url"],
        })
        used_sources.add(item["source_key"])
    return selected


def update_history(output: dict[str, Any]) -> dict[str, Any]:
    history = read_json(HISTORY_PATH, {"schema": 1, "items": []})
    indexed = {str(item.get("id")): item for item in history.get("items", []) if item.get("id")}
    for category in output.get("categories", []):
        for item in category.get("items", []):
            indexed[item["id"]] = {**item, "category_id": category["id"], "category_title": category["title"]}
    cutoff = now() - timedelta(days=180)
    items = [item for item in indexed.values() if (parse_time(item.get("created_at")) or cutoff) >= cutoff]
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    successful = output.get("quality", {}).get("status") in {"pass", "degraded"}
    return {
        "schema": 1,
        "updated_at": output["generated_at"] if successful else history.get("updated_at"),
        "last_attempt_at": output["generated_at"],
        "last_successful_fetch_at": output["generated_at"] if successful else history.get("last_successful_fetch_at"),
        "quality": {"status": output.get("quality", {}).get("status")},
        "items": items[:2000],
    }


def main() -> int:
    generated = now()
    checks: list[dict[str, Any]] = []
    candidates: dict[str, list[dict[str, Any]]] = {category["id"]: [] for category in CATEGORIES}
    failures: list[str] = []
    for source in SOURCES:
        check = {
            "source_key": source["key"],
            "source_label": source["label"],
            "source_type": source["kind"],
            "category_id": source["category_id"],
            "url": source["url"],
            "checked_at": generated.replace(microsecond=0).isoformat(),
        }
        try:
            raw_items = source_items(source, request_bytes(source))
            items = normalize_source(source, raw_items, generated)
            candidates[source["category_id"]].extend(items)
            check.update({"status": "pass", "raw_items": len(raw_items), "eligible_items": len(items)})
        except Exception as error:
            message = f"{source['label']}：{error}"
            failures.append(message)
            check.update({"status": "fail", "raw_items": 0, "eligible_items": 0, "error": str(error)})
        checks.append(check)

    categories: list[dict[str, Any]] = []
    degradations: list[str] = []
    for category in CATEGORIES:
        items = select_items(candidates[category["id"]])
        status = "pass" if len(items) >= 3 else "degraded" if items else "fail"
        if len(items) < 3:
            degradations.append(f"{category['title']}：14 日內只有 {len(items)} 則通過條件的官方／研究消息。")
        categories.append({
            "id": category["id"],
            "title": category["title"],
            "purpose": category["purpose"],
            "status": status,
            "source_keys": [source["key"] for source in SOURCES if source["category_id"] == category["id"]],
            "items": items,
        })

    successful_sources = sum(check["status"] == "pass" for check in checks)
    if any(category["status"] == "fail" for category in categories) or not any(category["items"] for category in categories):
        status = "fail"
    elif failures or degradations or successful_sources < 4:
        status = "degraded"
    else:
        status = "pass"
    generated_at = generated.replace(microsecond=0).isoformat()
    output = {
        "schema": 1,
        "generated_at": generated_at,
        "window_hours": WINDOW_HOURS,
        "quality": {
            "status": status,
            "provider": "official_feeds_github_releases_arxiv",
            "execution_gate_eligible": False,
            "failures": failures if status == "fail" else [],
            "degradations": failures + degradations if status == "degraded" else [],
            "method": "official RSS/Atom + official GitHub releases + arXiv preprints; source diversity cap; deterministic ranking",
        },
        "summary": {
            "categories": len(categories),
            "posts": sum(len(category["items"]) for category in categories),
            "unique_sources": len({item["source_key"] for category in categories for item in category["items"]}),
            "successful_sources": successful_sources,
            "failed_sources": len(checks) - successful_sources,
        },
        "source_checks": checks,
        "daily_actions": daily_actions(categories),
        "categories": categories,
    }
    write_json(OUTPUT_PATH, output)
    write_json(HISTORY_PATH, update_history(output))
    print(json.dumps({"output": str(OUTPUT_PATH), "status": status, **output["summary"], "actions": len(output["daily_actions"])}, ensure_ascii=False))
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
