"""
preprocess.py — 论文预处理：正则提取 metadata → LLM 修正 + 学科分类

用法:
    python preprocess.py                     # 处理所有 MD
    python preprocess.py --source papers/subdir  # 处理该 PDF 来源对应的 MD
    python preprocess.py --only subdir/paper  # 仅处理 PDF_DIR 下的相对路径，可重复
    python preprocess.py --force              # 强制重处理
    python preprocess.py --reset-failed       # 重试失败文件
"""

import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from utils import (artifact_path_for_source_key, call_llm, cli_values,
                   compact_sha256_text, generate_doc_id, load_config,
                   normalize_source_key, parse_md_metadata,
                   resolve_identity_source_root, resolve_pipeline_paths,
                   source_key_for_path)
from schema import LLMDisciplineResponse
from prompts import LEVEL1_DISCIPLINES, build_discipline_prompt

BASE_DIR = Path(__file__).parent


def _validated_discipline_level(value: dict, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 缺失或格式错误")
    level1 = str(value.get("level1") or "").strip()
    if level1 not in LEVEL1_DISCIPLINES:
        raise ValueError(f"{field_name}.level1 不在定义列表中: {level1 or '<empty>'}")
    return {
        "level1": level1,
        "level2": str(value.get("level2") or "").strip() or None,
        "level3": str(value.get("level3") or "").strip() or None,
    }


def _validated_disciplines(discipline: dict) -> tuple[dict, list[dict] | None]:
    primary = _validated_discipline_level(discipline.get("primary_discipline"), "primary_discipline")
    secondary = []
    for index, item in enumerate(discipline.get("secondary_disciplines") or []):
        normalized = _validated_discipline_level(item, f"secondary_disciplines[{index}]")
        if normalized != primary and normalized not in secondary:
            secondary.append(normalized)
    return primary, secondary or None


def _normalized_doi(value) -> str | None:
    doi = str(value or "").strip()
    if not doi:
        return None
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    doi = doi.rstrip(".,;)")
    return doi if doi.lower().startswith("10.") else None


def process_one(md_path: Path, client: OpenAI, model: str, cfg: dict, force: bool,
                identity: dict | None = None, source_key: str | None = None) -> tuple[bool, dict]:
    """处理单篇论文，返回 (had_new_output, token_usage)"""
    md_dir = BASE_DIR / cfg["markdown_dir"]
    json_dir = BASE_DIR / cfg["json_output_dir"]
    json_dir.mkdir(exist_ok=True)

    try:
        rel = md_path.relative_to(md_dir)
    except ValueError:
        rel = Path(md_path.stem).with_suffix(".json")
    out_path = json_dir / rel.with_suffix(".json")

    if out_path.exists() and not force:
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("metadata"):
            print(f"[跳过] {md_path.name}")
            return False, {}

    print(f"[处理] {source_key or md_path.name}")
    raw_text = md_path.read_text(encoding="utf-8")
    identity = identity or {}
    source_fingerprint = str(identity.get("source_pdf_sha256_96") or "").strip()
    if not source_fingerprint:
        raise ValueError("缺少原始 PDF 指纹，请先运行 convert 再执行 preprocess")
    text_fingerprint = (
        str(identity.get("converted_text_sha256_96") or "").strip()
        or compact_sha256_text(raw_text)
    )
    doc_id = generate_doc_id(source_fingerprint)

    md_meta = parse_md_metadata(raw_text, md_path)
    abstract = md_meta.get("abstract") or ""
    introduction = md_meta.get("introduction") or ""
    # 前部文本用于核验 metadata；抽取到的摘要/引言由 prompt 层在去重后补充。
    paper_head = raw_text[:cfg["metadata_head_chars"]]

    sys_prompt, user_prompt = build_discipline_prompt(
        abstract, introduction, paper_head,
        regex_title=md_meta.get("title", ""),
        regex_year=md_meta.get("year"),
        regex_doi=md_meta.get("doi", ""))

    llm_parsed, _, usage = call_llm(
        client, model, sys_prompt, user_prompt, cfg["temperature"],
        stream=False, max_tokens=cfg["max_output_tokens"], max_retries=cfg["max_retries"])

    if isinstance(llm_parsed.get("secondary_disciplines"), dict):
        llm_parsed["secondary_disciplines"] = [llm_parsed["secondary_disciplines"]]

    try:
        discipline = LLMDisciplineResponse(**llm_parsed).model_dump()
    except Exception:
        discipline = llm_parsed

    title = str(discipline.get("title") or "").strip()
    if not title:
        raise ValueError("LLM 未返回可确认的论文标题")
    primary_discipline, secondary_disciplines = _validated_disciplines(discipline)
    md_meta["title"] = title
    # `None` is intentional here: an unsupported regex match must be cleared.
    md_meta["year"] = discipline.get("year")
    md_meta["doi"] = _normalized_doi(discipline.get("doi"))

    result = {
        "metadata": {
            "doc_id": doc_id,
            "source_pdf_sha256_96": source_fingerprint,
            "converted_text_sha256_96": text_fingerprint,
            "source_file": source_key or md_path.name,
            "title": md_meta.get("title", ""),
            "year": md_meta.get("year"),
            "doi": md_meta.get("doi", ""),
            "abstract": abstract,
            "introduction": introduction,
            "primary_discipline": primary_discipline,
            "secondary_disciplines": secondary_disciplines,
            "keywords": md_meta.get("_keywords_from_paper") or discipline.get("keywords", []),
        },
        # Metadata changes invalidate old extraction; extract will repopulate this field.
        "entries": []
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {md_path.name} → {out_path}")
    return True, usage


def main() -> int:
    cfg = load_config()
    if not cfg["api_key"]:
        print("错误: 未设置 OPENAI_API_KEY")
        return 1

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
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

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(db_source_dir or source_key_root, BASE_DIR / cfg["db_dir"]))
    selected_source_keys = {source_key_for_path(md_path, md_key_root) for md_path in md_files}
    md_paths = {
        source_key_for_path(md_path, md_key_root): md_path
        for md_path in md_files
    }
    db.register_files(sorted(selected_source_keys))
    if reset_failed: db.reset_failed("preprocess", selected_source_keys)
    if force:
        db.reset_stage("preprocess", selected_source_keys)
    source_records = {row["source_key"]: row for row in db.get_records(selected_source_keys)}

    workers = cfg["workers"]
    limit = cfg["process_limit"]

    done_count, done_lock = 0, threading.Lock()
    t0 = time.time()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    tok_lock = threading.Lock()
    print(f"待处理 MD 文件: {len(md_files)}，{workers} 线程\n")

    def _worker():
        nonlocal done_count, total_prompt_tokens, total_completion_tokens
        while True:
            if limit:
                with done_lock:
                    if done_count >= limit: break
            source_key = db.claim_one("preprocess", selected_source_keys)
            if source_key is None: break
            md_path = md_paths.get(source_key) or artifact_path_for_source_key(md_key_root, source_key, ".md")
            if not md_path.exists():
                db.mark_failed(source_key, "preprocess", "MD 文件不存在")
                continue
            try:
                ok, usage = process_one(
                    md_path, client, cfg["model"], cfg, force=True,
                    identity=source_records.get(source_key), source_key=source_key)
                if ok:
                    pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                    db.mark_done(source_key, "preprocess")
                    db.set_preprocess_tokens(source_key, prompt_tokens=pt, completion_tokens=ct)
                    with done_lock: done_count += 1
                    with tok_lock:
                        total_prompt_tokens += pt
                        total_completion_tokens += ct
                else:
                    db.mark_failed(source_key, "preprocess", "process_one 返回 False")
            except Exception as e:
                db.mark_failed(source_key, "preprocess", str(e))
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
    print(f"preprocess 完成: {done_count} 篇, 总耗时 {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"Token: 输入 {total_prompt_tokens:,}  输出 {total_completion_tokens:,}  总计 {total_prompt_tokens+total_completion_tokens:,}")
    print(f"{'='*50}")
    db.print_stats()
    failed = [
        row["source_key"] for row in db.get_records(selected_source_keys)
        if row["preprocess_status"] == "failed"
    ]
    if failed:
        print(f"preprocess 失败文件: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
