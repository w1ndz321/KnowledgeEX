"""
state.py — SQLite 流水线状态管理

三阶段: convert → preprocess → extract
提供原子任务领取、断点续跑、错误重试。
"""

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).parent
STAGES = ("convert", "preprocess", "extract")
TASK_TIMEOUT = 600  # 超时 10 分钟，running 重置为 pending


def _now() -> str:
    return datetime.now().strftime("%m/%d/%Y/%H:%M:%S")


def resolve_db_path(input_dir: Path | None = None, db_dir: Path | None = None) -> Path:
    """根据 input_dir 推断 DB 文件名。例: papers → db/papers.db"""
    db = db_dir or (BASE_DIR / "db")
    db.mkdir(exist_ok=True)
    name = input_dir.name if input_dir else "default"
    return db / f"{name}.db"


class StateDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _create_papers_table(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                source_key                     TEXT PRIMARY KEY,
                convert_status                 TEXT NOT NULL DEFAULT 'pending',
                preprocess_status              TEXT NOT NULL DEFAULT 'pending',
                extract_status                 TEXT NOT NULL DEFAULT 'pending',
                char_count                     INTEGER,
                skip_reason                    TEXT,
                retry_count                    INTEGER NOT NULL DEFAULT 0,
                error_msg                      TEXT,
                claimed_at                     REAL,
                updated_at                     TEXT,
                extract_model                  TEXT,
                content_hash                   TEXT,
                doc_id                         TEXT,
                source_pdf_sha256_96           TEXT,
                converted_text_sha256_96       TEXT,
                duplicate_of                   TEXT,
                skip_code                      TEXT,
                skip_detail                    TEXT,
                convert_at                     TEXT,
                preprocess_at                  TEXT,
                extract_at                     TEXT,
                preprocess_prompt_tokens       INTEGER,
                preprocess_completion_tokens   INTEGER,
                extract_prompt_tokens          INTEGER,
                extract_completion_tokens      INTEGER,
                extract_failed_groups          TEXT
            )
        """)

    def _init_db(self):
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='papers'"
            ).fetchone()
            if exists:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
                if "stem" in columns and "source_key" not in columns:
                    conn.execute("ALTER TABLE papers RENAME TO papers_legacy_stem")
                    self._create_papers_table(conn)
                    target_columns = [
                        "convert_status", "preprocess_status", "extract_status", "char_count",
                        "skip_reason", "retry_count", "error_msg", "claimed_at", "updated_at",
                        "extract_model", "content_hash", "doc_id", "source_pdf_sha256_96",
                        "converted_text_sha256_96", "duplicate_of", "skip_code", "skip_detail",
                        "convert_at", "preprocess_at", "extract_at",
                        "preprocess_prompt_tokens", "preprocess_completion_tokens",
                        "extract_prompt_tokens", "extract_completion_tokens",
                        "extract_failed_groups",
                    ]
                    copied = [column for column in target_columns if column in columns]
                    select_fields = [
                        "stem || '.pdf' AS source_key",
                        *[
                            ("CASE WHEN duplicate_of IS NULL THEN NULL ELSE duplicate_of || '.pdf' END"
                             if column == "duplicate_of" else column)
                            for column in copied
                        ],
                    ]
                    conn.execute(
                        f"""INSERT INTO papers(source_key, {', '.join(copied)})
                            SELECT {', '.join(select_fields)} FROM papers_legacy_stem"""
                    )
                    conn.execute("DROP TABLE papers_legacy_stem")
                elif "source_key" not in columns:
                    raise RuntimeError("papers 状态表缺少 source_key，无法识别其 schema")
            else:
                self._create_papers_table(conn)
            for col in ("extract_model TEXT", "content_hash TEXT",
                        "doc_id TEXT", "source_pdf_sha256_96 TEXT",
                        "converted_text_sha256_96 TEXT", "duplicate_of TEXT",
                        "skip_code TEXT", "skip_detail TEXT",
                        "convert_at TEXT", "preprocess_at TEXT", "extract_at TEXT",
                        "preprocess_prompt_tokens INTEGER", "preprocess_completion_tokens INTEGER",
                        "extract_prompt_tokens INTEGER", "extract_completion_tokens INTEGER",
                        "extract_failed_groups TEXT"):
                try:
                    conn.execute(f"ALTER TABLE papers ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_source_pdf_sha256_96 ON papers(source_pdf_sha256_96)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_converted_text_sha256_96 ON papers(converted_text_sha256_96)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_skip_code ON papers(skip_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_convert_status ON papers(convert_status)")

    # ── 注册 ──

    def register_files(self, source_keys: list[str]):
        with self._conn() as conn:
            conn.executemany("INSERT OR IGNORE INTO papers(source_key, updated_at) VALUES(?, ?)",
                             [(key, _now()) for key in source_keys])

    # ── 原子领取 ──

    def claim_one(self, stage: str, allowed_source_keys: set[str] | None = None) -> str | None:
        """原子领取一个待处理任务，返回 source_key。超时 running 自动重置为 pending。"""
        col = f"{stage}_status"
        if allowed_source_keys is not None and not allowed_source_keys:
            return None
        key_filter = ""
        key_params: list[str] = []
        if allowed_source_keys is not None:
            key_params = sorted(allowed_source_keys)
            key_filter = f" AND source_key IN ({','.join('?' for _ in key_params)})"
        now_ts = time.time()
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET {col}='pending', claimed_at=NULL, updated_at=?
                WHERE {col}='running' AND claimed_at < ?""", (_now(), now_ts - TASK_TIMEOUT))
            if stage == "preprocess":
                extra = "AND convert_status='done'"
            elif stage == "extract":
                # 支持 pending 和 partial（增量重试）
                row = conn.execute(f"""SELECT source_key FROM papers
                    WHERE ({col}='pending' OR {col}='partial') AND skip_reason IS NULL
                    AND skip_code IS NULL
                    AND preprocess_status='done'{key_filter} LIMIT 1""", key_params).fetchone()
                if not row: return None
                source_key = row["source_key"]
                conn.execute(f"UPDATE papers SET {col}='running', claimed_at=?, updated_at=? WHERE source_key=?",
                             (now_ts, _now(), source_key))
                return source_key
            else:
                extra = ""
            row = conn.execute(f"""SELECT source_key FROM papers
                WHERE {col}='pending' AND skip_reason IS NULL AND skip_code IS NULL {extra}{key_filter} LIMIT 1""", key_params).fetchone()
            if not row: return None
            source_key = row["source_key"]
            conn.execute(f"UPDATE papers SET {col}='running', claimed_at=?, updated_at=? WHERE source_key=?",
                         (now_ts, _now(), source_key))
        return source_key

    # ── 状态标记 ──

    def mark_done(self, source_key: str, stage: str):
        col, at_col = f"{stage}_status", f"{stage}_at"
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET {col}='done', {at_col}=?, error_msg=NULL,
                skip_reason=NULL, skip_code=NULL, skip_detail=NULL, duplicate_of=NULL,
                updated_at=? WHERE source_key=?""",
                         (_now(), _now(), source_key))

    def mark_failed(self, source_key: str, stage: str, error_msg: str):
        col = f"{stage}_status"
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET {col}='failed', retry_count=retry_count+1,
                error_msg=?, updated_at=? WHERE source_key=?""", (str(error_msg)[:500], _now(), source_key))

    def skip(self, source_key: str, code: str, detail: str = "", duplicate_of: str | None = None,
             skip_convert: bool = False):
        """Exclude a paper from downstream processing.

        `skip_convert` is true only when conversion never happened, such as a
        duplicate source PDF detected from bytes before parsing.
        """
        reason = f"{code}: {detail}" if detail else code
        convert_clause = ", convert_status='skipped'" if skip_convert else ""
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET skip_reason=?, skip_code=?, skip_detail=?,
                duplicate_of=?{convert_clause}, preprocess_status='skipped',
                extract_status='skipped', claimed_at=NULL, updated_at=? WHERE source_key=?""",
                         (reason[:200], code[:80], detail[:500], duplicate_of, _now(), source_key))

    def release(self, source_key: str, stage: str):
        col = f"{stage}_status"
        with self._conn() as conn:
            conn.execute(f"UPDATE papers SET {col}='pending', claimed_at=NULL, updated_at=? WHERE source_key=?", (_now(), source_key))

    # ── 状态查询 ──

    def was_success(self, source_key: str, stage: str) -> bool:
        col = f"{stage}_status"
        with self._conn() as conn:
            row = conn.execute(f"SELECT {col} FROM papers WHERE source_key=?", (source_key,)).fetchone()
            return row is not None and row[col] == 'done'

    def get_processed(self, stage: str) -> list[str]:
        col = f"{stage}_status"
        with self._conn() as conn:
            return [r["source_key"] for r in conn.execute(f"SELECT source_key FROM papers WHERE {col} IN ('done','partial')").fetchall()]

    def reset_failed(self, stage: str, source_keys: list[str] | set[str] | None = None) -> int:
        col = f"{stage}_status"
        with self._conn() as conn:
            if source_keys:
                selected = sorted(set(source_keys))
                placeholders = ",".join("?" for _ in selected)
                n = conn.execute(
                    f"""UPDATE papers SET {col}='pending', updated_at=?
                        WHERE {col}='failed' AND source_key IN ({placeholders})""",
                    [_now(), *selected],
                ).rowcount
            else:
                n = conn.execute(f"UPDATE papers SET {col}='pending', updated_at=? WHERE {col}='failed'", (_now(),)).rowcount
        print(f"重置 {n} 个 {stage} 失败任务")
        return n

    def reset_stage(self, stage: str, source_keys: list[str] | set[str]) -> int:
        """将选中的论文阶段及失效的下游结果重置为 pending。"""
        selected = sorted(set(source_keys))
        if not selected:
            return 0
        col = f"{stage}_status"
        placeholders = ",".join("?" for _ in selected)
        skip_filter = ""
        if stage == "convert":
            extra = (
                ", char_count=NULL, content_hash=NULL, doc_id=NULL, "
                "source_pdf_sha256_96=NULL, converted_text_sha256_96=NULL, "
                "skip_reason=NULL, skip_code=NULL, skip_detail=NULL, duplicate_of=NULL, "
                "preprocess_status='pending', preprocess_at=NULL, "
                "preprocess_prompt_tokens=NULL, preprocess_completion_tokens=NULL, "
                "extract_status='pending', extract_at=NULL, extract_failed_groups=NULL, "
                "extract_model=NULL, extract_prompt_tokens=NULL, extract_completion_tokens=NULL"
            )
        elif stage == "preprocess":
            skip_filter = " AND skip_reason IS NULL AND skip_code IS NULL"
            extra = (
                ", preprocess_prompt_tokens=NULL, preprocess_completion_tokens=NULL, "
                "extract_status='pending', extract_at=NULL, extract_failed_groups=NULL, "
                "extract_model=NULL, extract_prompt_tokens=NULL, extract_completion_tokens=NULL"
            )
        elif stage == "extract":
            skip_filter = " AND skip_reason IS NULL AND skip_code IS NULL"
            extra = (", extract_failed_groups=NULL, extract_model=NULL, "
                     "extract_prompt_tokens=NULL, extract_completion_tokens=NULL")
        with self._conn() as conn:
            n = conn.execute(
                f"""UPDATE papers SET {col}='pending', claimed_at=NULL,
                    error_msg=NULL{extra}, updated_at=? WHERE source_key IN ({placeholders})
                    {skip_filter}""",
                [_now(), *selected],
            ).rowcount
        print(f"重置 {n} 个 {stage} 任务 → pending")
        return n

    # ── 数据记录 ──

    def set_char_count(self, source_key: str, char_count: int):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET char_count=?, updated_at=? WHERE source_key=?", (char_count, _now(), source_key))

    def set_preprocess_tokens(self, source_key: str, prompt_tokens: int = 0, completion_tokens: int = 0):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET preprocess_prompt_tokens=?, preprocess_completion_tokens=?, updated_at=? WHERE source_key=?",
                         (prompt_tokens, completion_tokens, _now(), source_key))

    def set_extract_model(self, source_key: str, model: str, prompt_tokens: int = 0, completion_tokens: int = 0):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET extract_model=?, extract_prompt_tokens=?, extract_completion_tokens=?, updated_at=? WHERE source_key=?",
                         (model, prompt_tokens, completion_tokens, _now(), source_key))

    def set_source_pdf_fingerprint(self, source_key: str, fingerprint: str, doc_id: str) -> str | None:
        """Persist source PDF identity and return a non-skipped duplicate, if present."""
        with self._conn() as conn:
            existing = conn.execute("""SELECT source_key FROM papers
                WHERE source_pdf_sha256_96=? AND source_key<>?
                AND skip_code IS NULL ORDER BY source_key LIMIT 1""", (fingerprint, source_key)).fetchone()
            conn.execute("""UPDATE papers SET source_pdf_sha256_96=?, doc_id=?, updated_at=?
                WHERE source_key=?""", (fingerprint, doc_id, _now(), source_key))
        return existing["source_key"] if existing else None

    def set_converted_text_fingerprint(self, source_key: str, fingerprint: str,
                                       check_duplicate: bool = True) -> str | None:
        """Persist converted Markdown identity and optionally return a valid duplicate."""
        with self._conn() as conn:
            conn.execute("""UPDATE papers SET converted_text_sha256_96=?, updated_at=?
                WHERE source_key=?""", (fingerprint, _now(), source_key))
            if not check_duplicate:
                return None
            existing = conn.execute("""SELECT source_key FROM papers
                WHERE converted_text_sha256_96=? AND source_key<>?
                AND skip_code IS NULL ORDER BY source_key LIMIT 1""", (fingerprint, source_key)).fetchone()
            if existing:
                return existing["source_key"]
        return None

    def get_failed_groups(self, source_key: str) -> list[str] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT extract_failed_groups FROM papers WHERE source_key=?", (source_key,)).fetchone()
            if row and row["extract_failed_groups"]:
                return [g.strip() for g in row["extract_failed_groups"].split(",") if g.strip()]
        return None

    def set_extract_failed_groups(self, source_key: str, failed_groups: list[str]):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET extract_failed_groups=?, extract_status='partial', updated_at=? WHERE source_key=?",
                         (",".join(failed_groups), _now(), source_key))

    def reset_skipped(self):
        with self._conn() as conn:
            n = conn.execute("""UPDATE papers SET skip_reason=NULL, skip_code=NULL,
                skip_detail=NULL, duplicate_of=NULL,
                convert_status=CASE WHEN convert_status='skipped' THEN 'pending' ELSE convert_status END,
                preprocess_status=CASE WHEN preprocess_status='skipped' THEN 'pending' ELSE preprocess_status END,
                extract_status=CASE WHEN extract_status='skipped' THEN 'pending' ELSE extract_status END,
                updated_at=? WHERE skip_reason IS NOT NULL OR skip_code IS NOT NULL""", (_now(),)).rowcount
        print(f"重置 {n} 个跳过记录")

    # ── 统计 ──

    def get_stats(self) -> dict:
        with self._conn() as conn:
            return dict(conn.execute("""SELECT
                COALESCE(SUM(convert_status='done'),0) AS conv_done,
                COALESCE(SUM(convert_status='pending'),0) AS conv_pending,
                COALESCE(SUM(convert_status='failed'),0) AS conv_fail,
                COALESCE(SUM(convert_status='skipped'),0) AS conv_skipped,
                COALESCE(SUM(preprocess_status='done'),0) AS pre_done,
                COALESCE(SUM(preprocess_status='pending'),0) AS pre_pending,
                COALESCE(SUM(preprocess_status='failed'),0) AS pre_fail,
                COALESCE(SUM(extract_status='done'),0) AS ext_done,
                COALESCE(SUM(extract_status='pending'),0) AS ext_pending,
                COALESCE(SUM(extract_status='failed'),0) AS ext_fail,
                COALESCE(SUM(skip_reason IS NOT NULL),0) AS skipped,
                COUNT(*) AS total
            FROM papers""").fetchone())

    def get_records(self, source_keys: list[str] | set[str] | None = None) -> list[dict]:
        with self._conn() as conn:
            if source_keys:
                selected = sorted(set(source_keys))
                placeholders = ",".join("?" for _ in selected)
                rows = conn.execute(
                    f"SELECT * FROM papers WHERE source_key IN ({placeholders}) ORDER BY source_key",
                    selected,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM papers ORDER BY source_key").fetchall()
        return [dict(row) for row in rows]

    def print_stats(self):
        s = self.get_stats()
        print(f"\n{'='*50}")
        print(f"总计: {s['total']} 篇  (已跳过: {s['skipped']})")
        print(f"  convert:    done={s['conv_done']}  pending={s['conv_pending']}  failed={s['conv_fail']}  skipped={s['conv_skipped']}")
        print(f"  preprocess: done={s['pre_done']}  pending={s['pre_pending']}  failed={s['pre_fail']}")
        print(f"  extract:    done={s['ext_done']}  pending={s['ext_pending']}  failed={s['ext_fail']}")
        with self._conn() as conn:
            chars = conn.execute("SELECT COUNT(char_count) AS cnt, SUM(char_count) AS total, AVG(char_count) AS avg FROM papers WHERE char_count IS NOT NULL").fetchone()
            toks = conn.execute("""SELECT COALESCE(SUM(preprocess_prompt_tokens),0) AS pre_in,
                COALESCE(SUM(preprocess_completion_tokens),0) AS pre_out,
                COALESCE(SUM(extract_prompt_tokens),0) AS ext_in,
                COALESCE(SUM(extract_completion_tokens),0) AS ext_out FROM papers""").fetchone()
        if chars and chars["cnt"]:
            print(f"  字符统计: {chars['cnt']}篇  总{chars['total']:,}  均{int(chars['avg'] or 0):,}")
        if toks["pre_in"] or toks["ext_in"]:
            print(f"  Token 消耗:")
            if toks["pre_in"]:
                pre_done = s["pre_done"] or 1
                print(f"    preprocess: 入{toks['pre_in']:,} 出{toks['pre_out']:,} 总{toks['pre_in']+toks['pre_out']:,} 均{int((toks['pre_in']+toks['pre_out'])/pre_done):,}/篇")
            if toks["ext_in"]:
                ext_done = s["ext_done"] or 1
                print(f"    extract:    入{toks['ext_in']:,} 出{toks['ext_out']:,} 总{toks['ext_in']+toks['ext_out']:,} 均{int((toks['ext_in']+toks['ext_out'])/ext_done):,}/篇")
        with self._conn() as conn:
            skip_rows = conn.execute("""SELECT COALESCE(skip_code, 'legacy_reason') AS code, COUNT(*) AS count
                FROM papers WHERE skip_reason IS NOT NULL OR skip_code IS NOT NULL
                GROUP BY COALESCE(skip_code, 'legacy_reason') ORDER BY count DESC""").fetchall()
        if skip_rows:
            print("  跳过原因: " + ", ".join(f"{row['code']}={row['count']}" for row in skip_rows))
        print(f"{'='*50}\n")

    def print_token_details(self, stage: str = "extract", limit: int = 20):
        """打印单篇 token 消耗 TOP N"""
        col_in = f"{stage}_prompt_tokens"
        col_out = f"{stage}_completion_tokens"
        with self._conn() as conn:
            rows = conn.execute(f"""SELECT source_key, {col_in}, {col_out}
                FROM papers WHERE {col_in} IS NOT NULL
                ORDER BY ({col_in}+{col_out}) DESC LIMIT ?""", (limit,)).fetchall()
        print(f"\n{'='*50}")
        print(f"{stage} 单篇 Token TOP {limit}:")
        for r in rows:
            total = (r[1] or 0) + (r[2] or 0)
            print(f"  {r[0][:60]:<60}  入{r[1] or 0:>8,}  出{r[2] or 0:>8,}  总{total:>10,}")
        print(f"{'='*50}\n")
