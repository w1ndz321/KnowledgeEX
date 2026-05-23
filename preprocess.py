"""
preprocess.py — 论文预处理：正则提取 metadata → LLM 修正 + 学科分类

用法:
    python preprocess.py                     # 处理所有 MD
    python preprocess.py --force              # 强制重处理
    python preprocess.py --reset-failed       # 重试失败文件
"""

import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from utils import load_config, generate_doc_id, parse_md_metadata, call_llm
from schema import LLMDisciplineResponse
from prompts import build_discipline_prompt

BASE_DIR = Path(__file__).parent


def process_one(md_path: Path, client: OpenAI, model: str, cfg: dict, force: bool) -> tuple[bool, dict]:
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

    print(f"[处理] {md_path.name}")
    raw_text = md_path.read_text(encoding="utf-8")

    md_meta = parse_md_metadata(raw_text, md_path)
    abstract = md_meta.get("abstract") or ""
    introduction = md_meta.get("introduction") or ""
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

    if discipline.get("title"): md_meta["title"] = discipline["title"]
    if discipline.get("year"): md_meta["year"] = discipline["year"]
    if discipline.get("doi"): md_meta["doi"] = discipline["doi"]

    doc_id = generate_doc_id(md_meta.get("title"), md_path.stem)

    existing_entries = []
    if out_path.exists():
        try:
            existing_entries = json.loads(out_path.read_text(encoding="utf-8")).get("entries", [])
        except Exception: pass

    result = {
        "metadata": {
            "doc_id": doc_id,
            "source_file": md_path.name,
            "title": md_meta.get("title", ""),
            "year": md_meta.get("year"),
            "doi": md_meta.get("doi", ""),
            "abstract": abstract,
            "introduction": introduction,
            "primary_discipline": discipline.get("primary_discipline", {}),
            "secondary_disciplines": discipline.get("secondary_disciplines"),
            "keywords": md_meta.get("_keywords_from_paper") or discipline.get("keywords", []),
        },
        "entries": existing_entries
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {md_path.name} → {out_path}")
    return True, usage


def main():
    cfg = load_config()
    if not cfg["api_key"]:
        print("错误: 未设置 OPENAI_API_KEY"); return

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
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
        md_files = sorted(md_dir.glob("*.md"))  # 不递归，仅该目录下的 MD
    else:
        md_dir = BASE_DIR / cfg["markdown_dir"]
        md_files = sorted(md_dir.glob("**/*.md"))
    if not md_files:
        print(f"{md_dir} 下没有 MD 文件"); return

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(input_dir or (BASE_DIR / cfg["pdf_dir"]), BASE_DIR / cfg["db_dir"]))
    if reset_failed: db.reset_failed("preprocess")
    db.register_files([p.stem for p in md_files])

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
            stem = db.claim_one("preprocess")
            if stem is None: break
            md_path = md_dir / f"{stem}.md"
            if not md_path.exists():
                matches = list(md_dir.glob(f"**/{stem}.md"))
                if not matches: db.mark_failed(stem, "preprocess", "MD 文件不存在"); continue
                md_path = matches[0]
            try:
                ok, usage = process_one(md_path, client, cfg["model"], cfg, force=True)
                if ok:
                    pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                    db.mark_done(stem, "preprocess")
                    db.set_preprocess_tokens(stem, prompt_tokens=pt, completion_tokens=ct)
                    with done_lock: done_count += 1
                    with tok_lock:
                        total_prompt_tokens += pt
                        total_completion_tokens += ct
                else:
                    db.mark_failed(stem, "preprocess", "process_one 返回 False")
            except Exception as e:
                db.mark_failed(stem, "preprocess", str(e))
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
    print(f"preprocess 完成: {done_count} 篇, 总耗时 {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"Token: 输入 {total_prompt_tokens:,}  输出 {total_completion_tokens:,}  总计 {total_prompt_tokens+total_completion_tokens:,}")
    print(f"{'='*50}")
    db.print_stats()


if __name__ == "__main__":
    main()
