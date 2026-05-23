"""
extract.py — 知识抽取（3 组独立：concept → relation → others）

用法:
    python extract.py                       # 处理所有 preprocess done 的论文
    python extract.py --debug                # 输出 LLM raw 到 debug/
    python extract.py --reset-failed         # 重试失败文件
"""

import json
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

from utils import load_config, generate_doc_id, call_llm
from prompts import (build_grouped_extraction_prompts, build_concept_prompt,
                       build_relation_prompt, build_custom_prompt, parse_custom_group_spec)

BASE_DIR = Path(__file__).parent

# ─── 类型映射 ──────────────────────────────────────────────────

TYPE_META = {
    "concept":             {"prefix": "c",  "id_field": "concept_id"},
    "relation":            {"prefix": "r",  "id_field": "relation_id"},
    "dataset":             {"prefix": "d",  "id_field": "dataset_id"},
    "method":              {"prefix": "m",  "id_field": "method_id"},
    "experiment":          {"prefix": "x",  "id_field": "experiment_id"},
    "performance_result":  {"prefix": "p",  "id_field": "perf_id"},
    "quantitative_result": {"prefix": "qr", "id_field": "qr_id"},
    "data_specification":  {"prefix": "ds", "id_field": "ds_id"},
    "conclusion":          {"prefix": "cl", "id_field": "conclusion_id"},
    "claim":               {"prefix": "ca", "id_field": "claim_id"},
    "future_work":         {"prefix": "fw", "id_field": "future_work_id"},
    "limitation":          {"prefix": "lm", "id_field": "limitation_id"},
}
TYPE_FIELDS = {
    "concept": ["term", "normalized", "std_label"],
    "relation": ["head", "relation_type", "relation_surface", "tail", "head_term", "tail_term"],
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

def _normalize_evidence(item: dict) -> dict:
    ev = item.get("evidence", {})
    return ev if ev else {"section": "", "original_text": item.get("original_text", "")}


def _match_term(term: str, concept_terms: dict) -> str | None:
    """匹配 term 到 concept_terms，返回 concept_id 或 None"""
    t = term.strip().lower()
    if not t: return None
    # 1. 大小写不敏感精确匹配
    for c_term, cid in concept_terms.items():
        if c_term.lower() == t: return cid
    # 2. 去括号标准化匹配
    t_norm = re.sub(r'\s*\([^)]*\)', '', t).strip()
    for c_term, cid in concept_terms.items():
        c_norm = re.sub(r'\s*\([^)]*\)', '', c_term.lower()).strip()
        if c_norm == t_norm: return cid
    # 3. 子串匹配
    for c_term, cid in concept_terms.items():
        c = c_term.lower()
        if t in c or c in t: return cid
    # 4. 去括号子串匹配
    for c_term, cid in concept_terms.items():
        c_norm = re.sub(r'\s*\([^)]*\)', '', c_term.lower()).strip()
        if t_norm in c_norm or c_norm in t_norm: return cid
    return None


def _make_entry(item: dict, entry_type: str, doc_id: str, counters: dict, concept_terms: dict) -> dict | None:
    meta = TYPE_META[entry_type]
    counters[entry_type] = counters.get(entry_type, 0) + 1
    eid = f"{doc_id}_{meta['prefix']}{counters[entry_type]}"
    entry = {"type": entry_type, meta["id_field"]: eid,
             "evidence": _normalize_evidence(item),
             "confidence": max(0.0, min(1.0, float(item.get("confidence", 1.0))))}
    for f in TYPE_FIELDS.get(entry_type, []):
        entry[f] = item.get(f)
    if entry_type == "relation":
        head, tail = item.get("head", "").strip(), item.get("tail", "").strip()
        entry["head_term"], entry["tail_term"] = head, tail
        if not head or not tail: return None
        if not concept_terms:
            entry["head"], entry["tail"] = "", ""
        else:
            hid = _match_term(head, concept_terms)
            tid = _match_term(tail, concept_terms)
            if not hid and not tid:
                entry["head"], entry["tail"] = "", ""
            else:
                entry["head"], entry["tail"] = hid or head, tid or tail
    if entry_type == "dataset" and not item.get("name"): return None
    return entry


def build_entries(entries_raw: list, doc_id: str, counters: dict, concept_terms: dict) -> list:
    entries = []
    for item in entries_raw:
        t = item.get("type")
        if not t or t not in TYPE_META: continue
        if t == "concept":
            term = item.get("term", "").strip()
            if not term: continue
            entry = _make_entry(item, "concept", doc_id, counters, concept_terms)
            if entry:
                concept_terms[term] = entry[TYPE_META["concept"]["id_field"]]
                entries.append(entry)
        else:
            entry = _make_entry(item, t, doc_id, counters, concept_terms)
            if entry: entries.append(entry)
    return entries


# ─── 单组提取 ──────────────────────────────────────────────────

def extract_one_group(client, model, sys_prompt, user_prompt, temperature,
                      max_tokens, max_retries, debug) -> tuple[list, str, dict]:
    """单组提取，返回 (entries_list, raw_response, token_usage)。"""
    kg, raw_resp, usage = call_llm(client, model, sys_prompt, user_prompt, temperature,
                                   stream=False, max_tokens=max_tokens, max_retries=max_retries)
    entries = []
    kg_entries = kg.get("entries")
    if kg_entries:
        entries = list(kg_entries)
    else:
        for key, items in kg.items():
            mapped = GROUP_KEYS.get(key, key)
            if isinstance(items, list):
                for item in items: item["type"] = mapped; entries.append(item)
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

    text = raw_text[:max_input_chars] if len(raw_text) > max_input_chars else raw_text
    debug_info["processed_length"] = len(text)

    md_dir = BASE_DIR / os.environ.get("MARKDOWN_DIR", "markdown")
    json_dir = BASE_DIR / os.environ.get("JSON_OUTPUT_DIR", "json_output")
    try:
        out_path = json_dir / md_path.relative_to(md_dir).with_suffix(".json")
    except ValueError:
        out_path = json_dir / f"{md_path.stem}.json"
    if not out_path.exists():
        print(f"  ⚠ 未找到 preprocess 结果: {md_path.stem}")
        return None, False, debug_info, token_usage
    file_data = json.loads(out_path.read_text(encoding="utf-8"))
    meta = file_data.get("metadata", file_data)
    doc_id = meta.get("doc_id") or generate_doc_id(meta.get("title"), md_path.stem)

    is_retry = bool(retry_groups)
    if is_retry:
        existing_entries = file_data.get("entries", [])

    all_prompts = build_grouped_extraction_prompts(text)

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
    if custom_type_groups:
        for i, types in enumerate(custom_type_groups):
            all_group_prompts.append(build_custom_prompt(text, types, i))
            local_type_map[f"GX_custom_{i}"] = types
    elif is_retry:
        all_group_prompts = [(n, s, u) for n, s, u in all_prompts if n in requested]
        for rn in requested & {"GC_concept", "GR_relation"}:
            if rn == "GC_concept": all_group_prompts.append(build_concept_prompt(text))
            elif rn == "GR_relation": all_group_prompts.append(build_relation_prompt(text))
    elif extract_groups:
        all_group_prompts = [(n, s, u) for n, s, u in all_prompts if n in requested]
        for short_name in extract_groups.split(","):
            full_name = _SHORT_TO_FULL.get(short_name.strip(), "")
            if full_name == "GC_concept" and full_name in requested:
                all_group_prompts.append(build_concept_prompt(text))
            elif full_name == "GR_relation" and full_name in requested:
                all_group_prompts.append(build_relation_prompt(text))
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
            futures = {gp.submit(extract_one_group, client, model, s, u, temperature,
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
                    client, model, sys_e, user_e, temperature, max_output_tokens, max_retries, debug)
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
            if t == "concept" and e.get("concept_id"):
                term = e.get("term", "")
                if term: concept_terms[term] = e["concept_id"]
    else:
        counters, concept_terms = {}, {}
        kept_entries = []

    new_entries = []
    if custom_type_groups:
        # concept 组优先（注册到 concept_terms），后续 relation 才能匹配
        concept_groups = []
        other_groups = []
        for i, types in enumerate(custom_type_groups):
            if "concept" in types: concept_groups.append(f"GX_custom_{i}")
            else: other_groups.append(f"GX_custom_{i}")
        _ENTRY_ORDER = concept_groups + other_groups
    else:
        _ENTRY_ORDER = ["GC_concept", "GR_relation", "G1_concept_relation",
                        "GD_dataset", "GDS_data_specification", "G2_dataset_spec",
                        "GM_method", "GX_experiment", "G3_method_experiment",
                        "GQR_quantitative_result", "GP_performance_result", "G4_quant_perf",
                        "GCA_claim", "GCL_conclusion", "GLM_limitation", "GFW_future_work",
                        "G5_insight_outlook"]
    for gn in _ENTRY_ORDER:
        if gn in group_results:
            new_entries.extend(build_entries(group_results[gn], doc_id, counters, concept_terms))

    all_entries = kept_entries if is_retry else []
    all_entries.extend(new_entries)

    file_data["entries"] = all_entries
    file_data["metadata"]["extraction_info"] = {
        "extraction_model": model,
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        "extraction_method": "grouped",
        "retry_groups": retry_groups if is_retry else None,
        "failed_groups": failed_groups or None,
    }
    result = file_data

    tc = {}
    for e in result["entries"]: tc[e["type"]] = tc.get(e["type"], 0) + 1
    print(f"  合计有效条目: {len(result['entries'])} 条")
    for t, c in sorted(tc.items()): print(f"    - {t}: {c}")
    print(f"  Token 总计: {token_usage['total_tokens']} "
          f"(入 {token_usage['prompt_tokens']}, 出 {token_usage['completion_tokens']})")

    result, had_failures = result, bool(failed_groups)
    return result, had_failures, debug_info, token_usage


# ─── 主入口 ───────────────────────────────────────────────────

def main():
    cfg = load_config()
    if not cfg["api_key"]:
        print("错误: 未设置 OPENAI_API_KEY"); return

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
    debug = "--debug" in sys.argv
    force = "--force" in sys.argv
    reset_failed = "--reset-failed" in sys.argv

    # 解析 --dir 参数
    input_dir = None
    if "--dir" in sys.argv:
        idx = sys.argv.index("--dir")
        if idx + 1 < len(sys.argv):
            p = Path(sys.argv[idx + 1])
            input_dir = p if p.is_absolute() else BASE_DIR / p
            if not input_dir.is_dir():
                print(f"目录不存在: {input_dir}"); return

    if input_dir:
        md_dir = input_dir
        md_files = sorted(md_dir.glob("*.md"))
    else:
        md_dir = BASE_DIR / cfg["markdown_dir"]
        md_files = sorted(md_dir.glob("**/*.md"))

    json_dir = BASE_DIR / cfg["json_output_dir"]
    json_dir.mkdir(exist_ok=True)
    debug_dir = json_dir / "debug"
    if debug: debug_dir.mkdir(exist_ok=True)

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(input_dir or (BASE_DIR / cfg["pdf_dir"]), BASE_DIR / cfg["db_dir"]))
    if reset_failed: db.reset_failed("extract")

    db.register_files([p.stem for p in md_files])
    db.print_stats()

    if not force:
        done = {s for s in db.get_processed("extract") if db.was_success(s, "extract")}
        md_files = [p for p in md_files if p.stem not in done]
        if not md_files:
            print("所有文件已完成抽取"); db.print_stats(); return
        print(f"待处理: {len(md_files)} 个文件\n")

    if cfg["process_limit"]:
        md_files = md_files[:cfg["process_limit"]]

    stem_to_path = {p.stem: p for p in md_files}
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
            stem = db.claim_one("extract")
            if stem is None: break
            retry_groups = db.get_failed_groups(stem)
            md_path = stem_to_path.get(stem) or (md_dir / f"{stem}.md")
            if not md_path.exists():
                matches = list(md_dir.glob(f"**/{stem}.md"))
                if not matches:
                    db.mark_failed(stem, "extract", "MD 文件不存在"); continue
                md_path = matches[0]

            tag = f"[extract] {'Δ' if retry_groups else 'F'}"
            print(f"{tag} {stem}" + (f" 仅重试 {retry_groups}" if retry_groups else ""))
            try:
                result, had_failures, dbg, tokens = extract_one(
                    md_path, client, cfg["model"], cfg["max_input_chars"],
                    cfg["temperature"], debug, cfg["max_output_tokens"],
                    max_retries=cfg["max_retries"], retry_groups=retry_groups,
                    extract_groups=cfg["extract_groups"],
                    extract_group_parallel=cfg["extract_group_parallel"],
                    extract_group_spec=cfg["extract_group_spec"])
                if result is None:
                    db.mark_failed(stem, "extract", "preprocess 结果不存在"); continue

                try:
                    out_path = json_dir / md_path.relative_to(md_dir).with_suffix(".json")
                except ValueError:
                    out_path = json_dir / f"{md_path.stem}.json"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

                if debug and dbg:
                    for gname, ginfo in dbg.get("groups", {}).items():
                        if ginfo.get("raw"):
                            (debug_dir / f"{stem}_{gname}_raw.json").write_text(ginfo["raw"], encoding="utf-8")

                pt = tokens.get("prompt_tokens", 0)
                ct = tokens.get("completion_tokens", 0)
                if had_failures:
                    failed = result["metadata"]["extraction_info"]["failed_groups"]
                    db.set_extract_failed_groups(stem, failed)
                    db.set_extract_model(stem, cfg["model"], prompt_tokens=pt, completion_tokens=ct)
                    print(f"  ⚡ {stem}: {len(result['entries'])} 条  {len(failed)}组失败  token={tokens.get('total_tokens', 0)}")
                else:
                    db.mark_done(stem, "extract")
                    db.set_extract_model(stem, cfg["model"], prompt_tokens=pt, completion_tokens=ct)
                    with done_lock: done_count += 1
                    with tok_lock: total_pt += pt; total_ct += ct
                    print(f"  ✓ {stem}: {len(result['entries'])} 条  token={tokens.get('total_tokens', 0)}")
            except Exception as e:
                db.mark_failed(stem, "extract", str(e))
                print(f"  ✗ {stem}: {e}")

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


if __name__ == "__main__":
    main()
