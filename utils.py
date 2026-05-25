"""
utils.py — 共享工具函数（LLM 调用、JSON 解析、元数据解析）
"""

import hashlib
import json
import os
import re
import time
import random
import logging

from pathlib import Path

logger = logging.getLogger(__name__)
HASH_HEX_CHARS = 24
HASH_BITS = HASH_HEX_CHARS * 4


# ─── 配置 ──────────────────────────────────────────────────────

def load_config():
    """从 .env 读取所有配置"""
    return {
        "api_key":      os.environ.get("OPENAI_API_KEY", ""),
        "base_url":     os.environ.get("OPENAI_BASE_URL", ""),
        "model":        os.environ.get("LLM_MODEL", ""),
        "temperature":  float(os.environ.get("LLM_TEMPERATURE", "0.0")),
        "max_input_chars":   int(os.environ.get("MAX_INPUT_CHARS", "100000")),
        "max_output_tokens": int(os.environ.get("MAX_OUTPUT_TOKENS", "16384")),
        "max_retries":       int(os.environ.get("MAX_RETRIES", "3")),
        "min_md_chars":      int(os.environ.get("MIN_MD_CHARS", "10000")),
        "metadata_head_chars": int(os.environ.get("METADATA_HEAD_CHARS", "5000")),
        "workers":           int(os.environ.get("WORKERS", "1")),
        "process_limit":     int(os.environ.get("PROCESS_LIMIT", "0")) or None,
        "convert_backend":   os.environ.get("CONVERT_BACKEND", "pymupdf4llm"),
        "convert_workers":   int(os.environ.get("CONVERT_WORKERS", "4")),
        "extract_groups":    os.environ.get("EXTRACT_GROUPS", "").strip(),
        "extract_group_spec": os.environ.get("EXTRACT_GROUP_SPEC", "").strip(),
        "extract_group_parallel": int(os.environ.get("EXTRACT_GROUP_PARALLEL", "1")),
        "pdf_dir":            os.environ.get("PDF_DIR", "papers"),
        "markdown_dir":       os.environ.get("MARKDOWN_DIR", "markdown"),
        "json_output_dir":    os.environ.get("JSON_OUTPUT_DIR", "json_output"),
        "db_dir":             os.environ.get("DB_DIR", "db"),
        "unzip_first":        os.environ.get("UNZIP_FIRST", "true").lower() == "true",
        "delete_zip_after":   os.environ.get("DELETE_ZIP_AFTER_UNZIP", "false").lower() == "true",
    }


def resolve_pipeline_paths(base_dir: Path, cfg: dict, source: str | Path | None = None) -> tuple[Path, Path]:
    """返回 PDF 来源目录及其对应的 Markdown 目录。

    当来源是配置 PDF 根目录内的子目录时，Markdown 保留相对层级，
    使分步调试只处理该范围产生的文件。
    """
    pdf_root = (base_dir / cfg["pdf_dir"]).resolve()
    markdown_root = (base_dir / cfg["markdown_dir"]).resolve()
    if source is None:
        return pdf_root, markdown_root

    source_path = Path(source)
    source_dir = (source_path if source_path.is_absolute() else base_dir / source_path).resolve()
    try:
        relative = source_dir.relative_to(pdf_root)
    except ValueError:
        relative = Path(source_dir.name)
    markdown_dir = markdown_root if relative == Path(".") else markdown_root / relative
    return source_dir, markdown_dir


def resolve_identity_source_root(base_dir: Path, cfg: dict, source_dir: Path) -> Path:
    """Use the configured PDF corpus root whenever a selected source is within it."""
    pdf_root = (base_dir / cfg["pdf_dir"]).resolve()
    try:
        source_dir.resolve().relative_to(pdf_root)
        return pdf_root
    except ValueError:
        return source_dir.resolve()


def cli_values(argv: list[str], flag: str) -> list[str]:
    """读取可重复的 `--flag value` 命令行值。"""
    values = []
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def normalize_source_key(value: str | Path) -> str:
    """Normalize a CLI/source value to a relative PDF task key."""
    text = str(value).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"无效的 source_key: {value}")
    if text.lower().endswith(".pdf"):
        text = f"{text[:-4]}.pdf"
    else:
        text = f"{text}.pdf"
    return Path(text).as_posix()


def source_key_for_path(path: Path, source_dir: Path) -> str:
    """Return a PDF task key from a corresponding PDF/Markdown/JSON path."""
    relative = path.resolve().relative_to(source_dir.resolve()).with_suffix(".pdf")
    return normalize_source_key(relative)


def artifact_path_for_source_key(root: Path, source_key: str, suffix: str) -> Path:
    """Map a PDF source key to a same-layout Markdown or JSON artifact path."""
    key = normalize_source_key(source_key)
    return root / Path(key).with_suffix(suffix)


# ─── Document identity / fingerprints ──────────────────────────

def compact_sha256_bytes(content: bytes) -> str:
    """Return a 96-bit persisted SHA-256 prefix for corpus identity checks."""
    return hashlib.sha256(content).hexdigest()[:HASH_HEX_CHARS]


def compact_sha256_text(content: str) -> str:
    return compact_sha256_bytes(content.encode("utf-8"))


def compact_sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()[:HASH_HEX_CHARS]


def generate_doc_id(source_pdf_sha256_96: str) -> str:
    """Create document identity from the exact source PDF bytes."""
    fingerprint = str(source_pdf_sha256_96 or "").strip()
    if not re.fullmatch(r"[0-9a-f]{24}", fingerprint):
        raise ValueError("doc_id 需要 96-bit SHA-256 十六进制指纹")
    return f"doc_{fingerprint}"


# ─── JSON 解析 ─────────────────────────────────────────────────

def extract_json(raw: str) -> str:
    """从 LLM 返回内容中提取 JSON"""
    for tag in ["```json", "```"]:
        if tag in raw:
            s = raw.find(tag) + len(tag)
            e = raw.find("```", s)
            if e > s:
                return raw[s:e].strip()
    for sc, ec in [("{", "}"), ("[", "]")]:
        si = raw.find(sc)
        if si >= 0:
            depth, in_str, esc = 0, False, False
            for i in range(si, len(raw)):
                c = raw[i]
                if esc: esc = False; continue
                if c == "\\": esc = True; continue
                if c == '"': in_str = not in_str; continue
                if not in_str:
                    depth += (1 if c == sc else -1 if c == ec else 0)
                    if depth == 0: return raw[si:i+1]
    return raw


def repair_truncated_json(raw: str) -> str:
    """修复因 token 截断的不完整 JSON"""
    raw = raw.strip()
    for tag in ["```json", "```"]:
        if raw.endswith(tag): raw = raw[:-len(tag)].strip()
        if raw.startswith(tag): raw = raw[len(tag):].strip()
    # 找最后一个完整条目边界
    for sep in ['\n    {', '\n  {', '\n    "', '\n  "']:
        pos = raw.rfind(sep)
        if pos < 100: continue
        before = raw[:pos].rstrip()
        if before.endswith(','): before = before[:-1].rstrip()
        if before.endswith('}'):
            candidate = before + '\n    ]\n  }'
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and 'entries' in obj: return candidate
            except json.JSONDecodeError: continue
    # 逐字符回退
    for cutoff in range(len(raw), max(len(raw) - 500, 50), -1):
        truncated = raw[:cutoff].rstrip()
        if truncated.endswith(','): truncated = truncated[:-1].rstrip()
        for suffix in ['\n    ]\n  }', '\n  ]\n}', ']}', '] }']:
            candidate = truncated + suffix
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and 'entries' in obj: return candidate
            except json.JSONDecodeError: continue
    return raw


def sanitize(obj):
    """清理 JSON 中可能超限的整数"""
    if isinstance(obj, dict): return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list): return [sanitize(x) for x in obj]
    if isinstance(obj, int) and not isinstance(obj, bool) and abs(obj) >= 10**1000:
        return str(obj)
    return obj


# ─── LLM 调用 ──────────────────────────────────────────────────

def call_llm(client, model, sys_prompt, user_prompt, temperature,
             stream=False, max_tokens=16384, max_retries=5, response_json=True):
    """调用 LLM，返回 (parsed_dict, raw_response, token_usage)"""
    from openai import RateLimitError, APIConnectionError, APITimeoutError

    kwargs = dict(model=model, temperature=temperature, max_tokens=max_tokens,
                  messages=[{"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt}])
    if response_json and not model.startswith("gemini"):
        kwargs["response_format"] = {"type": "json_object"}
    if model.startswith("qwen"):
        kwargs["extra_body"] = {"enable_thinking": False}
    raw, usage = "", {}
    for attempt in range(max_retries):
        try:
            if stream:
                last = None
                for chunk in client.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kwargs):
                    last = chunk
                    if chunk.choices and chunk.choices[0].delta.content:
                        raw += chunk.choices[0].delta.content
                if last and last.usage:
                    usage = {"prompt_tokens": last.usage.prompt_tokens, "completion_tokens": last.usage.completion_tokens, "total_tokens": last.usage.total_tokens}
            else:
                resp = client.chat.completions.create(**kwargs)
                raw = resp.choices[0].message.content or ""
                if resp.usage:
                    usage = {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens, "total_tokens": resp.usage.total_tokens}
            if not raw.strip():
                raise ValueError("LLM 返回空内容")
            extracted = extract_json(raw)
            start = next((i for i, c in enumerate(extracted) if c in "{["), 0)
            obj, _ = json.JSONDecoder().raw_decode(extracted, start)
            return sanitize(obj), raw, usage
        except RateLimitError as e:
            wait = 2 ** attempt + random.uniform(0, 1)
            logger.warning(f"限流 ({attempt+1}/{max_retries})，等待 {wait:.1f}s: {e}")
            if attempt == max_retries - 1: raise
            time.sleep(wait)
        except (APIConnectionError, APITimeoutError) as e:
            wait = 2 ** attempt
            logger.warning(f"网络错误 ({attempt+1}/{max_retries})，等待 {wait}s: {e}")
            if attempt == max_retries - 1: raise
            time.sleep(wait)
        except json.JSONDecodeError as e:
            repaired = repair_truncated_json(raw)
            if repaired != raw:
                try:
                    repaired_json = extract_json(repaired)
                    start = next((i for i, c in enumerate(repaired_json) if c in "{["), 0)
                    obj, _ = json.JSONDecoder().raw_decode(repaired_json, start)
                    n_entries = len(obj.get('entries', []))
                    print(f"  ⚡ JSON 截断修复: 恢复 {n_entries} 条")
                    return sanitize(obj), raw, usage
                except (json.JSONDecodeError, Exception) as e2:
                    logger.warning(f"JSON 修复失败 ({attempt+1}/{max_retries}): {e2}")
            else:
                logger.warning(f"JSON 解析错误 ({attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1: raise ValueError(f"LLM 返回无法解析为 JSON: {e}")
        except Exception as e:
            logger.warning(f"LLM 错误 ({attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1: raise
    return {}, "", {}


# ─── 元数据解析 ────────────────────────────────────────────────

PUB_METADATA_SKIP = [
    "Permission to make digital or hard copies", "For personal or classroom use",
    "Permission to copy without fee", "Republication or systematic reproduction",
    "Permission to republish", "© 1991 ACM", "Permission to make digital",
    "To copy otherwise, to republish, to post on servers", "Permission of the",
    "This material is published", "Copyright ©", "© Copyright",
    "Authorized licensed use", "For permission to copy", "Permission is granted",
    "This work is licensed under", "Licensed under a Creative Commons",
    "Published in", "arXiv:", "doi.org", "DOI:", "FERMILAB",
    "preprint", "Draft", "Technical Report",
    "Chem Soc Rev", "Chem. Soc. Rev.", "Nature", "Science", "Cell", "PNAS",
    "JACS", "Angew", "Adv. Mater.", "Adv. Sci.", "Phys. Rev.", "IEEE",
    "TUTORIAL REVIEW", "REVIEW", "ARTICLE", "LETTER", "PERSPECTIVE",
    "view article online", "view journal", "download pdf", "full text",
    "View Article Online", "View Journal",
]

SECTION_NAMES = [
    "Abstract", "Introduction", "Methods", "Results", "Conclusion",
    "References", "Key learning points",
]


def _extract_section(text: str, heading: str) -> str | None:
    """匹配 ## heading 并提取到下一个 ## 的内容"""
    m = re.search(rf"##\s+\*?\*?{re.escape(heading)}\*?\*?\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _strip_md(text: str) -> str:
    """去除 Markdown 加粗标记"""
    return re.sub(r"\*\*(.*?)\*\*", r"\1", text).strip()


def parse_md_metadata(text: str, md_path: Path) -> dict:
    """从 Markdown 文本中正则提取元数据（多轮fallback，覆盖中英文）"""
    meta = {}

    # ── 标题（多轮fallback）─────────────────────────────────
    title = None
    skip = PUB_METADATA_SKIP + ["View Article Online", "View Journal"]

    # 第一轮：取第一个 # 标题行（排除期刊名、版权等）
    for line in text.split("\n"):
        if line.startswith("# ") and not line.startswith("# #"):
            c = _strip_md(re.sub(r"`(.*?)`", r"\1", line[2:]))
            if len(c) < 15 or any(p.lower() in c.lower() for p in skip):
                continue
            # 移除内嵌的 DOI/引用信息
            for pat in [r"\bCite\s*this[:\s].*?(DOI[:\s]*[0-9./]+)?",
                        r"\bDOI[:\s]*10\.\d{4,}/[^\s]+"]:
                m = re.search(pat, c, re.IGNORECASE)
                if m:
                    b, a = c[:m.start()].strip(), c[m.end():].strip()
                    if b and a and len(b) > 10 and len(a) > 3: c = b + " " + a
                    elif b and len(b) > 15: c = b
                    elif a and len(a) > 15: c = a
            # 移除末尾的期刊/链接标记
            for p in ["view article online", "view journal", "download pdf", "full text"]:
                idx = c.lower().find(p)
                if idx >= 0: c = c[:idx].strip()
            c = re.sub(r"\s+[\*†§#†‡]+\s*$", "", c)
            c = re.sub(r"\s+[0-9]+-[0-9]+\s*$", "", c)
            c = re.sub(r"\s*\([12][0-9]{3}\)\s*$", "", c)
            c = re.sub(r"\s+", " ", c).strip()
            if len(c) >= 10: title = c
            break

    # 第二轮：取第一个 ## 标题行（排除已知段落名）
    if not title:
        for line in text.split("\n"):
            if line.startswith("## ") and not line.startswith("## #"):
                c = _strip_md(re.sub(r"`(.*?)`", r"\1", line[3:]))
                if any(s.lower() in c.lower() for s in SECTION_NAMES + skip):
                    continue
                c = re.sub(r"\s+", " ", c).strip()
                if len(c) >= 15: title = c; break

    # 第三轮：文件名 fallback
    if not title:
        title = md_path.stem.replace("_", " ").replace("-", " ")

    meta["title"] = title

    # ── DOI ─────────────────────────────────────────────
    doi = None
    m = re.search(r"doi\.org/(10\.\d{4,}/\S+)", text, re.IGNORECASE)
    if m:
        doi = m.group(1).rstrip(".")
    if not doi:
        m = re.search(r'10\.\d{4,}/[^\s"\']+', text[:3000])
        if m:
            doi = m.group().rstrip(".,;:)]}")
    meta["doi"] = doi

    # ── 年份（仅从文首元信息区域生成候选，避免正文示例/引用误命中）──
    metadata_head = text[:5000]
    year = None
    for pat in [
        r"^\s*(?:[#*_`-]+\s*)?(?:Date|Published|Publication\s*date|Submitted|Posted)\b[^\n]*?(\d{4})",
        r"^\s*(?:[#*_`-]+\s*)?(?:Received|Accepted|Revised)\b[^\n]*?(\d{4})",
    ]:
        m = re.search(pat, metadata_head, re.IGNORECASE | re.MULTILINE)
        if m:
            year = int(m.group(1))
            if 1990 <= year <= 2030: break
            year = None
    if not year:
        # 版权符号
        m = re.search(r"©\s*(\d{4})", metadata_head)
        if m: year = int(m.group(1))
    meta["year"] = year

    # ── Abstract（中英文 + 宽松匹配 + fallback 段落）─────
    abstract = _extract_section(text, "Abstract")
    if not abstract:
        abstract = _extract_section(text, "摘要")
    if not abstract:
        m = re.search(
            r"(?:^|\n)\*?\*?_?\*?\*?\s*(?:Abstract|摘要)[:.\s]?[\s—–\-]*(.*?)(?=\n\s*\n|\n##|\n# )",
            text, re.DOTALL | re.IGNORECASE)
        if m:
            candidate = m.group(0).strip()
            if len(candidate) > 100: abstract = candidate
    if not abstract:
        for line in text.split("\n"):
            ls = line.strip()
            if len(ls) > 200 and not ls.startswith("#"):
                author_markers = len(re.findall(r"\[\d+(?:,\d+)*\]", ls))
                if author_markers < 3 and "✉" not in ls:
                    abstract = ls
                    break
    meta["abstract"] = abstract

    # ── Introduction（穷举标题变体 + 宽松匹配）─────────────
    introduction = None
    for h in ["Introduction", "INTRODUCTION", "I. INTRODUCTION", "I. Introduction",
              "I Introduction", "1. Introduction", "1 Introduction", "1\tIntroduction",
              "I.\tINTRODUCTION", "1. INTRODUCTION", "1.\tINTRODUCTION",
              "INTRODUCTION.", "1.0 Introduction"]:
        introduction = _extract_section(text, h)
        if introduction: break
    if not introduction:
        m = re.search(
            r"^#{1,2}\s+\*?\*?\s*[IVX0-9.]*(?:Introduction|引言|Background\s*(?:&|\&|and)\s*Summary|Background)[:.]?\*?\*?\s*\n(.*?)(?=\n##|\Z)",
            text, re.DOTALL | re.IGNORECASE)
        if m: introduction = m.group(1).strip()
    meta["introduction"] = introduction

    # ── Keywords ─────────────────────────────────────────
    keywords = None
    kw_raw = _extract_section(text, "Keywords")
    if kw_raw:
        keywords = [k.strip() for k in re.split(r"[;,]", _strip_md(kw_raw)) if k.strip()]
    meta["_keywords_from_paper"] = keywords

    return meta
