#!/usr/bin/env python3
"""Compile active Wiki metadata into a guarded analysis context."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "wiki" / "manifest.json"
DEFAULT_WIKI_DIR = ROOT / "wiki"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "daily" / "knowledge_context.json"
DEFAULT_STALE_AFTER_DAYS = 30

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
LEGACY_METRIC_RE = re.compile(r"(?<![A-Za-z0-9])M([1-7])(?![A-Za-z0-9])", re.IGNORECASE)
QUESTION_MARK_RUN_RE = re.compile(r"\?{3,}")
MOJIBAKE_RE = re.compile(r"(?:\ufffd|Ã.|Â.|â(?:€|™|œ|ž)|(?:嚗|銝|鈭|蝬){2,})")
CONFIDENCE_LEVELS = ("unverified", "low", "medium", "high")
CONFIDENCE_ALIASES = {
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
}
LEGACY_METRIC_IDS = {
    "M1": "common_equity_price_to_nav",
    "M2": "enterprise_value_to_btc_nav",
    "M3": "pref_dilution_flag",
    "M4": "coverage_months",
    "M5": "sale_ratio",
    "M6": "sats_per_share",
    "M7": "strc_discount",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_utf8(path: Path) -> tuple[str, list[str]]:
    raw = path.read_bytes()
    flags: list[str] = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8-sig", errors="replace")
        flags.append("encoding_corruption")
    if has_encoding_corruption(text):
        flags.append("encoding_corruption")
    return text, unique(flags)


def has_encoding_corruption(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if MOJIBAKE_RE.search(value) or QUESTION_MARK_RUN_RE.search(value):
        return True
    question_marks = value.count("?")
    return len(value) >= 12 and question_marks >= 3 and question_marks / len(value) >= 0.15


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized, ["missing_frontmatter"]

    end_match = re.search(r"^---\s*$", normalized[4:], flags=re.MULTILINE)
    if not end_match:
        return {}, normalized, ["frontmatter_parse_error"]

    end_start = end_match.start() + 4
    end_finish = end_match.end() + 4
    block = normalized[4:end_start]
    body = normalized[end_finish:].lstrip("\n")
    metadata: dict[str, Any] = {}
    flags: list[str] = []

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            flags.append("frontmatter_parse_error")
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            flags.append("frontmatter_parse_error")
            continue
        if key in metadata:
            flags.append("frontmatter_duplicate_key")
        metadata[key] = parse_frontmatter_value(raw_value.strip())

    return metadata, body, unique(flags)


def parse_frontmatter_value(value: str) -> Any:
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [strip_quotes(item.strip()) for item in inner.split(",")]
    return strip_quotes(value)


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def clean_markdown_summary(value: str, limit: int = 500) -> str | None:
    text = value.strip()
    if not text:
        return None
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda match: match.group(2) or match.group(1), text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[*_~]+", "", text)
    text = re.sub(r"^\s*(?:一句話|摘要)\s*[：:]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def derive_summary(body: str) -> str | None:
    without_fences = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    paragraphs = re.split(r"\n\s*\n", without_fences)
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if not lines:
            continue
        if all(
            line.startswith(("#", "|", ">", "<")) or re.match(r"^[-*+]\s", line)
            for line in lines
        ):
            continue
        prose_lines = [
            line
            for line in lines
            if not line.startswith(("#", "|", "<")) and not re.match(r"^[-*+]\s", line)
        ]
        summary = clean_markdown_summary(" ".join(prose_lines))
        if summary:
            return summary
    return None


def extract_wikilinks(body: str) -> list[str]:
    links: list[str] = []
    for raw_target in WIKILINK_RE.findall(body):
        target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in links:
            links.append(target)
    return links


def normalize_date(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, False
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw).isoformat(), False
    except ValueError:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat(), False
        except ValueError:
            return None, True


def normalize_confidence(value: Any) -> tuple[str, bool]:
    if value in (None, ""):
        return "unverified", False
    normalized = CONFIDENCE_ALIASES.get(str(value).strip().lower())
    if normalized is None:
        return "unverified", True
    return normalized, False


def lower_confidence(confidence: str, steps: int) -> str:
    index = CONFIDENCE_LEVELS.index(confidence)
    return CONFIDENCE_LEVELS[max(0, index - steps)]


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def select_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip() and not has_encoding_corruption(value):
            return value.strip()
    return None


def source_path(relative_path: str) -> str:
    if not relative_path:
        return "wiki/"
    return (Path("wiki") / Path(relative_path)).as_posix()


def compile_page(
    page: dict[str, Any],
    wiki_dir: Path,
    active_slugs: set[str],
    as_of: date,
    stale_after_days: int,
) -> dict[str, Any]:
    flags: list[str] = []
    slug = str(page.get("slug") or "").strip()
    if not slug:
        flags.append("missing_slug")
    relative_path = str(page.get("path") or "").strip()
    body = ""
    frontmatter: dict[str, Any] = {}

    if not relative_path:
        flags.extend(["missing_source_path", "source_missing"])
    else:
        candidate = wiki_dir / relative_path
        try:
            candidate.resolve().relative_to(wiki_dir.resolve())
        except ValueError:
            flags.extend(["unsafe_source_path", "source_missing"])
        else:
            if not candidate.is_file():
                flags.append("source_missing")
            else:
                text, read_flags = read_utf8(candidate)
                flags.extend(read_flags)
                frontmatter, body, frontmatter_flags = parse_frontmatter(text)
                flags.extend(frontmatter_flags)

    metadata_values = [
        page.get("title"),
        page.get("summary"),
        page.get("group"),
        frontmatter.get("title"),
        frontmatter.get("summary"),
    ]
    if any(has_encoding_corruption(value) for value in metadata_values):
        flags.append("encoding_corruption")

    title = select_text(frontmatter.get("title"), page.get("title"))
    if title is None:
        flags.append("missing_title")
        title = str(page.get("slug") or Path(relative_path).stem or "untitled")

    summary_source = "frontmatter"
    summary = select_text(frontmatter.get("summary"))
    if summary is None:
        flags.append("missing_summary")
        summary_source = "manifest"
        summary = select_text(page.get("summary"))
    if summary is None:
        summary_source = "body_derived"
        summary = derive_summary(body)
        if summary is not None:
            flags.append("summary_derived_from_body")
    if summary is None:
        summary_source = "missing"
        flags.append("summary_unavailable")
    else:
        summary = clean_markdown_summary(summary)
        if summary is not None and has_encoding_corruption(summary):
            summary = None
            summary_source = "missing"
            flags.extend(["encoding_corruption", "summary_unavailable"])

    declared_confidence, invalid_confidence = normalize_confidence(
        frontmatter.get("confidence", page.get("confidence"))
    )
    if frontmatter.get("confidence", page.get("confidence")) in (None, ""):
        flags.append("missing_confidence")
    elif invalid_confidence:
        flags.append("invalid_confidence")

    updated, invalid_updated = normalize_date(frontmatter.get("updated", page.get("updated")))
    if frontmatter.get("updated", page.get("updated")) in (None, ""):
        flags.append("missing_updated")
    elif invalid_updated:
        flags.append("invalid_updated")

    last_verified, invalid_last_verified = normalize_date(
        frontmatter.get("last_verified", page.get("last_verified"))
    )
    if frontmatter.get("last_verified", page.get("last_verified")) in (None, ""):
        flags.append("missing_last_verified")
    elif invalid_last_verified:
        flags.append("invalid_last_verified")

    stale_days: int | None = None
    if last_verified is not None:
        age = (as_of - date.fromisoformat(last_verified)).days
        if age < 0:
            flags.append("future_last_verified")
            stale_days = 0
        else:
            stale_days = age
            if age > stale_after_days:
                flags.append("stale")

    wikilinks = extract_wikilinks(body)
    if any(link.casefold() not in active_slugs for link in wikilinks):
        flags.append("unresolved_wikilink")

    legacy_text = "\n".join(value for value in (body, title, summary or "") if value)
    legacy_metric_names = sorted(
        {f"M{match}" for match in LEGACY_METRIC_RE.findall(legacy_text)},
        key=lambda value: int(value[1:]),
    )
    if legacy_metric_names:
        flags.append("legacy_m1_m7_name")

    penalty_flags = {
        "encoding_corruption",
        "frontmatter_parse_error",
        "frontmatter_duplicate_key",
        "future_last_verified",
        "invalid_confidence",
        "invalid_last_verified",
        "invalid_updated",
        "legacy_m1_m7_name",
        "missing_frontmatter",
        "missing_last_verified",
        "missing_slug",
        "missing_summary",
        "missing_title",
        "missing_updated",
        "source_missing",
        "stale",
        "summary_unavailable",
        "unsafe_source_path",
    }
    penalty = sum(1 for flag in set(flags) if flag in penalty_flags)
    confidence = lower_confidence(declared_confidence, penalty)
    if last_verified is None or summary is None or "source_missing" in flags:
        confidence = "unverified"

    analysis_use = "exclude" if confidence == "unverified" else "context_only"
    raw_decision_inputs = frontmatter.get("decision_inputs", [])
    decision_inputs = (
        [str(value).strip() for value in raw_decision_inputs if str(value).strip()]
        if isinstance(raw_decision_inputs, list)
        else []
    )
    if raw_decision_inputs and not decision_inputs:
        flags.append("invalid_decision_inputs")
    return {
        "slug": slug or Path(relative_path).stem,
        "title": title,
        "summary": summary,
        "summary_source": summary_source,
        "declared_confidence": declared_confidence,
        "confidence": confidence,
        "updated": updated,
        "last_verified": last_verified,
        "stale_days": stale_days,
        "wikilinks": wikilinks,
        "source_path": source_path(relative_path),
        "legacy_metric_names": legacy_metric_names,
        "legacy_metric_replacements": {
            name: LEGACY_METRIC_IDS[name] for name in legacy_metric_names
        },
        "quality_flags": sorted(set(flags)),
        "analysis_use": analysis_use,
        "decision_inputs": decision_inputs,
        "requires_independent_verification": True,
    }


def build_knowledge_context(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    wiki_dir: Path = DEFAULT_WIKI_DIR,
    *,
    as_of: date | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc).date()
    global_flags: list[str] = []
    manifest: dict[str, Any] = {}

    if stale_after_days < 0:
        raise ValueError("stale_after_days must be non-negative")
    if not manifest_path.is_file():
        global_flags.append("manifest_missing")
    else:
        manifest_text, manifest_flags = read_utf8(manifest_path)
        global_flags.extend(f"manifest_{flag}" for flag in manifest_flags)
        try:
            loaded = json.loads(manifest_text)
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                global_flags.append("manifest_invalid_shape")
        except json.JSONDecodeError:
            global_flags.append("manifest_invalid_json")

    raw_pages = manifest.get("pages", [])
    if not isinstance(raw_pages, list):
        global_flags.append("manifest_pages_invalid")
        raw_pages = []

    active_pages: list[dict[str, Any]] = []
    known_slugs: set[str] = set()
    seen_paths: set[str] = set()
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            global_flags.append("manifest_page_invalid")
            continue
        slug = str(raw_page.get("slug") or "").strip()
        path = str(raw_page.get("path") or "").strip()
        if slug and slug.casefold() in known_slugs:
            global_flags.append("manifest_duplicate_slug")
        if path and path.casefold() in seen_paths:
            global_flags.append("manifest_duplicate_path")
        known_slugs.add(slug.casefold())
        seen_paths.add(path.casefold())
        if str(raw_page.get("status", "")).strip().lower() == "active":
            active_pages.append(raw_page)

    compiled = [
        compile_page(page, wiki_dir, known_slugs, as_of, stale_after_days)
        for page in active_pages
    ]
    flag_counts = Counter(flag for page in compiled for flag in page["quality_flags"])
    excluded = sum(page["analysis_use"] == "exclude" for page in compiled)
    context_only = len(compiled) - excluded
    status = "ok"
    if global_flags or flag_counts:
        status = "degraded"
    if any(flag in global_flags for flag in ("manifest_missing", "manifest_invalid_json", "manifest_invalid_shape")):
        status = "blocked"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "as_of_date": as_of.isoformat(),
        "source_manifest": manifest_path.as_posix(),
        "stale_after_days": stale_after_days,
        "status": status,
        "usage_policy": {
            "treat_as_facts": False,
            "allowed_use": "hypothesis_and_background_context_only",
            "fact_requirement": "Corroborate with verified daily data or primary-source evidence before factual use.",
        },
        "quality": {
            "active_pages": len(compiled),
            "context_only_pages": context_only,
            "excluded_pages": excluded,
            "global_flags": sorted(set(global_flags)),
            "flag_counts": dict(sorted(flag_counts.items())),
        },
        "pages": compiled,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--wiki-dir", type=Path, default=DEFAULT_WIKI_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--stale-after-days", type=int, default=DEFAULT_STALE_AFTER_DAYS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        context = build_knowledge_context(
            args.manifest,
            args.wiki_dir,
            as_of=args.as_of,
            stale_after_days=args.stale_after_days,
        )
        write_json(args.output, context)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "knowledge_context": str(args.output),
                "status": context["status"],
                "active_pages": context["quality"]["active_pages"],
                "excluded_pages": context["quality"]["excluded_pages"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
