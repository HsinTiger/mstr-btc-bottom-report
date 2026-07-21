#!/usr/bin/env python3
"""Collect curated X intelligence through the official recent-search API."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "daily" / "x_intelligence.json"
HISTORY_PATH = ROOT / "data" / "daily" / "x_intelligence_history.json"
ENDPOINT = "https://api.x.com/2/tweets/search/recent"
WINDOW_HOURS = 48
MAX_ITEMS_PER_CATEGORY = 8
MAX_PAGES_PER_CATEGORY = 2

CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "ai-application-monetization",
        "title": "AI 應用與變現",
        "purpose": "追蹤產品採用、企業付費、定價、通路與真正收入證據。",
        "accounts": {
            "openai": ("官方公司", 1.00),
            "anthropicai": ("官方公司", 1.00),
            "googledeepmind": ("官方公司", 1.00),
            "microsoftai": ("官方公司", 0.95),
            "nvidia": ("官方公司", 0.95),
            "perplexity_ai": ("官方公司", 0.90),
            "cursor_ai": ("官方產品", 0.90),
            "replit": ("官方產品", 0.85),
            "lovable_dev": ("官方產品", 0.85),
            "vercel": ("官方平台", 0.85),
        },
        "query_terms": ["AI", "agent", "enterprise", "API", "pricing", "revenue", "launch", "customers"],
        "relevance_terms": ["pricing", "revenue", "enterprise", "customer", "paid", "subscription", "launch", "api", "agent", "adoption"],
    },
    {
        "id": "engineering-model-progress",
        "title": "工程方法與模型進展",
        "purpose": "追蹤模型能力、評測、訓練、推論成本、工具鏈與可重現工程方法。",
        "accounts": {
            "openai": ("官方研究", 1.00),
            "anthropicai": ("官方研究", 1.00),
            "googledeepmind": ("官方研究", 1.00),
            "aiatmeta": ("官方研究", 0.95),
            "mistralai": ("官方研究", 0.95),
            "huggingface": ("官方平台", 0.90),
            "karpathy": ("策展工程師", 0.90),
            "vllm_project": ("開源專案", 0.90),
            "pytorch": ("開源專案", 0.90),
            "nvidiaaidev": ("官方工程", 0.90),
        },
        "query_terms": ["model", "benchmark", "eval", "inference", "training", "reasoning", "open-source", "research"],
        "relevance_terms": ["model", "benchmark", "eval", "inference", "training", "reasoning", "open source", "weights", "latency", "context"],
    },
    {
        "id": "crypto-us-stocks",
        "title": "Crypto 與美股",
        "purpose": "追蹤監管、流動性、ETF、公司事件與市場敘事；不直接作交易硬訊號。",
        "accounts": {
            "strategy": ("官方公司", 1.00),
            "coinbase": ("官方公司", 0.95),
            "coinbaseinsto": ("機構研究", 0.95),
            "blackrock": ("官方資管", 0.95),
            "ishares": ("官方 ETF", 0.95),
            "nasdaq": ("官方交易所", 0.95),
            "nyse": ("官方交易所", 0.95),
            "federalreserve": ("官方機構", 1.00),
            "secgov": ("官方機構", 1.00),
            "reuters": ("主流媒體", 0.85),
            "bloomberg": ("主流媒體", 0.85),
            "coindesk": ("產業媒體", 0.80),
            "glassnode": ("鏈上研究", 0.85),
        },
        "query_terms": ["BTC", "Bitcoin", "ETH", "Ethereum", "crypto", "MSTR", "ETF", "stocks", "Nasdaq", "S&P 500"],
        "relevance_terms": ["bitcoin", "btc", "ethereum", "eth", "crypto", "mstr", "etf", "stock", "nasdaq", "s&p", "earnings", "fed", "sec"],
    },
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().replace(microsecond=0).isoformat()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_query(category: dict[str, Any]) -> str:
    sources = " OR ".join(f"from:{username}" for username in category["accounts"])
    terms = " OR ".join(f'"{term}"' if " " in term else term for term in category["query_terms"])
    query = f"({sources}) ({terms}) -is:retweet"
    if len(query) > 512:
        raise ValueError(f"X query exceeds 512 characters: {category['id']}={len(query)}")
    return query


def request_recent_search(token: str, category: dict[str, Any]) -> dict[str, Any]:
    base_params = {
        "query": build_query(category),
        "max_results": "100",
        "start_time": (now() - timedelta(hours=WINDOW_HOURS)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expansions": "author_id",
        "tweet.fields": "id,text,author_id,created_at,lang,public_metrics,possibly_sensitive,conversation_id",
        "user.fields": "id,name,username,verified,verified_type,public_metrics,description",
    }
    combined: dict[str, Any] = {"data": [], "includes": {"users": []}, "_partial_errors": [], "_pagination_truncated": False}
    pagination_token: str | None = None
    for _ in range(MAX_PAGES_PER_CATEGORY):
        params = {**base_params, **({"next_token": pagination_token} if pagination_token else {})}
        url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "mstr-btc-bottom-report-x-intelligence/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"X API HTTP {error.code}: {body}") from error
        if payload.get("errors") and not payload.get("data"):
            raise RuntimeError(f"X API response errors: {payload['errors']}")
        combined["data"].extend(payload.get("data", []))
        combined["includes"]["users"].extend(payload.get("includes", {}).get("users", []))
        combined["_partial_errors"].extend(payload.get("errors", []))
        pagination_token = payload.get("meta", {}).get("next_token")
        if not pagination_token:
            break
    if pagination_token:
        combined["_pagination_truncated"] = True
    combined["includes"]["users"] = list({str(user.get("id")): user for user in combined["includes"]["users"]}.values())
    return combined


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def term_matches(text: str, term: str) -> bool:
    normalized_text = re.sub(r"[-_]", " ", text.lower())
    normalized_term = re.sub(r"[-_]", " ", term.lower()).strip()
    escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
    plural = "" if normalized_term.endswith("s") else "s?"
    return re.search(rf"(?<![a-z0-9]){escaped}{plural}(?![a-z0-9])", normalized_text) is not None


def has_any_term(text: str, terms: list[str]) -> bool:
    return any(term_matches(text, term) for term in terms)


def why_it_matters(category_id: str, text: str) -> str:
    if category_id == "ai-application-monetization":
        if has_any_term(text, ["pricing", "revenue", "paid", "subscription", "enterprise", "customer"]):
            return "這是付費意願或企業採用證據；仍需區分宣布、簽約與已認列收入。"
        return "這是產品分發與採用線索；發布功能不等於已形成可持續變現。"
    if category_id == "engineering-model-progress":
        if has_any_term(text, ["benchmark", "eval", "score", "sota"]):
            return "先檢查評測集、基準線與可重現性，避免只看單一榜單標題。"
        if has_any_term(text, ["inference", "latency", "throughput", "training", "cost"]):
            return "真正工程價值在成本、延遲、吞吐與穩定性，不只看模型尺寸。"
        return "這是模型或工具鏈進展；需等待公開方法、權重或獨立重現再提高信心。"
    if has_any_term(text, ["sec", "regulation", "regulatory", "fed", "rate"]):
        return "屬政策與流動性事件雷達；必須回查官方文件，不能只憑貼文進交易閘門。"
    if has_any_term(text, ["etf", "flow", "inflow", "outflow"]):
        return "屬機構資金流線索；需與正式流量資料及價格成交交叉驗證。"
    if has_any_term(text, ["earnings", "revenue", "guidance"]):
        return "屬公司基本面事件；應回查財報、電話會議與監管文件。"
    return "屬市場敘事與事件雷達；只作研究背景，不直接放行 Crypto 或美股交易。"


def relevance_score(category: dict[str, Any], text: str) -> float:
    hits = sum(1 for term in category["relevance_terms"] if term_matches(text, term))
    return min(1.0, hits / 4)


def engagement_score(metrics: dict[str, Any]) -> float:
    weighted = (
        float(metrics.get("like_count") or 0)
        + 2 * float(metrics.get("retweet_count") or 0)
        + 1.5 * float(metrics.get("quote_count") or 0)
        + 0.5 * float(metrics.get("reply_count") or 0)
    )
    return min(1.0, math.log1p(weighted) / math.log(10_001))


def normalize_items(category: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    users = {str(item.get("id")): item for item in payload.get("includes", {}).get("users", [])}
    items: list[dict[str, Any]] = []
    generated = now()
    for post in payload.get("data", []):
        author = users.get(str(post.get("author_id")), {})
        username = str(author.get("username") or "").lower()
        account = category["accounts"].get(username)
        created = parse_time(post.get("created_at"))
        text = compact_text(post.get("text"))
        if not account or not created or not text or post.get("possibly_sensitive") is True:
            continue
        relevance = relevance_score(category, text)
        if relevance <= 0:
            continue
        age_hours = max(0.0, (generated - created).total_seconds() / 3600)
        recency = max(0.0, 1 - age_hours / WINDOW_HOURS)
        metrics = post.get("public_metrics") or {}
        engagement = engagement_score(metrics)
        source_tier, source_weight = account
        score = round(100 * (0.40 * source_weight + 0.30 * relevance + 0.20 * recency + 0.10 * engagement), 1)
        post_id = str(post.get("id"))
        items.append({
            "id": post_id,
            "url": f"https://x.com/{username}/status/{post_id}",
            "created_at": created.replace(microsecond=0).isoformat(),
            "author": str(author.get("name") or username),
            "username": username,
            "verified": bool(author.get("verified")),
            "verified_type": author.get("verified_type"),
            "source_tier": source_tier,
            "language": post.get("lang"),
            "text": text,
            "why_it_matters": why_it_matters(category["id"], text),
            "public_metrics": {
                "likes": int(metrics.get("like_count") or 0),
                "reposts": int(metrics.get("retweet_count") or 0),
                "quotes": int(metrics.get("quote_count") or 0),
                "replies": int(metrics.get("reply_count") or 0),
            },
            "ranking_score_0_100": score,
            "ranking_components_0_100": {
                "source_quality": round(100 * source_weight, 1),
                "keyword_relevance": round(100 * relevance, 1),
                "recency": round(100 * recency, 1),
                "engagement": round(100 * engagement, 1),
            },
            "decision_use": "context_only_not_execution_gate",
        })
    return sorted(items, key=lambda item: (item["ranking_score_0_100"], item["created_at"]), reverse=True)


def category_output(category: dict[str, Any], items: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "id": category["id"],
        "title": category["title"],
        "purpose": category["purpose"],
        "status": status,
        "source_accounts": sorted(category["accounts"]),
        "items": items[:MAX_ITEMS_PER_CATEGORY],
    }


def update_history(output: dict[str, Any]) -> dict[str, Any]:
    history = read_json(HISTORY_PATH, {"schema": 1, "items": []})
    indexed = {str(item.get("id")): item for item in history.get("items", []) if item.get("id")}
    for category in output.get("categories", []):
        for item in category.get("items", []):
            indexed[item["id"]] = {**item, "category_id": category["id"], "category_title": category["title"]}
    cutoff = now() - timedelta(days=180)
    items = [item for item in indexed.values() if (parse_time(item.get("created_at")) or cutoff) >= cutoff]
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    successful_fetch = output.get("quality", {}).get("authentication") == "app_only_bearer_secret" and output.get("quality", {}).get("status") in {"pass", "degraded"}
    updated_at = output["generated_at"] if successful_fetch else history.get("updated_at")
    return {
        "schema": 1,
        "updated_at": updated_at,
        "last_attempt_at": output["generated_at"],
        "last_successful_fetch_at": output["generated_at"] if successful_fetch else history.get("last_successful_fetch_at"),
        "quality": {"status": output.get("quality", {}).get("status")},
        "items": items[:2000],
    }


def main() -> int:
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    generated_at = now_iso()
    categories: list[dict[str, Any]] = []
    failures: list[str] = []
    degradations: list[str] = []
    if not token:
        categories = [category_output(category, [], "unconfigured") for category in CATEGORIES]
        status = "unconfigured"
        degradations.append("GitHub Secret X_BEARER_TOKEN 尚未設定；本頁不沿用舊 X 貼文。")
    else:
        candidates_by_category: dict[str, list[dict[str, Any]]] = {}
        failed_categories: dict[str, str] = {}
        for category in CATEGORIES:
            try:
                payload = request_recent_search(token, category)
                if payload.get("_partial_errors"):
                    degradations.append(f"{category['title']}：X API 回傳 {len(payload['_partial_errors'])} 個部分錯誤。")
                if payload.get("_pagination_truncated"):
                    degradations.append(f"{category['title']}：已抓滿 {MAX_PAGES_PER_CATEGORY} 頁，仍有下一頁；本批次為截斷樣本。")
                candidates_by_category[category["id"]] = normalize_items(category, payload)
            except Exception as error:
                failed_categories[category["id"]] = str(error)
                failures.append(f"{category['title']}：{error}")

        selected: dict[str, list[dict[str, Any]]] = {category["id"]: [] for category in CATEGORIES}
        assigned_ids: set[str] = set()
        ranked_candidates = sorted(
            (
                (category["id"], item)
                for category in CATEGORIES
                for item in candidates_by_category.get(category["id"], [])
            ),
            key=lambda candidate: (
                float(candidate[1]["ranking_score_0_100"]),
                candidate[1]["created_at"],
                candidate[1]["id"],
                candidate[0],
            ),
            reverse=True,
        )
        for category_id, item in ranked_candidates:
            if item["id"] in assigned_ids or len(selected[category_id]) >= MAX_ITEMS_PER_CATEGORY:
                continue
            selected[category_id].append(item)
            assigned_ids.add(item["id"])

        for category in CATEGORIES:
            if category["id"] in failed_categories:
                categories.append(category_output(category, [], "fail"))
            else:
                items = selected[category["id"]]
                category_status = "pass" if len(items) >= 3 else "degraded"
                if len(items) < 3:
                    degradations.append(f"{category['title']}：48 小時內只有 {len(items)} 則通過策展條件的貼文。")
                categories.append(category_output(category, items, category_status))
        status = "fail" if failures else "degraded" if degradations else "pass"
    output = {
        "schema": 1,
        "generated_at": generated_at,
        "window_hours": WINDOW_HOURS,
        "quality": {
            "status": status,
            "api_provider": "X API",
            "endpoint": ENDPOINT,
            "authentication": "app_only_bearer_secret" if token else "missing_secret",
            "execution_gate_eligible": False,
            "failures": failures,
            "degradations": degradations,
            "method": "curated accounts + bounded pagination + keyword relevance + recency + engagement; reposts excluded",
        },
        "summary": {
            "categories": len(categories),
            "posts": sum(len(category["items"]) for category in categories),
            "unique_sources": len({item["username"] for category in categories for item in category["items"]}),
        },
        "categories": categories,
    }
    write_json(OUTPUT_PATH, output)
    write_json(HISTORY_PATH, update_history(output))
    print(json.dumps({"output": str(OUTPUT_PATH), "status": status, **output["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
