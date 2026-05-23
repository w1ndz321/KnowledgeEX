"""
convert.py — PDF 转 Markdown（多进程）

用法:
    python convert.py                         # 转换 PDF_DIR 下所有 PDF
    python convert.py --dir papers            # 指定输入子目录
    python convert.py --force                 # 强制重转
    python convert.py --time                  # 显示耗时
    python convert.py --reset-failed          # 重试失败文件
"""

import hashlib
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from utils import load_config

BASE_DIR = Path(__file__).parent


# ─── 单文件转换 worker ─────────────────────────────────────────

def _process_single(args: tuple) -> dict:
    """多进程 worker：转换单个 PDF。args = (pdf_path_str, md_dir_str, force, backend, rel_parent)"""
    pdf_path_str, md_dir_str, force, backend, rel_parent = args
    pdf_path = Path(pdf_path_str)
    md_dir = Path(md_dir_str)
    stem = pdf_path.stem

    md_path = md_dir / rel_parent / f"{stem}.md"
    if md_path.exists() and not force:
        content = md_path.read_text(encoding="utf-8")
        return {"stem": stem, "skipped": True, "char_count": len(content),
                "content_hash": hashlib.md5(content.encode()).hexdigest(), "elapsed": 0.0}

    t0 = time.time()
    md_path.parent.mkdir(parents=True, exist_ok=True)

    if backend == "pymupdf":
        import fitz
        doc = fitz.open(pdf_path_str)
        md_text = "\n\n".join(str(page.get_text("text")) for page in doc)
        doc.close()
    else:
        import pymupdf4llm
        md_text = str(pymupdf4llm.to_markdown(pdf_path_str))

    elapsed = time.time() - t0
    md_text = _fix_abstract_order(_clean_md(md_text))
    md_path.write_text(md_text, encoding="utf-8")

    return {"stem": stem, "skipped": False, "elapsed": elapsed,
            "char_count": len(md_text),
            "content_hash": hashlib.md5(md_text.encode()).hexdigest()}


def _clean_md(md_text: str) -> str:
    md_text = re.sub(r"\*\*==> picture \[.*?\] intentionally omitted <==\*\*\n?", "", md_text)
    md_text = re.sub(r"(?m)^\|.*\n?", "", md_text)
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


# ─── 主入口 ───────────────────────────────────────────────────

def main():
    cfg = load_config()
    force = "--force" in sys.argv
    show_time = "--time" in sys.argv
    reset_failed = "--reset-failed" in sys.argv

    md_dir = BASE_DIR / cfg["markdown_dir"]
    md_dir.mkdir(exist_ok=True)
    pdf_dir = BASE_DIR / cfg["pdf_dir"]

    # 解析输入目录
    input_dir = pdf_dir
    if "--dir" in sys.argv:
        idx = sys.argv.index("--dir")
        if idx + 1 < len(sys.argv):
            input_dir = Path(sys.argv[idx + 1])
            if not input_dir.is_dir():
                print(f"目录不存在: {input_dir}"); return

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
    if not pdfs:
        print(f"{input_dir} 下没有 PDF 文件"); return

    workers = cfg["convert_workers"]
    backend = cfg["convert_backend"]

    from state import StateDB, resolve_db_path
    db = StateDB(resolve_db_path(input_dir, BASE_DIR / cfg["db_dir"]))
    if reset_failed:
        db.reset_failed("convert")
    db.register_files([p.stem for p in pdfs])

    # 过滤已完成
    if not force:
        done_stems = {s for s in db.get_processed("convert") if db.was_success(s, "convert")}
        pending = [p for p in pdfs if p.stem not in done_stems]
        if len(pending) < len(pdfs):
            print(f"跳过 {len(pdfs) - len(pending)} 个已完成文件")
        pdfs = pending

    if not pdfs:
        print("所有文件已完成转换"); db.print_stats(); return

    if cfg["process_limit"]:
        pdfs = pdfs[:cfg["process_limit"]]

    min_md_chars = cfg["min_md_chars"]
    total = len(pdfs)
    print(f"待处理: {total} 个文件，{workers} 进程，后端: {backend}\n")

    t0 = time.time()
    converted, skipped_count = 0, 0
    time_records = []
    task_args = [(str(p), str(md_dir), force, backend,
                  str(p.relative_to(input_dir).parent)) for p in pdfs]

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_single, ta): pdf for ta, pdf in zip(task_args, pdfs)}
        for i, f in enumerate(as_completed(futures), 1):
            pdf = futures[f]
            try:
                r = f.result()
                stem = r["stem"]
                if r["skipped"]:
                    skipped_count += 1
                    db.mark_done(stem, "convert")
                    if r["char_count"]: db.set_char_count(stem, r["char_count"])
                    continue
                converted += 1
                db.mark_done(stem, "convert")
                if r["char_count"]: db.set_char_count(stem, r["char_count"])
                if r["elapsed"]: time_records.append((pdf.name, r["elapsed"]))
                dup_of = db.check_and_set_hash(stem, r["content_hash"])
                if dup_of:
                    db.skip(stem, f"内容与 {dup_of} 重复")
                    print(f"[{i}/{total}] {pdf.name}  [重复]")
                    converted -= 1; skipped_count += 1
                    continue
                if r["char_count"] < min_md_chars:
                    db.skip(stem, f"字符不足: {r['char_count']} < {min_md_chars}")
                    print(f"[{i}/{total}] {pdf.name}  ({r['char_count']} 字符) [跳过]")
                else:
                    print(f"[{i}/{total}] {pdf.name}  ({r['char_count']} 字符, {r['elapsed']:.1f}s)")
            except Exception as e:
                db.mark_failed(pdf.stem, "convert", str(e))
                print(f"[{i}/{total}] ✗ {pdf.name}: {e}")

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


if __name__ == "__main__":
    main()
