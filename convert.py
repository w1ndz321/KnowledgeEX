"""
convert.py — PDF 转带页码标记的 Markdown（多进程）

用法:
    python convert.py                         # 转换 PDF_DIR 下所有 PDF
    python convert.py --source papers/subdir  # 指定 PDF 来源目录
    python convert.py --only subdir/paper     # 仅转换 PDF_DIR 下的相对路径，可重复
    python convert.py --force                 # 强制重转
    python convert.py --time                  # 显示耗时
    python convert.py --reset-failed          # 重试失败文件
"""

import os
import re
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from utils import (artifact_path_for_source_key, cli_values, compact_sha256_file,
                   compact_sha256_text, generate_doc_id, load_config,
                   normalize_source_key, resolve_identity_source_root,
                   resolve_pipeline_paths, source_key_for_path)

BASE_DIR = Path(__file__).parent
PAGE_MARKER = "<!-- PAGE {page} -->"


def _to_page_markdown(pdf_path_str: str, backend: str) -> str:
    """转换为单个 Markdown，并在每页内容前写入稳定页码标记。"""
    page_texts = []
    if backend == "pymupdf":
        import fitz
        doc = fitz.open(pdf_path_str)
        page_texts = [str(page.get_text("text")) for page in doc]
        doc.close()
    else:
        import pymupdf4llm
        try:
            chunks = pymupdf4llm.to_markdown(pdf_path_str, page_chunks=True)
            if isinstance(chunks, list):
                page_texts = [
                    str(chunk.get("text", "")) if isinstance(chunk, dict) else str(chunk)
                    for chunk in chunks
                ]
        except TypeError:
            # Older pymupdf4llm versions may not expose page_chunks.
            page_texts = []
        if not page_texts:
            import fitz
            doc = fitz.open(pdf_path_str)
            page_count = len(doc)
            doc.close()
            page_texts = [
                str(pymupdf4llm.to_markdown(pdf_path_str, pages=[page_no]))
                for page_no in range(page_count)
            ]

    return "\n\n".join(
        f"{PAGE_MARKER.format(page=page_no)}\n\n{text}"
        for page_no, text in enumerate(page_texts, 1)
    )


# ─── 单文件转换 worker ─────────────────────────────────────────

def _process_single(args: tuple) -> dict:
    """多进程 worker：转换单个 PDF。"""
    pdf_path_str, md_path_str, force, backend, source_key = args
    md_path = Path(md_path_str)
    if md_path.exists() and not force:
        content = md_path.read_text(encoding="utf-8")
        return {"source_key": source_key, "skipped": True, "char_count": len(content),
                "converted_text_sha256_96": compact_sha256_text(content), "elapsed": 0.0}

    t0 = time.time()
    md_path.parent.mkdir(parents=True, exist_ok=True)

    md_text = _to_page_markdown(pdf_path_str, backend)

    elapsed = time.time() - t0
    md_text = _fix_abstract_order(_clean_md(md_text))
    md_path.write_text(md_text, encoding="utf-8")

    return {"source_key": source_key, "skipped": False, "elapsed": elapsed,
            "char_count": len(md_text),
            "converted_text_sha256_96": compact_sha256_text(md_text)}


def _clean_md(md_text: str) -> str:
    md_text = re.sub(r"\*\*==> picture \[.*?\] intentionally omitted <==\*\*\n?", "", md_text)
    # 截断参考文献
    m = re.search(r"\n#{1,2}\s+\*?\*?\s*(?:References?|Bibliography|REFERENCES?|BIBLIOGRAPHY|参考文献|引用文献)\s*\*?\*?\s*\n", md_text, re.IGNORECASE)
    if not m:
        m = re.search(r"\n\*?\*?\s*(?:References?|Bibliography|REFERENCES?|BIBLIOGRAPHY|参考文献)\s*\*?\*?\s*\n", md_text, re.IGNORECASE)
    if not m:
        m = re.search(r"\n#{1,2}\s+\*?\*?\s*(?:Acknowledg(?:e?)ments?|Author\s+[Cc]ontributions?|Supplementary|Appendices|Data\s+[Aa]vailability)\s*\*?\*?\s*\n", md_text, re.IGNORECASE)
    if not m:
        ref_lines = list(re.finditer(r"^\s*[-–••*\[]\s*\[?\d{1,4}\]?\s", md_text, re.MULTILINE))
        if len(ref_lines) >= 5:
            for i in range(len(ref_lines) - 1, 0, -1):
                if ref_lines[i].start() - ref_lines[i-1].end() > 500:
                    m = ref_lines[i]; break
            if not m: m = ref_lines[-5]
    if m: md_text = md_text[:m.start()]
    return md_text


def _fix_abstract_order(md_text: str) -> str:
    pattern = r"(##\s+\*?\*?Abstract\*?\*?\s*\n)(.*?)(?=\n##|\Z)"
    match = re.search(pattern, md_text, re.DOTALL | re.IGNORECASE)
    if not match: return md_text
    heading, body = match.group(1), match.group(2).strip()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if len(paragraphs) < 2 or not paragraphs[0][0].islower(): return md_text
    paragraphs = paragraphs[1:] + [paragraphs[0]]
    merged = [paragraphs[0]]
    for p in paragraphs[1:]:
        if p[0].islower(): merged[-1] += " " + p
        else: merged.append(p)
    return md_text[:match.start()] + heading + "\n\n".join(merged) + md_text[match.end():]


def _discard_obsolete_outputs(pdf: Path, source_key_root: Path, md_root: Path,
                              cfg: dict, remove_markdown: bool) -> None:
    """Remove generated downstream artifacts once this source is excluded from the corpus."""
    source_key = source_key_for_path(pdf, source_key_root)
    md_path = artifact_path_for_source_key(md_root, source_key, ".md")
    if remove_markdown and md_path.exists():
        md_path.unlink()
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    try:
        json_path = json_root / md_path.resolve().relative_to(markdown_root).with_suffix(".json")
    except ValueError:
        return
    if json_path.exists():
        json_path.unlink()
    debug_dir = json_root / "debug"
    if debug_dir.exists():
        debug_prefix = debug_dir / md_path.resolve().relative_to(markdown_root).with_suffix("")
        for debug_path in debug_prefix.parent.glob(f"{debug_prefix.name}_*.json"):
            debug_path.unlink()


# ─── 主入口 ───────────────────────────────────────────────────

def main() -> int:
    cfg = load_config()
    force = "--force" in sys.argv
    show_time = "--time" in sys.argv
    reset_failed = "--reset-failed" in sys.argv

    source_values = cli_values(sys.argv, "--source") or cli_values(sys.argv, "--dir")
    input_dir, md_dir = resolve_pipeline_paths(BASE_DIR, cfg, source_values[-1] if source_values else None)
    if not input_dir.is_dir():
        print(f"目录不存在: {input_dir}")
        return 1
    source_key_root = resolve_identity_source_root(BASE_DIR, cfg, input_dir)
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    md_root = markdown_root if source_key_root == (BASE_DIR / cfg["pdf_dir"]).resolve() else md_dir
    md_dir.mkdir(parents=True, exist_ok=True)
    only = {normalize_source_key(value) for value in cli_values(sys.argv, "--only")}

    # ZIP 自动解压
    unzip_first = cfg.get("unzip_first", True)
    delete_zip = cfg.get("delete_zip_after", False)
    if unzip_first:
        zips = sorted(input_dir.glob("**/*.zip"))
        if zips:
            print(f"发现 {len(zips)} 个 ZIP，解压中...")
            for zp in zips:
                zip_name = zp.stem
                extract_to = input_dir / zip_name
                if extract_to.exists():
                    existing_pdfs = list(extract_to.glob("**/*.pdf"))
                    if existing_pdfs:
                        print(f"  跳过 {zp.name}（已解压 {len(existing_pdfs)} pdf）")
                        if delete_zip: zp.unlink()
                        continue
                extract_to.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(zp, 'r') as zf:
                        for member in zf.infolist():
                            if member.filename.lower().endswith('.pdf'):
                                zf.extract(member, extract_to)
                except (zipfile.BadZipFile, OSError) as e:
                    print(f"  ✗ {zp.name} 损坏，跳过: {e}")
                    continue
                pdf_count = len(list(extract_to.glob("*.pdf")))
                print(f"  {zp.name} → {pdf_count} pdf")
                if delete_zip: zp.unlink()
            if delete_zip: print("  已删除原始 ZIP")

    pdfs = sorted(input_dir.glob("**/*.pdf"))
    if only:
        pdfs = [pdf for pdf in pdfs if source_key_for_path(pdf, source_key_root) in only]
    if not pdfs:
        scope = f"（筛选: {', '.join(sorted(only))}）" if only else ""
        print(f"{input_dir} 下没有匹配的 PDF 文件{scope}")
        return 1

    workers = cfg["convert_workers"]
    backend = cfg["convert_backend"]

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(source_key_root, BASE_DIR / cfg["db_dir"]))
    selected_source_keys = {source_key_for_path(pdf, source_key_root) for pdf in pdfs}
    db.register_files(sorted(selected_source_keys))
    if reset_failed:
        db.reset_failed("convert", selected_source_keys)
    if force:
        db.reset_stage("convert", selected_source_keys)

    backfill_pdfs = []
    text_backfill_pdfs = []
    # 过滤已完成；旧 DB 中没有 PDF 指纹的完成记录需先回填一次，供新增文件判重。
    if not force:
        done_source_keys = {key for key in db.get_processed("convert") if db.was_success(key, "convert")}
        existing_records = {row["source_key"]: row for row in db.get_records(selected_source_keys)}
        backfill_pdfs = [
            pdf for pdf in pdfs
            if source_key_for_path(pdf, source_key_root) in done_source_keys
            and not existing_records.get(source_key_for_path(pdf, source_key_root), {}).get("source_pdf_sha256_96")
        ]
        text_backfill_pdfs = [
            pdf for pdf in pdfs
            if source_key_for_path(pdf, source_key_root) in done_source_keys
            and not existing_records.get(source_key_for_path(pdf, source_key_root), {}).get("converted_text_sha256_96")
        ]
        pending = [pdf for pdf in pdfs if source_key_for_path(pdf, source_key_root) not in done_source_keys]
        if len(pending) < len(pdfs):
            print(f"跳过 {len(pdfs) - len(pending)} 个已完成文件")
        pdfs = pending

    if cfg["process_limit"]:
        pdfs = pdfs[:cfg["process_limit"]]

    min_md_chars = cfg["min_md_chars"]
    source_duplicate_count = 0
    unique_pdfs = []
    work_source_keys = {source_key_for_path(pdf, source_key_root) for pdf in pdfs}
    backfill_source_keys = {source_key_for_path(pdf, source_key_root) for pdf in backfill_pdfs}
    fingerprint_pdfs = sorted(
        [*backfill_pdfs, *pdfs],
        key=lambda pdf: (source_key_for_path(pdf, source_key_root) not in backfill_source_keys, str(pdf)),
    )
    for pdf in fingerprint_pdfs:
        source_key = source_key_for_path(pdf, source_key_root)
        try:
            source_fingerprint = compact_sha256_file(pdf)
            duplicate_of = db.set_source_pdf_fingerprint(
                source_key, source_fingerprint, generate_doc_id(source_fingerprint))
        except Exception as exc:
            db.mark_failed(source_key, "convert", f"PDF 指纹计算失败: {exc}")
            print(f"✗ {source_key}: PDF 指纹计算失败: {exc}")
            continue
        if duplicate_of:
            detail = f"原始 PDF 指纹与 {duplicate_of} 相同，未执行 Markdown 转换"
            _discard_obsolete_outputs(pdf, source_key_root, md_root, cfg, remove_markdown=True)
            db.skip(
                source_key, "duplicate_pdf", detail,
                duplicate_of=duplicate_of, skip_convert=True,
            )
            if source_key in work_source_keys:
                source_duplicate_count += 1
            print(f"{source_key}  [跳过: duplicate_pdf -> {duplicate_of}]")
            continue
        if source_key in work_source_keys:
            unique_pdfs.append(pdf)
    for pdf in sorted(text_backfill_pdfs):
        source_key = source_key_for_path(pdf, source_key_root)
        if not db.was_success(source_key, "convert"):
            continue
        md_path = artifact_path_for_source_key(md_root, source_key, ".md")
        if not md_path.exists():
            continue
        text_fingerprint = compact_sha256_text(md_path.read_text(encoding="utf-8"))
        duplicate_of = db.set_converted_text_fingerprint(source_key, text_fingerprint)
        if duplicate_of:
            detail = f"历史 Markdown 指纹与 {duplicate_of} 相同，不进入下游阶段"
            _discard_obsolete_outputs(pdf, source_key_root, md_root, cfg, remove_markdown=True)
            db.skip(source_key, "duplicate_markdown", detail, duplicate_of=duplicate_of)
            print(f"{source_key}  [跳过: duplicate_markdown -> {duplicate_of}]")
    pdfs = unique_pdfs
    if not pdfs:
        message = "指纹检查后没有需要执行的 PDF 转换" if work_source_keys else "所有文件已完成转换"
        print(message)
        db.print_stats()
        return 0

    total = len(pdfs)
    print(f"待处理: {total} 个文件，{workers} 进程，后端: {backend}\n")

    t0 = time.time()
    converted, skipped_count = 0, source_duplicate_count
    time_records = []
    task_args = [
        (
            str(pdf),
            str(artifact_path_for_source_key(md_root, source_key_for_path(pdf, source_key_root), ".md")),
            force,
            backend,
            source_key_for_path(pdf, source_key_root),
        )
        for pdf in pdfs
    ]

    def _record_result(i: int, pdf: Path, result: dict):
        nonlocal converted, skipped_count
        source_key = result["source_key"]
        db.mark_done(source_key, "convert")
        if result["char_count"]: db.set_char_count(source_key, result["char_count"])
        if not result["skipped"]:
            converted += 1
            if result["elapsed"]: time_records.append((pdf.name, result["elapsed"]))
        if result["char_count"] < min_md_chars:
            db.set_converted_text_fingerprint(
                source_key, result["converted_text_sha256_96"], check_duplicate=False)
            detail = f"转换文本字符数 {result['char_count']} < {min_md_chars}"
            _discard_obsolete_outputs(pdf, source_key_root, md_root, cfg, remove_markdown=False)
            db.skip(source_key, "insufficient_text", detail)
            print(f"[{i}/{total}] {source_key}  ({result['char_count']} 字符) [跳过: insufficient_text]")
            if not result["skipped"]:
                converted -= 1
            skipped_count += 1
            return
        dup_of = db.set_converted_text_fingerprint(source_key, result["converted_text_sha256_96"])
        if dup_of:
            detail = f"转换后 Markdown 指纹与 {dup_of} 相同，不进入下游阶段"
            _discard_obsolete_outputs(pdf, source_key_root, md_root, cfg, remove_markdown=True)
            db.skip(source_key, "duplicate_markdown", detail, duplicate_of=dup_of)
            print(f"[{i}/{total}] {source_key}  [重复]")
            if not result["skipped"]:
                converted -= 1
            skipped_count += 1
        else:
            action = "复用已有 Markdown" if result["skipped"] else f"{result['elapsed']:.1f}s"
            print(f"[{i}/{total}] {source_key}  ({result['char_count']} 字符, {action})")

    try:
        completed_results: dict[Path, dict] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_single, ta): pdf for ta, pdf in zip(task_args, pdfs)}
            for f in as_completed(futures):
                pdf = futures[f]
                try:
                    completed_results[pdf] = f.result()
                except Exception as e:
                    source_key = source_key_for_path(pdf, source_key_root)
                    db.mark_failed(source_key, "convert", str(e))
                    print(f"✗ {source_key}: {e}")
        for i, pdf in enumerate(pdfs, 1):
            if pdf in completed_results:
                _record_result(i, pdf, completed_results[pdf])
    except PermissionError as e:
        print(f"多进程不可用，回退为串行转换: {e}")
        for i, (args, pdf) in enumerate(zip(task_args, pdfs), 1):
            try:
                _record_result(i, pdf, _process_single(args))
            except Exception as exc:
                source_key = source_key_for_path(pdf, source_key_root)
                db.mark_failed(source_key, "convert", str(exc))
                print(f"[{i}/{total}] ✗ {source_key}: {exc}")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"convert 完成: {converted} 篇  总耗时 {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"跳过: {skipped_count}")
    if converted: print(f"平均: {elapsed/converted:.2f}s/文件")
    if show_time and time_records:
        for name, t in sorted(time_records, key=lambda x: -x[1])[:20]:
            print(f"  {name}: {t:.2f}s")
    print(f"{'='*50}\n全部完成。")
    db.print_stats()
    failed = [
        row["source_key"] for row in db.get_records(selected_source_keys)
        if row["convert_status"] == "failed"
    ]
    if failed:
        print(f"convert 失败文件: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
