"""
extract.py — 默认两组 anchor 抽取，再从源文档扩展 evidence

用法:
    python extract.py                       # 处理所有 preprocess done 的论文
    python extract.py --source papers/subdir # 处理该 PDF 来源对应的 MD
    python extract.py --only subdir/paper   # 仅处理 PDF_DIR 下的相对路径，可重复
    python extract.py --debug                # 输出 LLM raw 到 debug/
    python extract.py --reset-failed         # 重试失败文件
"""

import json
import hashlib
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from utils import (artifact_path_for_source_key, call_llm, cli_values,
                   generate_doc_id, load_config, normalize_source_key,
                   resolve_identity_source_root, resolve_pipeline_paths,
                   source_key_for_path)
from prompts import build_grouped_extraction_prompts, build_custom_prompt, parse_custom_group_spec
from schema import Entry, PAYLOAD_MODELS

BASE_DIR = Path(__file__).parent


def _result_path(md_path: Path, json_dir: Path, markdown_root: Path) -> Path:
    """按 Markdown 根目录的相对路径定位同一篇论文的 JSON 结果。"""
    try:
        return json_dir / md_path.relative_to(markdown_root).with_suffix(".json")
    except ValueError:
        return json_dir / f"{md_path.stem}.json"


def _debug_prefix(debug_dir: Path, md_path: Path, markdown_root: Path) -> Path:
    """Return a collision-free debug output prefix mirroring Markdown layout."""
    try:
        return debug_dir / md_path.relative_to(markdown_root).with_suffix("")
    except ValueError:
        return debug_dir / md_path.stem


# ─── 类型映射 ──────────────────────────────────────────────────

TYPE_META = {
    "concept":             {"prefix": "c"},
    "relation":            {"prefix": "r"},
    "dataset":             {"prefix": "d"},
    "method":              {"prefix": "m"},
    "experiment":          {"prefix": "x"},
    "performance_result":  {"prefix": "p"},
    "quantitative_result": {"prefix": "qr"},
    "data_specification":  {"prefix": "ds"},
    "conclusion":          {"prefix": "cl"},
    "claim":               {"prefix": "ca"},
    "future_work":         {"prefix": "fw"},
    "limitation":          {"prefix": "lm"},
}
TYPE_FIELDS = {
    "concept": ["term", "normalized", "std_label"],
    "relation": ["head_term", "relation_type", "relation_surface", "tail_term"],
    "dataset": ["name", "modality", "domain"],
    "method": ["name", "method_type"],
    "experiment": ["task", "setup"],
    "quantitative_result": ["quantity", "value", "unit", "context", "result_type"],
    "data_specification": ["spec_type", "description"],
    "performance_result": ["metric", "compared_to"],
}
GROUP_KEYS = {
    "concepts": "concept", "relations": "relation", "datasets": "dataset",
    "methods": "method", "experiments": "experiment", "performances": "performance_result",
    "quantitative_results": "quantitative_result", "data_specifications": "data_specification",
    "conclusions": "conclusion", "claims": "claim", "future_works": "future_work",
    "limitations": "limitation",
}
GROUP_TYPE_MAP = {
    "GA_study_design":          ["concept", "relation", "dataset", "data_specification", "method", "experiment"],
    "GB_results_claims":        ["quantitative_result", "performance_result",
                                  "claim", "conclusion", "limitation", "future_work"],
    "G1_concept_relation":      ["concept", "relation"],
    "GC_concept":                ["concept"],
    "GR_relation":               ["relation"],
    "G2_dataset_spec":           ["dataset", "data_specification"],
    "G3_method_experiment":      ["method", "experiment"],
    "G4_quant_perf":             ["quantitative_result", "performance_result"],
    "G5_insight_outlook":        ["conclusion", "claim", "future_work", "limitation"],
    # 独立类型（EXTRACT_GROUP_SPEC 单类型时使用）
    "GD_dataset":                ["dataset"],
    "GDS_data_specification":    ["data_specification"],
    "GM_method":                 ["method"],
    "GX_experiment":             ["experiment"],
    "GQR_quantitative_result":   ["quantitative_result"],
    "GP_performance_result":     ["performance_result"],
    "GCA_claim":                 ["claim"],
    "GCL_conclusion":            ["conclusion"],
    "GLM_limitation":            ["limitation"],
    "GFW_future_work":           ["future_work"],
}
_SHORT_TO_FULL = {
    "study_design":      "GA_study_design",
    "results_claims":    "GB_results_claims",
    "concept_relation":  "G1_concept_relation",
    "concept":           "GC_concept",
    "relation":          "GR_relation",
    "dataset_spec":      "G2_dataset_spec",
    "method_experiment": "G3_method_experiment",
    "quant_perf":        "G4_quant_perf",
    "insight_outlook":   "G5_insight_outlook",
    # 独立类型快捷名
    "dataset":             "GD_dataset",
    "data_specification":  "GDS_data_specification",
    "method":              "GM_method",
    "experiment":          "GX_experiment",
    "quantitative_result": "GQR_quantitative_result",
    "performance_result":  "GP_performance_result",
    "claim":               "GCA_claim",
    "conclusion":          "GCL_conclusion",
    "limitation":          "GLM_limitation",
    "future_work":         "GFW_future_work",
}


# ─── 条目构建 ──────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"(?m)^<!--\s*PAGE\s+(\d+)\s*-->\s*$")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_CAPTION_RE = re.compile(r"\b(?:table|fig(?:ure)?\.?)\s*\d+", re.IGNORECASE)
_MAX_EVIDENCE_CHARS = 5000
_EVIDENCE_SENTENCES_EACH_SIDE = 5
_RESULTS_FRONT_CHARS = 10000
_MIN_UNIQUE_FRAGMENT_CHARS = 80
_MAX_DATASETS_PER_SHARED_EVIDENCE = 5
_SOURCE_GAP_MARKER = "\n\n<!-- SOURCE WINDOW GAP: middle text omitted for input budget -->\n\n"
_PROTECTED_PERIOD = "\uE000"
_SENTENCE_ABBREVIATION_RE = re.compile(
    r"\b(?:e\.g|i\.e|et\s+al|fig|figs|eq|eqs|sec|secs|tab|table|no|vs|dr|prof)\.",
    re.IGNORECASE,
)


def _strip_markdown_tables(text: str) -> str:
    """从送往普通抽取组的视图中移除 Markdown 表格行，原 Markdown 不变。"""
    return re.sub(r"(?m)^\s*\|.*\|\s*$\n?", "", text)


def _front_tail_results_view(text: str, max_chars: int) -> tuple[str, str]:
    """在固定预算内同时保留摘要/主张前部和结果/结论后部。"""
    if len(text) <= max_chars:
        return text, "full"
    front_chars = min(_RESULTS_FRONT_CHARS, max_chars // 3)
    # `max_chars` counts source content; the short separator is prompt metadata.
    tail_chars = max_chars - front_chars
    if tail_chars <= 0:
        return text[-max_chars:], "tail_only"
    return (
        text[:front_chars].rstrip() + _SOURCE_GAP_MARKER + text[-tail_chars:].lstrip(),
        f"front_{front_chars}_tail_{tail_chars}",
    )


def _normalize_match_text(text: str) -> str:
    """仅消除排版差异，避免将改写文本误认为原文证据。"""
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                        "‐": "-", "‑": "-", "–": "-"}))
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # pymupdf4llm may wrap mathematical glyphs as `[𝑚]` or `[(]`.
    text = re.sub(r"\[([(){}\u0370-\u03ff\U0001d400-\U0001d7ff]+)\]", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    # A rendered index such as `𝜏^2` may originate from pymupdf text `𝜏_[2]`.
    symbol = r"A-Za-z\u0370-\u03ff\U0001d400-\U0001d7ff"
    text = re.sub(rf"(?<=[{symbol}])\[(\d+)\]", r"\1", text)
    text = re.sub(rf"(?<=[{symbol}])\^(\d+)", r"\1", text)
    text = re.sub(r"(?<=\d)\s*-\s*(?=[A-Za-z])", "-", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([\(\[\{])\s+", r"\1", text)
    text = re.sub(r"\s+([,.;:!?%)\]}，。；：！？])", r"\1", text)
    return re.sub(r"(?<=\))\s+(?=\()", "", text)


def _split_blocks(text: str) -> list[dict]:
    blocks = []
    for block in re.split(r"\n\s*\n+", text):
        raw = block.strip()
        normalized = _normalize_match_text(raw)
        if normalized:
            blocks.append({"raw": raw, "normalized": normalized})
    return blocks


def _split_pages(text: str) -> list[dict]:
    """读取页码标记并保存原文块及匹配视图。兼容旧文件（返回空列表）。"""
    matches = list(_PAGE_MARKER_RE.finditer(text))
    pages = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[match.end():end].strip()
        pages.append({
            "page": int(match.group(1)),
            "raw": raw,
            "normalized": _normalize_match_text(raw),
            "blocks": _split_blocks(raw),
        })
    return pages


def _locate_unique_fragment(needle: str, pages: list[dict]) -> tuple[int, int, str] | None:
    """用唯一的长连续字面片段恢复定位，并在审计中区别于完整匹配。"""
    if len(needle) < _MIN_UNIQUE_FRAGMENT_CHARS:
        return None
    sizes = sorted({
        _MIN_UNIQUE_FRAGMENT_CHARS,
        min(120, len(needle)),
        min(160, len(needle)),
    }, reverse=True)
    for size in sizes:
        if size < _MIN_UNIQUE_FRAGMENT_CHARS:
            continue
        step = max(20, size // 3)
        starts = list(range(0, len(needle) - size + 1, step))
        if not starts or starts[-1] != len(needle) - size:
            starts.append(len(needle) - size)
        for start in starts:
            fragment = needle[start:start + size].strip()
            if len(fragment) < _MIN_UNIQUE_FRAGMENT_CHARS:
                continue
            matches = {
                (page_idx, block_idx)
                for page_idx, page in enumerate(pages)
                for block_idx, block in enumerate(page["blocks"])
                if fragment in block["normalized"]
            }
            if len(matches) == 1:
                page_idx, block_idx = next(iter(matches))
                return page_idx, block_idx, "unique_fragment"
    return None


def _locate_anchor(anchor_text: str, pages: list[dict]) -> tuple[int, int, str] | None:
    needle = _normalize_match_text(anchor_text)
    if not needle or not pages:
        return None
    for page_idx, page in enumerate(pages):
        blocks = page["blocks"]
        for block_idx, block in enumerate(blocks):
            if needle in block["normalized"]:
                return page_idx, block_idx, "exact"
        for span in range(2, min(5, len(blocks) + 1)):
            for block_idx in range(len(blocks) - span + 1):
                joined = " ".join(block["normalized"] for block in blocks[block_idx:block_idx + span])
                if needle in joined:
                    return page_idx, block_idx, "exact"
        if needle in page["normalized"]:
            prefix = needle[:min(80, len(needle))]
            for block_idx, block in enumerate(blocks):
                if prefix in block["normalized"]:
                    return page_idx, block_idx, "exact"
    return _locate_unique_fragment(needle, pages)


def _locate_evidence_page(anchor_text: str, pages: list[dict]) -> int | None:
    located = _locate_anchor(anchor_text, pages)
    return pages[located[0]]["page"] if located else None


def _is_table_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and sum(bool(_TABLE_LINE_RE.match(line)) for line in lines) >= 2


def _compact_table_block(block: str, anchor_text: str) -> str:
    """保留表头和命中数据行，避免将整张大表复制到每条 evidence。"""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    table_lines = [line for line in lines if _TABLE_LINE_RE.match(line)]
    if not table_lines:
        return block.strip()
    needle = _normalize_match_text(anchor_text)
    matched = [line for line in table_lines if needle and needle in _normalize_match_text(line)]
    separator_idx = next((i for i, line in enumerate(table_lines) if _TABLE_SEPARATOR_RE.match(line)), None)
    header = table_lines[:separator_idx + 1] if separator_idx is not None else table_lines[:1]
    if not matched:
        return "\n".join(table_lines)[:_MAX_EVIDENCE_CHARS]
    selected = header + [line for line in matched if line not in header]
    return "\n".join(selected)[:_MAX_EVIDENCE_CHARS]


def _split_sentences(text: str) -> list[str]:
    """Split extracted prose conservatively while protecting common scholarly periods."""
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    protected = _SENTENCE_ABBREVIATION_RE.sub(
        lambda match: match.group(0).replace(".", _PROTECTED_PERIOD), compact)
    protected = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0).replace(".", _PROTECTED_PERIOD),
        protected,
    )
    protected = re.sub(r"(?<=\d)\.(?=\d)", _PROTECTED_PERIOD, protected)
    sentences = re.split(r"(?<=[.!?。！？])\s+", protected)
    return [
        sentence.replace(_PROTECTED_PERIOD, ".").strip()
        for sentence in sentences
        if sentence.strip()
    ]


def _text_sentence_items(pages: list[dict]) -> list[dict]:
    items = []
    for page_idx, page in enumerate(pages):
        for block_idx, block in enumerate(page["blocks"]):
            if _is_table_block(block["raw"]):
                continue
            for sentence in _split_sentences(block["raw"]):
                items.append({"page_idx": page_idx, "block_idx": block_idx, "raw": sentence})
    return items


def _window_containing_needle(needle: str, items: list[dict],
                              candidate_indices: list[int]) -> tuple[int, int] | None:
    max_span = min(12, len(candidate_indices))
    for span in range(1, max_span + 1):
        for start in range(len(candidate_indices) - span + 1):
            indices = candidate_indices[start:start + span]
            window = " ".join(items[index]["raw"] for index in indices)
            if needle in _normalize_match_text(window):
                return indices[0], indices[-1]
    return None


def _locate_anchor_sentence_span(anchor_text: str, pages: list[dict],
                                 located: tuple[int, int, str],
                                 items: list[dict]) -> tuple[int, int] | None:
    page_idx, block_idx = located[:2]
    page_indices = [index for index, item in enumerate(items) if item["page_idx"] == page_idx]
    needle = _normalize_match_text(anchor_text)
    span = _window_containing_needle(needle, items, page_indices)
    if span:
        return span

    if located[2] == "unique_fragment":
        for size in (160, 120, _MIN_UNIQUE_FRAGMENT_CHARS):
            if len(needle) < size:
                continue
            step = max(20, size // 3)
            starts = list(range(0, len(needle) - size + 1, step))
            if starts[-1] != len(needle) - size:
                starts.append(len(needle) - size)
            for start in starts:
                span = _window_containing_needle(
                    needle[start:start + size], items, page_indices)
                if span:
                    return span

    block_indices = [
        index for index, item in enumerate(items)
        if item["page_idx"] == page_idx and item["block_idx"] == block_idx
    ]
    return (block_indices[0], block_indices[0]) if block_indices else None


def _expand_evidence(anchor_text: str, pages: list[dict], located: tuple[int, int, str]) -> str:
    page_idx, block_idx = located[:2]
    blocks = pages[page_idx]["blocks"]
    anchor_block = blocks[block_idx]["raw"]
    table_idx = block_idx if _is_table_block(anchor_block) else None
    if table_idx is None and block_idx + 1 < len(blocks) and _CAPTION_RE.search(anchor_block):
        if _is_table_block(blocks[block_idx + 1]["raw"]):
            table_idx = block_idx + 1

    if table_idx is not None:
        parts = []
        if table_idx > 0 and _CAPTION_RE.search(blocks[table_idx - 1]["raw"]):
            parts.append(blocks[table_idx - 1]["raw"])
        parts.append(_compact_table_block(blocks[table_idx]["raw"], anchor_text))
        if table_idx + 1 < len(blocks) and not _is_table_block(blocks[table_idx + 1]["raw"]):
            parts.append(blocks[table_idx + 1]["raw"])
        return "\n\n".join(parts)[:_MAX_EVIDENCE_CHARS].strip()

    sentence_items = _text_sentence_items(pages)
    sentence_span = _locate_anchor_sentence_span(anchor_text, pages, located, sentence_items)
    if not sentence_span:
        return anchor_text[:_MAX_EVIDENCE_CHARS].strip()
    anchor_start, anchor_end = sentence_span
    start = max(0, anchor_start - _EVIDENCE_SENTENCES_EACH_SIDE)
    end = min(len(sentence_items), anchor_end + _EVIDENCE_SENTENCES_EACH_SIDE + 1)
    return " ".join(item["raw"] for item in sentence_items[start:end])[:_MAX_EVIDENCE_CHARS].strip()


def _evidence_source_kind(pages: list[dict], located: tuple[int, int, str] | None) -> str:
    if located is None:
        return "unmatched"
    page_idx, block_idx = located[:2]
    blocks = pages[page_idx]["blocks"]
    if _is_table_block(blocks[block_idx]["raw"]):
        return "table"
    if (block_idx + 1 < len(blocks)
            and _CAPTION_RE.search(blocks[block_idx]["raw"])
            and _is_table_block(blocks[block_idx + 1]["raw"])):
        return "table_caption"
    return "text"


def _normalize_evidence(item: dict, pages: list[dict], return_audit: bool = False):
    ev = item.get("evidence", {})
    if not isinstance(ev, dict):
        ev = {}
    anchor_text = str(
        ev.get("anchor_text") or item.get("anchor_text") or
        ev.get("original_text") or item.get("original_text", "") or ""
    )
    located = _locate_anchor(anchor_text, pages)
    original_text = _expand_evidence(anchor_text, pages, located) if located else anchor_text
    evidence = {
        "section": str(ev.get("section", "") or ""),
        "page": pages[located[0]]["page"] if located else None,
        "anchor_text": anchor_text,
        "original_text": original_text,
        "match_method": located[2] if located else "unmatched",
    }
    if not return_audit:
        return evidence
    audit = {
        "anchor_text": anchor_text,
        "matched": located is not None,
        "match_method": located[2] if located else "unmatched",
        "page": evidence["page"],
        "source_kind": _evidence_source_kind(pages, located),
        "anchor_chars": len(anchor_text),
        "evidence_chars": len(original_text),
        "expanded": located is not None and len(original_text) > len(anchor_text),
    }
    return evidence, audit


def _normalize_term(term: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", term.strip().casefold()).strip()


def _raw_payload(item: dict, entry_type: str) -> dict:
    raw = item.get("payload")
    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = {field: item.get(field) for field in TYPE_FIELDS.get(entry_type, []) if field in item}
    if entry_type == "relation":
        payload["head_term"] = str(
            payload.get("head_term") or payload.get("head") or item.get("head") or "").strip()
        payload["tail_term"] = str(
            payload.get("tail_term") or payload.get("tail") or item.get("tail") or "").strip()
        payload.pop("head", None)
        payload.pop("tail", None)
    return payload


def _concept_alias_keys(payload: dict) -> list[str]:
    keys = []
    for alias in (payload.get("term"), payload.get("normalized"), payload.get("std_label")):
        raw_alias = str(alias or "").strip()
        candidates = [raw_alias]
        candidates.extend(
            part.strip() for part in re.findall(r"\(([^()]+)\)", raw_alias)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9+_.-]{1,15}", part.strip())
        )
        for candidate in candidates:
            key = _normalize_term(candidate)
            if key and key not in keys:
                keys.append(key)
    return keys


def _register_concept_aliases(entry: dict, concept_terms: dict[str, str]) -> None:
    cid = entry["entry_id"]
    for key in _concept_alias_keys(entry["payload"]):
        if key not in concept_terms:
            concept_terms[key] = cid
        elif concept_terms[key] != cid:
            concept_terms[key] = ""


def _is_duplicate_concept(payload: dict, concept_terms: dict[str, str]) -> bool:
    return any(concept_terms.get(key) for key in _concept_alias_keys(payload))


def _match_term(term: str, concept_terms: dict) -> str | None:
    """按规范化术语或已登记缩写匹配 concept entry_id，不创建模糊子串链接。"""
    return concept_terms.get(_normalize_term(term)) or None


def _validated_payload(entry_type: str, payload: dict) -> dict | None:
    try:
        return PAYLOAD_MODELS[entry_type].model_validate(payload).model_dump()
    except Exception:
        return None


def _dedup_key(entry_type: str, payload: dict, anchor_text: str) -> str:
    """Create an internal key for same-run deduplication and provisional links."""
    stable_payload = {
        key: value for key, value in payload.items()
        if key not in {"head_entry_id", "tail_entry_id"}
    }
    identity = {
        "type": entry_type,
        "payload": stable_payload,
        "anchor_text": _normalize_match_text(anchor_text),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"_tmp_{TYPE_META[entry_type]['prefix']}_{digest}"


def _validate_entry(entry: dict) -> dict | None:
    try:
        return Entry.model_validate(entry).model_dump()
    except Exception:
        return None


def _make_entry(item: dict, entry_type: str, doc_id: str, counters: dict,
                concept_terms: dict, pages: list[dict],
                evidence_audit: list[dict] | None = None) -> dict | None:
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence", 1.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence, audit = _normalize_evidence(item, pages, return_audit=True)
    raw_payload = _raw_payload(item, entry_type)
    if entry_type == "relation":
        head = str(raw_payload.get("head_term", "") or "").strip()
        tail = str(raw_payload.get("tail_term", "") or "").strip()
        if not head or not tail: return None
    if entry_type == "dataset" and not raw_payload.get("name"): return None
    payload = _validated_payload(entry_type, raw_payload)
    if payload is None:
        print(f"    [跳过无效条目] {entry_type}: payload 不符合 schema")
        return None
    eid = _dedup_key(entry_type, payload, evidence["anchor_text"])
    if entry_type == "relation":
        payload["head_entry_id"] = _match_term(payload["head_term"], concept_terms)
        payload["tail_entry_id"] = _match_term(payload["tail_term"], concept_terms)
    entry = {"entry_id": eid, "type": entry_type, "payload": payload,
             "evidence": evidence, "confidence": confidence}
    validated = _validate_entry(entry)
    if validated and evidence_audit is not None:
        audit.update({"type": entry_type, "entry_id": eid})
        evidence_audit.append(audit)
    elif not validated:
        print(f"    [跳过无效条目] {entry_type} {eid}: 字段不符合 schema")
    return validated


def build_entries(entries_raw: list, doc_id: str, counters: dict, concept_terms: dict,
                  pages: list[dict], evidence_audit: list[dict] | None = None) -> list:
    entries = []
    valid_items = [item for item in entries_raw if isinstance(item, dict)]
    # Relation 端点依赖 concept entry_id；同一组中即使模型先返回 relation，也应先注册概念。
    ordered_items = (
        [item for item in valid_items if item.get("type") == "concept"] +
        [item for item in valid_items if item.get("type") != "concept"]
    )
    for item in ordered_items:
        t = item.get("type")
        if not t or t not in TYPE_META: continue
        if t == "concept":
            payload = _raw_payload(item, "concept")
            term = str(payload.get("term", "") or "").strip()
            if not term: continue
            if _is_duplicate_concept(payload, concept_terms):
                continue
            entry = _make_entry(item, "concept", doc_id, counters, concept_terms, pages, evidence_audit)
            if entry:
                _register_concept_aliases(entry, concept_terms)
                entries.append(entry)
        else:
            entry = _make_entry(item, t, doc_id, counters, concept_terms, pages, evidence_audit)
            if entry: entries.append(entry)
    return entries


def _collapse_enumerated_datasets(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """压缩共享一段已定位枚举证据的 dataset，完整名单仍保存在 evidence 原文中。"""
    groups: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        evidence = entry.get("evidence", {})
        original_text = str(evidence.get("original_text", "") or "")
        if (entry.get("type") == "dataset"
                and evidence.get("page") is not None
                and original_text):
            groups.setdefault(original_text, []).append(index)

    removed_indices: set[int] = set()
    collapsed = []
    for indices in groups.values():
        if len(indices) <= _MAX_DATASETS_PER_SHARED_EVIDENCE:
            continue
        kept = entries[indices[0]]
        removed = [entries[index] for index in indices[1:]]
        removed_indices.update(indices[1:])
        collapsed.append({
            "kept_name": kept.get("payload", {}).get("name", ""),
            "removed_names": [entry.get("payload", {}).get("name", "") for entry in removed],
            "_removed_keys": [entry.get("entry_id", "") for entry in removed],
            "page": kept.get("evidence", {}).get("page"),
        })
    return [entry for index, entry in enumerate(entries) if index not in removed_indices], collapsed


def _deduplicate_entries(entries: list[dict]) -> tuple[list[dict], list[str]]:
    """移除同一知识和同一证据锚点生成的重复条目，保留首次出现顺序。"""
    kept = []
    removed_ids = []
    seen = set()
    for entry in entries:
        entry_id = entry.get("entry_id")
        if entry_id and entry_id in seen:
            removed_ids.append(entry_id)
            continue
        if entry_id:
            seen.add(entry_id)
        kept.append(entry)
    return kept, removed_ids


def _assign_public_entry_ids(entries: list[dict], doc_id: str) -> dict[str, str]:
    """Assign document-local type sequence IDs after all filtering is complete."""
    counters: dict[str, int] = {}
    replacements = {}
    for entry in entries:
        entry_type = entry["type"]
        counters[entry_type] = counters.get(entry_type, 0) + 1
        old_id = entry["entry_id"]
        new_id = f"{doc_id}_{entry_type}_{counters[entry_type]:04d}"
        replacements[old_id] = new_id
        entry["entry_id"] = new_id
    for entry in entries:
        if entry["type"] != "relation":
            continue
        payload = entry.get("payload", {})
        for field in ("head_entry_id", "tail_entry_id"):
            linked_id = payload.get(field)
            payload[field] = replacements.get(linked_id) if linked_id else None
    return replacements


# ─── 单组提取 ──────────────────────────────────────────────────

def extract_one_group(group_name, client, model, sys_prompt, user_prompt, temperature,
                      max_tokens, max_retries, debug) -> tuple[list, str, dict]:
    """单组提取，返回 (entries_list, raw_response, token_usage)。"""
    def _request_once() -> tuple[list, str, dict]:
        kg, raw_resp, usage = call_llm(
            client, model, sys_prompt, user_prompt, temperature,
            stream=False, max_tokens=max_tokens, max_retries=max_retries)
        entries = []
        kg_entries = kg.get("entries")
        if kg_entries:
            entries = list(kg_entries)
        else:
            for key, items in kg.items():
                mapped = GROUP_KEYS.get(key, key)
                if isinstance(items, list):
                    for item in items:
                        item["type"] = mapped
                        entries.append(item)
        return entries, raw_resp, usage

    entries, raw_resp, usage = _request_once()
    if group_name == "GB_results_claims" and not entries:
        print(f"    [{group_name}] 首次返回空条目，自动补抽一次")
        entries, raw_resp, retry_usage = _request_once()
        usage = {
            key: usage.get(key, 0) + retry_usage.get(key, 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        if not entries:
            raise ValueError("结果与论断组连续两次返回空条目")
    return entries, raw_resp, usage


# ─── 单篇论文提取 ──────────────────────────────────────────────

def extract_one(md_path, client, model, max_input_chars, temperature, debug,
                max_output_tokens=16384, max_retries=5, retry_groups: list[str] | None = None,
                extract_groups: str = "", extract_group_parallel: int = 1,
                extract_group_spec: str = ""):
    """对单篇论文执行分组 LLM 抽取"""
    try:
        return _extract_one_impl(md_path, client, model, max_input_chars, temperature, debug,
                                  max_output_tokens, max_retries, retry_groups,
                                  extract_groups, extract_group_parallel, extract_group_spec)
    except Exception:
        import traceback
        print(f"  !!! extract_one 异常: {traceback.format_exc()}")
        raise


def _extract_one_impl(md_path, client, model, max_input_chars, temperature, debug,
                       max_output_tokens=16384, max_retries=5, retry_groups=None,
                       extract_groups="", extract_group_parallel=1, extract_group_spec=""):
    raw_text = md_path.read_text(encoding="utf-8")
    orig_len = len(raw_text)
    debug_info = {"original_length": orig_len, "groups": {}}
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    pages = _split_pages(raw_text)
    table_text, results_input_strategy = _front_tail_results_view(raw_text, max_input_chars)
    text_without_tables = _strip_markdown_tables(raw_text)
    text = text_without_tables[:max_input_chars] if len(text_without_tables) > max_input_chars else text_without_tables
    debug_info["processed_length"] = len(text)
    debug_info["table_processed_length"] = len(table_text)
    debug_info["results_input_strategy"] = results_input_strategy
    debug_info["pages"] = len(pages)

    md_dir = BASE_DIR / os.environ.get("MARKDOWN_DIR", "markdown")
    json_dir = BASE_DIR / os.environ.get("JSON_OUTPUT_DIR", "json_output")
    out_path = _result_path(md_path, json_dir, md_dir)
    if not out_path.exists():
        print(f"  ⚠ 未找到 preprocess 结果: {md_path.stem}")
        return None, False, debug_info, token_usage
    file_data = json.loads(out_path.read_text(encoding="utf-8"))
    meta = file_data.get("metadata", file_data)
    meta.pop("doc_id_basis", None)
    meta.pop("paper_id", None)
    source_fingerprint = str(meta.get("source_pdf_sha256_96") or "").strip()
    if not source_fingerprint:
        raise ValueError("preprocess 结果缺少原始 PDF 指纹，请从 convert 阶段重新运行")
    expected_doc_id = generate_doc_id(source_fingerprint)
    stored_doc_id = str(meta.get("doc_id") or "")
    if stored_doc_id != expected_doc_id:
        raise ValueError("preprocess 结果中的 doc_id 与原始 PDF 指纹不一致，请重新 preprocess")
    doc_id = stored_doc_id

    is_retry = bool(retry_groups)
    if is_retry:
        existing_entries = file_data.get("entries", [])
        if any("entry_id" not in entry or "payload" not in entry for entry in existing_entries):
            raise ValueError("现有 entries 为旧 schema，请使用 --force 对该论文重新 extract")

    all_prompts = build_grouped_extraction_prompts(text, quantitative_text=table_text)

    # ── 自定义分组优先 ──
    custom_type_groups = parse_custom_group_spec(extract_group_spec) if extract_group_spec else []

    if is_retry:
        requested = set(retry_groups)
    elif custom_type_groups:
        requested = set()  # 不用预设筛选，全自定义
    elif extract_groups:
        requested = {_SHORT_TO_FULL[g.strip()] for g in extract_groups.split(",") if g.strip() in _SHORT_TO_FULL}
    else:
        requested = {n for n, _, _ in all_prompts}

    # 构建所有 prompt
    all_group_prompts = []
    # 每篇论文用独立的 group type map（线程安全）
    local_type_map = dict(GROUP_TYPE_MAP)

    def _build_configured_group(group_name: str) -> tuple[str, str, str] | None:
        types = local_type_map.get(group_name, [])
        if not types:
            return None
        prompt_text = table_text if {"quantitative_result", "performance_result"} & set(types) else text
        _, system_prompt, user_prompt = build_custom_prompt(prompt_text, types, 0)
        return group_name, system_prompt, user_prompt

    if custom_type_groups:
        for i, types in enumerate(custom_type_groups):
            prompt_text = table_text if {"quantitative_result", "performance_result"} & set(types) else text
            group_name = f"GX_custom_{i}"
            _, system_prompt, user_prompt = build_custom_prompt(prompt_text, types, i)
            all_group_prompts.append((group_name, system_prompt, user_prompt))
            local_type_map[group_name] = types
    elif is_retry:
        default_prompts = {n: (n, s, u) for n, s, u in all_prompts}
        for group_name in requested:
            prompt = default_prompts.get(group_name) or _build_configured_group(group_name)
            if prompt:
                all_group_prompts.append(prompt)
    elif extract_groups:
        default_prompts = {n: (n, s, u) for n, s, u in all_prompts}
        for group_name in requested:
            prompt = default_prompts.get(group_name) or _build_configured_group(group_name)
            if prompt:
                all_group_prompts.append(prompt)
    else:
        all_group_prompts = all_prompts

    group_results = {}
    failed_groups = []
    t0 = time.time()

    if not all_group_prompts:
        print("  ⚠ 没有需要提取的组")
        return None, False, debug_info, token_usage

    # 组间并发/串行执行
    if extract_group_parallel != 1 and not is_retry and len(all_group_prompts) > 1:
        tok_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=extract_group_parallel or len(all_group_prompts)) as gp:
            futures = {gp.submit(extract_one_group, n, client, model, s, u, temperature,
                                 max_output_tokens, max_retries, debug): n
                       for n, s, u in all_group_prompts}
            for f in as_completed(futures):
                group_name = futures[f]
                try:
                    entries, raw_resp, usage = f.result()
                    group_results[group_name] = entries
                    with tok_lock:
                        token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        token_usage["total_tokens"] += usage.get("total_tokens", 0)
                    n = len(entries)
                    print(f"    [{group_name}] {n} 条  in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)}")
                    if debug: debug_info["groups"][group_name] = {"entries": n, "raw": raw_resp}
                except Exception as e:
                    failed_groups.append(group_name)
                    group_results[group_name] = []
                    print(f"    [{group_name}] ✗ 失败: {e}")
                    if debug: debug_info["groups"][group_name] = {"error": str(e)}
    else:
        for group_name, sys_e, user_e in all_group_prompts:
            prefix = "  [重试]" if is_retry else ""
            try:
                entries, raw_resp, usage = extract_one_group(
                    group_name, client, model, sys_e, user_e, temperature,
                    max_output_tokens, max_retries, debug)
                group_results[group_name] = entries
                token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                token_usage["total_tokens"] += usage.get("total_tokens", 0)
                n = len(entries)
                print(f"    {prefix}[{group_name}] {n} 条  in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)}")
                if debug: debug_info["groups"][group_name] = {"entries": n, "raw": raw_resp}
            except Exception as e:
                failed_groups.append(group_name)
                group_results[group_name] = []
                print(f"    {prefix}[{group_name}] ✗ 失败: {e}")
                if debug: debug_info["groups"][group_name] = {"error": str(e)}

    elapsed = time.time() - t0
    status = f"  {len(failed_groups)} 组失败" if failed_groups else "全部成功"
    print(f"  提取完成: {elapsed:.1f}s  {status}")

    # 合并 entries
    if is_retry:
        types_to_replace = set()
        for gn in retry_groups: types_to_replace.update(local_type_map.get(gn, []))
        kept_entries = [e for e in existing_entries if e["type"] not in types_to_replace]
        counters, concept_terms = {}, {}
        for e in kept_entries:
            t = e["type"]
            counters[t] = counters.get(t, 0) + 1
            if t == "concept" and e.get("entry_id"):
                _register_concept_aliases(e, concept_terms)
    else:
        counters, concept_terms = {}, {}
        kept_entries = []

    new_entries = []
    evidence_audit = []
    if custom_type_groups:
        # concept 组优先（注册到 concept_terms），后续 relation 才能匹配
        concept_groups = []
        other_groups = []
        for i, types in enumerate(custom_type_groups):
            if "concept" in types: concept_groups.append(f"GX_custom_{i}")
            else: other_groups.append(f"GX_custom_{i}")
        _ENTRY_ORDER = concept_groups + other_groups
    else:
        _ENTRY_ORDER = ["GA_study_design", "GB_results_claims",
                        "GC_concept", "GR_relation", "G1_concept_relation",
                        "GD_dataset", "GDS_data_specification", "G2_dataset_spec",
                        "GM_method", "GX_experiment", "G3_method_experiment",
                        "GQR_quantitative_result", "GP_performance_result", "G4_quant_perf",
                        "GCA_claim", "GCL_conclusion", "GLM_limitation", "GFW_future_work",
                        "G5_insight_outlook"]
    for gn in _ENTRY_ORDER:
        if gn in group_results:
            new_entries.extend(build_entries(
                group_results[gn], doc_id, counters, concept_terms, pages, evidence_audit))
    new_entries, duplicate_entry_ids = _deduplicate_entries(new_entries)
    if duplicate_entry_ids:
        debug_info["deduplicated_entries"] = len(duplicate_entry_ids)
        seen_audit_ids = set()
        unique_audit = []
        for record in evidence_audit:
            if record.get("entry_id") in seen_audit_ids:
                continue
            seen_audit_ids.add(record.get("entry_id"))
            unique_audit.append(record)
        evidence_audit = unique_audit
        print(f"  去除完全重复条目: {len(duplicate_entry_ids)} 条")
    new_entries, collapsed_datasets = _collapse_enumerated_datasets(new_entries)
    public_collapsed_datasets = [
        {key: value for key, value in item.items() if key != "_removed_keys"}
        for item in collapsed_datasets
    ]
    if collapsed_datasets:
        removed_ids = {
            entry_id
            for collapsed in collapsed_datasets
            for entry_id in collapsed["_removed_keys"]
        }
        evidence_audit = [
            record for record in evidence_audit
            if record.get("entry_id") not in removed_ids
        ]
        debug_info["collapsed_enumerated_datasets"] = public_collapsed_datasets
        removed_count = sum(len(item["removed_names"]) for item in collapsed_datasets)
        print(f"  合并 dataset 枚举: 移除 {removed_count} 条重复展开项，保留原文 evidence")

    all_entries = kept_entries if is_retry else []
    all_entries.extend(new_entries)
    final_ids = _assign_public_entry_ids(all_entries, doc_id)
    for record in evidence_audit:
        record["entry_id"] = final_ids.get(record.get("entry_id"), record.get("entry_id"))

    file_data["entries"] = all_entries
    file_data["metadata"]["extraction_info"] = {
        "extraction_model": model,
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        "extraction_method": "anchor_grouped",
        "retry_groups": retry_groups if is_retry else None,
        "failed_groups": failed_groups or None,
    }
    result = file_data

    tc = {}
    for e in result["entries"]: tc[e["type"]] = tc.get(e["type"], 0) + 1
    located_evidence = sum(1 for e in result["entries"] if e.get("evidence", {}).get("page") is not None)
    relations = [e for e in result["entries"] if e["type"] == "relation"]
    fully_linked_relations = sum(
        1 for e in relations
        if e.get("payload", {}).get("head_entry_id") and e.get("payload", {}).get("tail_entry_id")
    )
    partially_linked_relations = sum(
        1 for e in relations
        if bool(e.get("payload", {}).get("head_entry_id")) != bool(e.get("payload", {}).get("tail_entry_id"))
    )
    audit_matched = sum(1 for record in evidence_audit if record["matched"])
    audit_kinds = {}
    audit_methods = {}
    for record in evidence_audit:
        kind = record["source_kind"]
        audit_kinds[kind] = audit_kinds.get(kind, 0) + 1
        method = record["match_method"]
        audit_methods[method] = audit_methods.get(method, 0) + 1
    debug_info["evidence_anchor_audit"] = {
        "scope": "entries_created_in_this_extract_run",
        "total": len(evidence_audit),
        "matched": audit_matched,
        "unmatched": len(evidence_audit) - audit_matched,
        "match_rate": round(audit_matched / len(evidence_audit), 4) if evidence_audit else None,
        "source_kind_counts": audit_kinds,
        "match_method_counts": audit_methods,
        "collapsed_enumerated_datasets": public_collapsed_datasets,
        "records": evidence_audit if debug else [],
    }
    debug_info["relation_linking"] = {
        "total": len(relations),
        "fully_linked": fully_linked_relations,
        "partially_linked": partially_linked_relations,
        "unlinked": len(relations) - fully_linked_relations - partially_linked_relations,
    }
    print(f"  合计有效条目: {len(result['entries'])} 条")
    print(f"  证据页码定位: {located_evidence}/{len(result['entries'])} 条")
    if evidence_audit:
        print(
            f"  本轮 anchor 命中: {audit_matched}/{len(evidence_audit)} 条 "
            f"(exact={audit_methods.get('exact', 0)}, "
            f"unique_fragment={audit_methods.get('unique_fragment', 0)})"
        )
    if relations:
        print(
            f"  Relation 完整链接: {fully_linked_relations}/{len(relations)} 条 "
            f"(部分链接 {partially_linked_relations})"
        )
    for t, c in sorted(tc.items()): print(f"    - {t}: {c}")
    print(f"  Token 总计: {token_usage['total_tokens']} "
          f"(入 {token_usage['prompt_tokens']}, 出 {token_usage['completion_tokens']})")

    result, had_failures = result, bool(failed_groups)
    return result, had_failures, debug_info, token_usage


# ─── 主入口 ───────────────────────────────────────────────────

def main() -> int:
    cfg = load_config()
    if not cfg["api_key"]:
        print("错误: 未设置 OPENAI_API_KEY")
        return 1

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
    debug = "--debug" in sys.argv
    force = "--force" in sys.argv
    reset_failed = "--reset-failed" in sys.argv

    source_values = cli_values(sys.argv, "--source")
    source_dir = None
    input_dir = None
    if source_values:
        source_dir, input_dir = resolve_pipeline_paths(BASE_DIR, cfg, source_values[-1])
        if not source_dir.is_dir():
            print(f"目录不存在: {source_dir}")
            return 1
    else:
        direct_dirs = cli_values(sys.argv, "--dir")
        if direct_dirs:
            p = Path(direct_dirs[-1])
            input_dir = p if p.is_absolute() else BASE_DIR / p
            if not input_dir.is_dir():
                print(f"目录不存在: {input_dir}")
                return 1
    db_source_dir = None
    db_source_values = cli_values(sys.argv, "--db-source")
    if db_source_values:
        p = Path(db_source_values[-1])
        db_source_dir = p if p.is_absolute() else BASE_DIR / p

    if input_dir:
        md_dir = input_dir
        md_files = sorted(md_dir.glob("**/*.md"))
    else:
        md_dir = BASE_DIR / cfg["markdown_dir"]
        md_files = sorted(md_dir.glob("**/*.md"))
    configured_pdf_root = (BASE_DIR / cfg["pdf_dir"]).resolve()
    configured_markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    if source_dir is not None:
        source_key_root = resolve_identity_source_root(BASE_DIR, cfg, source_dir)
        md_key_root = configured_markdown_root if source_key_root == configured_pdf_root else md_dir
    elif input_dir is None:
        source_key_root, md_key_root = configured_pdf_root, configured_markdown_root
    else:
        source_key_root, md_key_root = db_source_dir or input_dir, md_dir
    only = {normalize_source_key(value) for value in cli_values(sys.argv, "--only")}
    if only:
        md_files = [md_path for md_path in md_files if source_key_for_path(md_path, md_key_root) in only]
    if not md_files:
        scope = f"（筛选: {', '.join(sorted(only))}）" if only else ""
        print(f"{md_dir} 下没有匹配的 MD 文件{scope}")
        return 1

    json_dir = BASE_DIR / cfg["json_output_dir"]
    json_dir.mkdir(exist_ok=True)
    debug_dir = json_dir / "debug"
    if debug: debug_dir.mkdir(exist_ok=True)

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(db_source_dir or source_key_root, BASE_DIR / cfg["db_dir"]))
    selected_source_keys = {source_key_for_path(md_path, md_key_root) for md_path in md_files}
    md_paths = {
        source_key_for_path(md_path, md_key_root): md_path
        for md_path in md_files
    }
    db.register_files(sorted(selected_source_keys))
    if reset_failed: db.reset_failed("extract", selected_source_keys)
    if force:
        db.reset_stage("extract", selected_source_keys)
    db.print_stats()

    if not force:
        done = {s for s in db.get_processed("extract") if db.was_success(s, "extract")}
        md_files = [md_path for md_path in md_files if source_key_for_path(md_path, md_key_root) not in done]
        if not md_files:
            print("所有文件已完成抽取"); db.print_stats(); return 0
        print(f"待处理: {len(md_files)} 个文件\n")

    if cfg["process_limit"]:
        md_files = md_files[:cfg["process_limit"]]

    md_paths = {
        source_key_for_path(md_path, md_key_root): md_path
        for md_path in md_files
    }
    workers = cfg["workers"]
    mode = "并行" if cfg["extract_group_parallel"] != 1 else "串行"
    groups = cfg["extract_groups"] or "默认"
    print(f"并发: {workers} 线程（论文间并行，论文内 {groups} {mode}）\n")

    t0 = time.time()
    done_count, done_lock = 0, threading.Lock()
    total_pt, total_ct = 0, 0
    tok_lock = threading.Lock()

    def _worker():
        nonlocal done_count, total_pt, total_ct
        while True:
            source_key = db.claim_one("extract", selected_source_keys)
            if source_key is None: break
            retry_groups = db.get_failed_groups(source_key)
            md_path = md_paths.get(source_key) or artifact_path_for_source_key(md_key_root, source_key, ".md")
            if not md_path.exists():
                db.mark_failed(source_key, "extract", "MD 文件不存在")
                continue

            tag = f"[extract] {'Δ' if retry_groups else 'F'}"
            print(f"{tag} {source_key}" + (f" 仅重试 {retry_groups}" if retry_groups else ""))
            try:
                result, had_failures, dbg, tokens = extract_one(
                    md_path, client, cfg["model"], cfg["max_input_chars"],
                    cfg["temperature"], debug, cfg["max_output_tokens"],
                    max_retries=cfg["max_retries"], retry_groups=retry_groups,
                    extract_groups=cfg["extract_groups"],
                    extract_group_parallel=cfg["extract_group_parallel"],
                    extract_group_spec=cfg["extract_group_spec"])
                if result is None:
                    db.mark_failed(source_key, "extract", "preprocess 结果不存在"); continue

                out_path = _result_path(md_path, json_dir, BASE_DIR / cfg["markdown_dir"])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

                if debug and dbg:
                    debug_prefix = _debug_prefix(
                        debug_dir, md_path, BASE_DIR / cfg["markdown_dir"])
                    debug_prefix.parent.mkdir(parents=True, exist_ok=True)
                    for gname, ginfo in dbg.get("groups", {}).items():
                        if ginfo.get("raw"):
                            Path(f"{debug_prefix}_{gname}_raw.json").write_text(ginfo["raw"], encoding="utf-8")
                    audit = dbg.get("evidence_anchor_audit")
                    if audit:
                        Path(f"{debug_prefix}_evidence_anchor_audit.json").write_text(
                            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

                pt = tokens.get("prompt_tokens", 0)
                ct = tokens.get("completion_tokens", 0)
                if had_failures:
                    failed = result["metadata"]["extraction_info"]["failed_groups"]
                    db.set_extract_failed_groups(source_key, failed)
                    db.set_extract_model(source_key, cfg["model"], prompt_tokens=pt, completion_tokens=ct)
                    print(f"  ⚡ {source_key}: {len(result['entries'])} 条  {len(failed)}组失败  token={tokens.get('total_tokens', 0)}")
                else:
                    db.mark_done(source_key, "extract")
                    db.set_extract_model(source_key, cfg["model"], prompt_tokens=pt, completion_tokens=ct)
                    with done_lock: done_count += 1
                    with tok_lock: total_pt += pt; total_ct += ct
                    print(f"  ✓ {source_key}: {len(result['entries'])} 条  token={tokens.get('total_tokens', 0)}")
            except Exception as e:
                db.mark_failed(source_key, "extract", str(e))
                print(f"  ✗ {source_key}: {e}")

    if workers == 1:
        _worker()
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker) for _ in range(workers)]
            for f in as_completed(futures):
                if f.exception(): print(f"工作线程异常: {f.exception()}")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"extract 完成: {done_count} 篇, 总耗时 {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"Token: 输入 {total_pt:,}  输出 {total_ct:,}  总计 {total_pt+total_ct:,}")
    print(f"{'='*50}")
    db.print_stats()
    incomplete = [
        row["source_key"] for row in db.get_records(selected_source_keys)
        if not row.get("skip_reason") and row["extract_status"] != "done"
    ]
    if incomplete:
        print(f"extract 未完成文件: {', '.join(incomplete)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
