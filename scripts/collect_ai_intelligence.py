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
        "id": "engineering-methods",
        "title": "工程方法",
        "purpose": "追蹤代理工作流、評測、推論、框架、效能與可重現的開源工程做法。",
        "terms": ["agent", "eval", "inference", "framework", "library", "release", "gpu", "performance", "latency", "open source", "workflow", "sdk", "tool"],
    },
    {
        "id": "model-progress",
        "title": "模型進展",
        "purpose": "追蹤模型能力、訓練、推理、權重、研究方法與基準限制。",
        "terms": ["model", "benchmark", "eval", "inference", "training", "reasoning", "research", "weights", "architecture", "multimodal", "pretraining", "post-training"],
    },
]

SOURCES: list[dict[str, Any]] = [
    {"key": "openai-news", "label": "OpenAI News", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://openai.com/news/rss.xml", "tier": "官方公司", "weight": 1.00},
    {"key": "google-ai-blog", "label": "Google AI Blog", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://blog.google/technology/ai/rss/", "tier": "官方公司", "weight": 0.98},
    {"key": "cursor-changelog", "label": "Cursor Changelog", "kind": "official_feed", "category_id": "ai-application-monetization", "url": "https://www.cursor.com/changelog/rss.xml", "allowed_hosts": ["www.cursor.com", "cursor.com"], "tier": "官方產品", "weight": 0.96},
    {"key": "vercel-ai-releases", "label": "Vercel AI SDK Releases", "kind": "github_release", "category_id": "engineering-methods", "url": "https://api.github.com/repos/vercel/ai/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "nvidia-developer", "label": "NVIDIA Developer Blog", "kind": "official_feed", "category_id": "engineering-methods", "url": "https://developer.nvidia.com/blog/feed/", "tier": "官方工程", "weight": 0.97},
    {"key": "vllm-releases", "label": "vLLM Releases", "kind": "github_release", "category_id": "engineering-methods", "url": "https://api.github.com/repos/vllm-project/vllm/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "transformers-releases", "label": "Transformers Releases", "kind": "github_release", "category_id": "engineering-methods", "url": "https://api.github.com/repos/huggingface/transformers/releases?per_page=8", "tier": "官方開源專案", "weight": 0.95},
    {"key": "deepmind-blog", "label": "Google DeepMind Blog", "kind": "official_feed", "category_id": "model-progress", "url": "https://deepmind.google/blog/rss.xml", "tier": "官方研究", "weight": 1.00},
    {"key": "huggingface-blog", "label": "Hugging Face Blog", "kind": "official_feed", "category_id": "model-progress", "url": "https://huggingface.co/blog/feed.xml", "tier": "官方平台", "weight": 0.94},
    {"key": "arxiv-ai", "label": "arXiv cs.AI 最新研究", "kind": "preprint_feed", "category_id": "model-progress", "url": "https://export.arxiv.org/api/query?search_query=cat%3Acs.AI&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending", "allowed_hosts": ["export.arxiv.org", "arxiv.org"], "tier": "未同儕審查研究", "weight": 0.72},
]

EDITORIAL_THEMES: dict[str, list[dict[str, Any]]] = {
    "ai-application-monetization": [
        {
            "id": "workflow-distribution",
            "terms": ["workflow", "slack", "search", "browser", "integration", "app", "tool"],
            "kicker": "AI 應用與變現",
            "headline": "AI 正從獨立目的地，變成既有工作流裡的隱形功能",
            "standfirst": "產品價值不再只看模型多聰明，而要看它能否在不增加切換成本的情況下完成原本就存在的工作。",
            "common_interpretation": "市場容易把更多入口、整合與代理功能解讀成採用加速。",
            "variant_view": "真正稀缺的不是再多一個 AI 入口，而是能被量測、被治理、被持續使用的流程結果。",
            "second_order_effect": "若 AI 被嵌進搜尋、通訊與開發環境，競爭優勢會從單次回答品質，轉向權限、上下文與工作紀錄的累積。",
            "practical_readthrough": "只挑一條高頻工作流測試，固定記錄完成率、人工接手次數、時間與成本；沒有流程改善就不因新功能而遷移。",
            "falsifier": "若四週內仍只有功能發布、沒有可重現的使用頻率、完成率或付費證據，這個整合趨勢仍只是分發敘事。",
        },
        {
            "id": "enterprise-proof",
            "terms": ["enterprise", "customer", "pricing", "business", "revenue", "deploy", "adoption"],
            "kicker": "AI 應用與變現",
            "headline": "企業 AI 的瓶頸不是需求，而是把展示轉成可稽核的生產成果",
            "standfirst": "客戶案例與產品宣布只能證明興趣；能否持續付費，取決於流程成果、治理成本與實際採用深度。",
            "common_interpretation": "大型客戶名稱、員工覆蓋數與新方案常被當成商業化已被驗證。",
            "variant_view": "部署廣度不等於使用深度，節省時間也不等於可認列收入；缺少續約與單位經濟時應把它視為領先訊號。",
            "second_order_effect": "採用擴大後，真正限制毛利與黏著度的可能是人工覆核、資料治理與例外處理，而非模型推理本身。",
            "practical_readthrough": "閱讀客戶案例時拆成四欄：使用者數、每週使用頻率、可重現成果、付費或續約證據；缺哪一欄就標未知。",
            "falsifier": "若後續揭露顯示高續約、使用頻率上升且單位成本下降，部署廣度就可能確實轉成可持續商業化。",
        },
        {
            "id": "agent-reliability",
            "terms": ["agent", "autonomous", "codex", "assistant", "voice", "chat"],
            "kicker": "AI 應用與變現",
            "headline": "代理正在成為新介面，但可靠性會比自治程度更早決定價值",
            "standfirst": "代理能做更多事不代表使用者敢放手；可預覽計畫、限制權限與保留稽核軌跡才是進入正式流程的門票。",
            "common_interpretation": "產品競爭正聚焦在代理可跨工具、跨資料與長時間自主執行。",
            "variant_view": "短期勝負更可能由失敗可見性與人工接管成本決定，而不是誰宣稱的自治程度最高。",
            "second_order_effect": "治理與可觀測性若成為採用門檻，評測市場會從單題能力轉向端到端任務完成率與事故成本。",
            "practical_readthrough": "測代理時保留完整計畫、工具呼叫、失敗點與人工接手時間；只看成功展示會系統性高估價值。",
            "falsifier": "若無監督代理在真實長任務的錯誤率、回復成本與權限事故持續下降，自治程度才可能重新成為主要差異。",
        },
    ],
    "engineering-methods": [
        {
            "id": "inference-economics",
            "terms": ["inference", "throughput", "latency", "gpu", "performance", "quantization", "memory"],
            "kicker": "工程方法",
            "headline": "下一輪 AI 工程優勢，可能來自每一美元交付更多可靠推理",
            "standfirst": "模型能力差距縮小後，吞吐、延遲、記憶體與失敗重試會直接決定產品能否承擔真實流量。",
            "common_interpretation": "更快的硬體與推論框架通常被直接解讀為更低成本與更高產能。",
            "variant_view": "峰值吞吐不是單位經濟；只有把併發、尾延遲、品質損失與維運成本一起算，效能才會變成商業優勢。",
            "second_order_effect": "推論效率提高可能反而釋放更多代理步驟與更長上下文，使總運算需求上升而非下降。",
            "practical_readthrough": "用自己的提示長度與併發做 A/B，至少同時記錄成功率、P95 延遲、每項任務成本與品質回歸。",
            "falsifier": "若新方法只在單一基準提高峰值吞吐，卻沒有真實負載、品質與總成本資料，不應宣稱已改善生產經濟性。",
        },
        {
            "id": "agent-verifiability",
            "terms": ["agent", "eval", "evaluation", "workflow", "benchmark", "reproducible", "observability"],
            "kicker": "工程方法",
            "headline": "代理工程正在從功能競賽，轉向可驗證性競賽",
            "standfirst": "能否重現、定位失敗並量測端到端完成率，會比增加更多工具呼叫更快改善正式環境可靠性。",
            "common_interpretation": "新框架常以更多代理能力、工具與基準分數作為進步證據。",
            "variant_view": "沒有可重播軌跡、失敗分類與固定評測集，功能越多只會讓不可預測面積變大。",
            "second_order_effect": "可觀測性與評測資料會逐漸成為代理系統的護城河，因為它們保存了組織自己的失敗分布。",
            "practical_readthrough": "先建立十個固定真實任務與失敗分類，再比較框架；沒有同一組回歸證據就不升級。",
            "falsifier": "若增加工具與自治步驟後，固定任務完成率上升且人工接手與事故率沒有惡化，功能擴張才算被驗證。",
        },
        {
            "id": "release-velocity-debt",
            "terms": ["release", "sdk", "framework", "library", "version", "breaking", "api"],
            "kicker": "工程方法",
            "headline": "AI 框架更新越快，版本選擇本身越像一項風險管理工作",
            "standfirst": "發布頻率帶來能力，也帶來相容性、行為漂移與維運負債；最新版本不必然是正式環境的最佳版本。",
            "common_interpretation": "密集發布通常被視為生態活力與產品成熟速度。",
            "variant_view": "對正式工作流而言，可重現與可回滾可能比追上最新功能更有價值。",
            "second_order_effect": "穩定介面、版本釘選與遷移工具會成為框架競爭的重要部分，而不只是附屬文件。",
            "practical_readthrough": "把升級分成破壞性變更、效能、品質與安全四張檢查表；隔離 canary 通過後才更新鎖檔。",
            "falsifier": "若新版在固定回歸集全面改善且沒有遷移、回滾與行為漂移成本，追新造成的維運負債才被證偽。",
        },
    ],
    "model-progress": [
        {
            "id": "specialization-efficiency",
            "terms": ["inference", "diffusion", "quantization", "lightweight", "flash", "efficient", "latency"],
            "kicker": "模型進展",
            "headline": "模型競爭不只追求更強，也開始追求更專用、更省、更快落地",
            "standfirst": "輕量模型、低位元推論與專用能力同時出現，顯示產品端正在把平均能力拆成延遲、成本與特定任務可靠性。",
            "common_interpretation": "新模型與新推論方法通常被分別解讀成能力升級或效率改善。",
            "variant_view": "更值得追蹤的是兩者是否合流：模型若按任務專用化，再配合低成本推論，產品可能不必為每個請求支付最高能力溢價。",
            "second_order_effect": "模型路由會從成本工具變成產品架構：快速模型處理大多數工作，專用或高能力模型只接手高風險例外。",
            "practical_readthrough": "把任務分成低風險高頻、專業窄域與高風險例外三層，分別測成功率、延遲與成本，不再只選一個模型包辦全部。",
            "falsifier": "若輕量或專用模型在真實任務需要大量重試與高階模型接管，總成本與延遲沒有下降，這個分層路由論點就不成立。",
        },
        {
            "id": "benchmark-to-reliability",
            "terms": ["benchmark", "eval", "evaluation", "reasoning", "score", "accuracy"],
            "kicker": "模型進展",
            "headline": "榜單上的進步，仍要經過真實任務可靠性這一關",
            "standfirst": "評測分數能指出能力方向，卻不能自動涵蓋資料污染、工具失敗、長任務漂移與你的工作分布。",
            "common_interpretation": "新基準或更高分數常被視為模型能力已全面提升。",
            "variant_view": "真正可用的進步是失敗分布改變，而不只是平均分上升；尤其要看最差案例與重試成本。",
            "second_order_effect": "企業會更依賴自有評測集，公開榜單對採購與模型路由的決定力可能下降。",
            "practical_readthrough": "從研究中只取一個可驗證假說，放進自己的固定任務集；同時保存失敗案例，不用單一平均分做結論。",
            "falsifier": "若公開基準提升能在多個獨立真實任務集穩定重現，且尾端失敗同步下降，榜單才可作為較強代理指標。",
        },
        {
            "id": "open-deployment",
            "terms": ["weights", "open source", "open-source", "model release", "checkpoint", "license"],
            "kicker": "模型進展",
            "headline": "開放模型的競爭焦點，正從能否下載轉向能否治理與部署",
            "standfirst": "權重可得只是起點；授權、硬體需求、微調可重現性與安全邊界才決定它是否形成實際替代。",
            "common_interpretation": "釋出權重容易被直接解讀為能力民主化與成本下降。",
            "variant_view": "若部署與維運成本高於 API 差價，開放權重帶來的是選擇權，而不是立即可用的成本優勢。",
            "second_order_effect": "模型路由與混合部署會比單一模型押注更重要，因為組織能按資料敏感度與任務難度分配模型。",
            "practical_readthrough": "比較時把授權、硬體、延遲、品質、監控與升級工時一起列入總持有成本。",
            "falsifier": "若相同品質下的完整持有成本長期低於託管 API，且治理負擔可控，開放模型就從選擇權變成結構性優勢。",
        },
        {
            "id": "efficient-learning",
            "terms": ["training", "architecture", "pretraining", "post-training", "multimodal", "data", "causal"],
            "kicker": "模型進展",
            "headline": "模型研究正把問題從『更大』改寫成『怎樣學得更有效』",
            "standfirst": "資料設計、訓練方法與推理結構若能提高樣本效率，可能比單純擴大參數更影響下一階段成本與能力。",
            "common_interpretation": "新架構或訓練方法常以更高能力或更低計算量作為主要賣點。",
            "variant_view": "研究價值要看是否跨資料集、跨規模與跨實作重現；單篇預印本只能提供假說，不能提供結論。",
            "second_order_effect": "若效率方法可重現，中型團隊會得到更多客製化空間，模型差異也可能從規模轉向資料與後訓練。",
            "practical_readthrough": "先確認程式碼、資料與消融實驗，再決定是否值得最小重現；缺任一項就維持研究觀察。",
            "falsifier": "若獨立團隊無法重現，或改善只存在於單一設定，效率優勢就不能外推到實際模型開發。",
        },
    ],
}


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


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def item_integrity_payload(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "url",
        "created_at",
        "title",
        "text",
        "source_key",
        "category_id",
        "source_label",
        "source_type",
        "source_tier",
        "why_it_matters",
        "next_action",
        "decision_use",
    )
    return {key: item.get(key) for key in fields}


def with_item_integrity(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["integrity_hash"] = canonical_hash(item_integrity_payload(result))
    return result


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
        normalized.append(with_item_integrity({
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
        }))
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


def editorial_theme(category_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    themes = EDITORIAL_THEMES[category_id]

    def theme_score(theme: dict[str, Any]) -> tuple[int, int, float]:
        item_hits = [
            (item, sum(term_matches(str(item.get("text") or ""), term) for term in theme["terms"]))
            for item in items
        ]
        supporting = [(item, hits) for item, hits in item_hits if hits > 0]
        return (
            len({item["source_key"] for item, _ in supporting}),
            sum(hits for _, hits in supporting),
            sum(float(item["ranking_score_0_100"]) for item, _ in supporting),
        )

    return max(themes, key=theme_score)


def editorial_evidence(items: list[dict[str, Any]], theme: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    ranked = sorted(
        items,
        key=lambda item: (
            sum(term_matches(str(item.get("text") or ""), term) for term in theme["terms"]),
            float(item["ranking_score_0_100"]),
        ),
        reverse=True,
    )
    for item in ranked:
        if not any(term_matches(str(item.get("text") or ""), term) for term in theme["terms"]):
            continue
        if item["source_key"] in used_sources:
            continue
        matched_terms = [term for term in theme["terms"] if term_matches(str(item.get("text") or ""), term)]
        selected.append({
            "item_id": item["id"],
            "title": item["title"],
            "source_key": item["source_key"],
            "source_label": item["source_label"],
            "source_type": item["source_type"],
            "created_at": item["created_at"],
            "url": item["url"],
            "ranking_score_0_100": item["ranking_score_0_100"],
            "source_excerpt": compact_text(item["text"], 360),
            "matched_terms": matched_terms,
            "item_integrity_hash": item["integrity_hash"],
            "relationship": "topic_context_not_endorsement",
        })
        used_sources.add(item["source_key"])
        if len(selected) == 3:
            break
    return selected


def editorial_brief(category: dict[str, Any]) -> dict[str, Any]:
    items = category["items"]
    theme = editorial_theme(category["id"], items)
    evidence = editorial_evidence(items, theme)
    source_count = len({item["source_key"] for item in evidence})
    status = "pass" if source_count >= 2 else "degraded" if source_count == 1 else "fail"
    if len(evidence) >= 2:
        lede = (
            f"今日可回查的共同訊號來自 {evidence[0]['source_label']} 的「{evidence[0]['title']}」，"
            f"以及 {evidence[1]['source_label']} 的「{evidence[1]['title']}」。"
            "它們只證明相關主題同時出現，不代表來源認同本站假說。"
        )
    elif evidence:
        lede = f"今日只有 {evidence[0]['source_label']} 的「{evidence[0]['title']}」命中此主題；不宣稱形成跨來源訊號。"
    else:
        lede = "今日沒有足夠且可追溯的證據形成編輯觀點。"
    return {
        "id": f"brief-{category['id']}",
        "category_id": category["id"],
        "category_title": category["title"],
        "status": status,
        "theme_id": theme["id"],
        "kicker": theme["kicker"],
        "headline": theme["headline"],
        "standfirst": theme["standfirst"],
        "lede": lede,
        "common_interpretation": theme["common_interpretation"],
        "variant_view": theme["variant_view"],
        "second_order_effect": theme["second_order_effect"],
        "practical_readthrough": theme["practical_readthrough"],
        "falsifier": theme["falsifier"],
        "evidence_source_count": source_count,
        "matched_term_count": sum(len(item["matched_terms"]) for item in evidence),
        "evidence": evidence,
        "editorial_scope": "editorial_hypothesis_not_source_claim",
    }


def previous_editorial_run(history: dict[str, Any], current_date: str) -> dict[str, Any] | None:
    candidates = [
        run for run in history.get("editorial_runs", [])
        if isinstance(run, dict) and str(run.get("date") or "") < current_date
    ]
    return max(candidates, key=lambda run: str(run.get("generated_at") or ""), default=None)


def history_evidence_map(brief: dict[str, Any]) -> dict[str, str | None]:
    if isinstance(brief.get("evidence_refs"), list):
        return {
            str(item.get("item_id")): str(item.get("item_integrity_hash") or "") or None
            for item in brief["evidence_refs"]
            if isinstance(item, dict) and item.get("item_id")
        }
    return {str(item): None for item in brief.get("evidence_ids", [])}


def editorial_digest(categories: list[dict[str, Any]], previous_run: dict[str, Any] | None = None) -> dict[str, Any]:
    briefs = [editorial_brief(category) for category in categories]
    previous_map = {
        str(brief.get("category_id")): brief
        for brief in (previous_run or {}).get("briefs", [])
        if isinstance(brief, dict)
    }
    for brief in briefs:
        previous = previous_map.get(brief["category_id"])
        current_refs = {item["item_id"]: item["item_integrity_hash"] for item in brief["evidence"]}
        previous_refs = history_evidence_map(previous or {})
        if previous is None:
            brief["novelty_status"] = "baseline"
            brief["what_changed"] = "首次建立可回查基準；明日起只在主軸或證據真的改變時更新敘事。"
        elif previous.get("theme_id") != brief["theme_id"]:
            brief["novelty_status"] = "changed"
            brief["what_changed"] = f"主軸由「{previous.get('headline') or previous.get('theme_id')}」轉為「{brief['headline']}」。"
        else:
            current_ids = set(current_refs)
            previous_ids = set(previous_refs)
            added_ids = current_ids - previous_ids
            removed_ids = previous_ids - current_ids
            modified_ids = {
                item_id for item_id in current_ids & previous_ids
                if previous_refs[item_id] is not None and previous_refs[item_id] != current_refs[item_id]
            }
            new_evidence = [item for item in brief["evidence"] if item["item_id"] in added_ids]
            if modified_ids:
                brief["novelty_status"] = "revised"
                brief["what_changed"] = f"主軸未變，但 {len(modified_ids)} 則同一來源內容或 metadata 改變；視為修訂，不視為新證據。"
            elif added_ids and removed_ids:
                labels = "、".join(dict.fromkeys(item["source_label"] for item in new_evidence))
                brief["novelty_status"] = "refreshed"
                brief["what_changed"] = f"主軸未變；替換 {len(removed_ids)} 則、加入 {len(added_ids)} 則主題訊號，新增來源為 {labels}。"
            elif added_ids:
                labels = "、".join(dict.fromkeys(item["source_label"] for item in new_evidence))
                brief["novelty_status"] = "reinforced"
                brief["what_changed"] = f"主軸未變；新增 {len(added_ids)} 則主題訊號，來自 {labels}。"
            elif removed_ids:
                brief["novelty_status"] = "weakened"
                brief["what_changed"] = f"主軸未變，但少了 {len(removed_ids)} 則先前主題訊號；證據廣度下降。"
            else:
                brief["novelty_status"] = "unchanged"
                brief["what_changed"] = "主軸與核心證據未變；今日不製造新故事，保留上一個不同日期的判斷。"
    eligible = [brief for brief in briefs if brief["status"] in {"pass", "degraded"}]
    lead = max(
        eligible,
        key=lambda brief: (
            brief["status"] == "pass",
            brief["evidence_source_count"],
            brief["matched_term_count"],
            sum(float(item["ranking_score_0_100"]) for item in brief["evidence"]),
        ),
    ) if eligible else None
    return {
        "status": "pass" if all(brief["status"] == "pass" for brief in briefs) else "degraded" if eligible else "fail",
        "lead_brief_id": lead["id"] if lead else None,
        "briefs": briefs,
        "policy_hash": canonical_hash(EDITORIAL_THEMES),
        "method": "deterministic topic-context synthesis; source excerpts do not endorse editorial hypotheses; every hypothesis includes a falsifier",
    }


def brief_history_payload(brief: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "category_id": brief["category_id"],
        "theme_id": brief["theme_id"],
        "headline": brief["headline"],
        "standfirst": brief["standfirst"],
        "common_interpretation": brief["common_interpretation"],
        "variant_view": brief["variant_view"],
        "second_order_effect": brief["second_order_effect"],
        "practical_readthrough": brief["practical_readthrough"],
        "falsifier": brief["falsifier"],
        "evidence_refs": [
            {"item_id": item["item_id"], "item_integrity_hash": item["item_integrity_hash"]}
            for item in brief["evidence"]
        ],
    }
    return {**payload, "brief_hash": canonical_hash(payload)}


def legacy_brief_history_payload(brief: dict[str, Any], indexed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_refs = []
    item_ids = [item.get("item_id") for item in brief.get("evidence_refs", []) if isinstance(item, dict)]
    if not item_ids:
        item_ids = list(brief.get("evidence_ids", []))
    for item_id in item_ids:
        item = indexed.get(str(item_id))
        if item:
            evidence_refs.append({"item_id": str(item_id), "item_integrity_hash": item["integrity_hash"]})
    payload = {key: value for key, value in brief.items() if key not in {"brief_hash", "evidence_ids", "evidence_refs"}}
    payload.update({"legacy_migrated": True, "evidence_refs": evidence_refs})
    return {**payload, "brief_hash": canonical_hash(payload)}


def editorial_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "run_hash"}


def with_run_hash(run: dict[str, Any]) -> dict[str, Any]:
    result = dict(run)
    result["run_hash"] = canonical_hash(editorial_run_payload(result))
    return result


def update_history(output: dict[str, Any], history: dict[str, Any] | None = None) -> dict[str, Any]:
    history = history or read_json(HISTORY_PATH, {"schema": 2, "integrity_contract": "source_content_v1", "items": [], "editorial_runs": []})
    raw_items = history.get("items", [])
    if not isinstance(raw_items, list) or any(not isinstance(item, dict) or not item.get("id") for item in raw_items):
        raise ValueError("AI 情報歷史 items 含非物件、空白 ID 或不相容資料")
    normalized_history_items = [with_item_integrity(item) for item in raw_items]
    indexed = {(str(item["id"]), str(item["integrity_hash"])): item for item in normalized_history_items}
    for category in output.get("categories", []):
        for item in category.get("items", []):
            normalized = with_item_integrity(item)
            indexed[(str(normalized["id"]), str(normalized["integrity_hash"]))] = normalized
    items = list(indexed.values())
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    if len(items) > 50000:
        raise ValueError("AI 情報歷史超過 50,000 筆；必須先遷移到分片封存，不得靜默裁切")
    latest_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        latest_by_id.setdefault(str(item["id"]), item)
    successful = output.get("quality", {}).get("status") in {"pass", "degraded"}
    raw_runs = history.get("editorial_runs", [])
    if not isinstance(raw_runs, list) or any(not isinstance(run, dict) for run in raw_runs):
        raise ValueError("AI 機構觀點歷史含非物件資料")
    editorial_runs: list[dict[str, Any]] = []
    previous_hash: str | None = None
    revisions: dict[str, int] = {}
    rebase_chain = history.get("integrity_contract") != "source_content_v1"
    for legacy in sorted(raw_runs, key=lambda run: str(run.get("generated_at") or "")):
        date = str(legacy.get("date") or str(legacy.get("generated_at") or "")[:10])
        revision = revisions.get(date, 0) + 1
        revisions[date] = revision
        if legacy.get("run_hash") and legacy.get("schema") == 1 and not rebase_chain:
            migrated = dict(legacy)
            if migrated.get("previous_run_hash") != previous_hash or migrated.get("revision") != revision:
                raise ValueError("AI 機構觀點歷史雜湊鏈、日期順序或 revision 已損壞")
            if migrated["run_hash"] != canonical_hash(editorial_run_payload(migrated)):
                raise ValueError("AI 機構觀點歷史 run_hash 無法重算")
        else:
            migrated = with_run_hash({
                "schema": 1,
                "date": date,
                "generated_at": legacy.get("generated_at"),
                "revision": revision,
                "supersedes_generated_at": next((run["generated_at"] for run in reversed(editorial_runs) if run["date"] == date), None),
                "previous_run_hash": previous_hash,
                "lead_brief_id": legacy.get("lead_brief_id"),
                "briefs": [legacy_brief_history_payload(brief, latest_by_id) for brief in legacy.get("briefs", []) if isinstance(brief, dict)],
            })
        editorial_runs.append(migrated)
        previous_hash = migrated["run_hash"]
    if successful:
        date = output["generated_at"][:10]
        same_day = [run for run in editorial_runs if run["date"] == date]
        editorial_runs.append(with_run_hash({
            "schema": 1,
            "date": output["generated_at"][:10],
            "generated_at": output["generated_at"],
            "revision": len(same_day) + 1,
            "supersedes_generated_at": same_day[-1]["generated_at"] if same_day else None,
            "previous_run_hash": previous_hash,
            "lead_brief_id": output["editorial_digest"]["lead_brief_id"],
            "briefs": [brief_history_payload(brief) for brief in output["editorial_digest"]["briefs"]],
        }))
    if len(editorial_runs) > 5000:
        raise ValueError("AI 機構觀點歷史超過 5,000 次 revision；必須先分片封存，不得靜默裁切")
    return {
        "schema": 2,
        "integrity_contract": "source_content_v1",
        "updated_at": output["generated_at"] if successful else history.get("updated_at"),
        "last_attempt_at": output["generated_at"],
        "last_successful_fetch_at": output["generated_at"] if successful else history.get("last_successful_fetch_at"),
        "quality": {"status": output.get("quality", {}).get("status")},
        "items": items,
        "editorial_runs": editorial_runs,
    }


def main() -> int:
    generated = now()
    history = read_json(HISTORY_PATH, {"schema": 2, "integrity_contract": "source_content_v1", "items": [], "editorial_runs": []})
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

    editorial = editorial_digest(categories, previous_editorial_run(history, generated.date().isoformat()))
    if editorial["status"] != "pass":
        weak = [brief["category_title"] for brief in editorial["briefs"] if brief["status"] != "pass"]
        degradations.append(f"機構觀點跨來源證據不足：{'、'.join(weak)}。")

    successful_sources = sum(check["status"] == "pass" for check in checks)
    if any(category["status"] == "fail" for category in categories) or not any(category["items"] for category in categories):
        status = "fail"
    elif failures or degradations or successful_sources < 4:
        status = "degraded"
    else:
        status = "pass"
    generated_at = generated.replace(microsecond=0).isoformat()
    output = {
        "schema": 3,
        "generated_at": generated_at,
        "window_hours": WINDOW_HOURS,
        "quality": {
            "status": status,
            "provider": "official_feeds_github_releases_arxiv",
            "execution_gate_eligible": False,
            "failures": failures if status == "fail" else [],
            "degradations": failures + degradations if status == "degraded" else [],
            "method": "official RSS/Atom + official GitHub releases + arXiv preprints; source diversity cap; deterministic topic-context synthesis with explicit editorial-hypothesis boundary",
        },
        "summary": {
            "categories": len(categories),
            "posts": sum(len(category["items"]) for category in categories),
            "unique_sources": len({item["source_key"] for category in categories for item in category["items"]}),
            "successful_sources": successful_sources,
            "failed_sources": len(checks) - successful_sources,
            "editorial_briefs": len(editorial["briefs"]),
            "editorial_pass": sum(brief["status"] == "pass" for brief in editorial["briefs"]),
        },
        "source_checks": checks,
        "editorial_digest": editorial,
        "daily_actions": daily_actions(categories),
        "categories": categories,
    }
    write_json(OUTPUT_PATH, output)
    write_json(HISTORY_PATH, update_history(output, history))
    print(json.dumps({"output": str(OUTPUT_PATH), "status": status, **output["summary"], "actions": len(output["daily_actions"])}, ensure_ascii=False))
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
