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

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS papers (
                    stem              TEXT PRIMARY KEY,
                    convert_status    TEXT NOT NULL DEFAULT 'pending',
                    preprocess_status TEXT NOT NULL DEFAULT 'pending',
                    extract_status    TEXT NOT NULL DEFAULT 'pending',
                    char_count        INTEGER,
                    skip_reason       TEXT,
                    retry_count       INTEGER NOT NULL DEFAULT 0,
                    error_msg         TEXT,
                    claimed_at        REAL,
                    updated_at        TEXT
                )
            """)
            for col in ("extract_model TEXT", "content_hash TEXT",
                        "convert_at TEXT", "preprocess_at TEXT", "extract_at TEXT",
                        "preprocess_prompt_tokens INTEGER", "preprocess_completion_tokens INTEGER",
                        "extract_prompt_tokens INTEGER", "extract_completion_tokens INTEGER",
                        "extract_failed_groups TEXT"):
                try:
                    conn.execute(f"ALTER TABLE papers ADD COLUMN {col}")
                except Exception:
                    pass

    # ── 注册 ──

    def register_files(self, stems: list[str]):
        with self._conn() as conn:
            conn.executemany("INSERT OR IGNORE INTO papers(stem, updated_at) VALUES(?, ?)",
                             [(s, _now()) for s in stems])

    # ── 原子领取 ──

    def claim_one(self, stage: str) -> str | None:
        """原子领取一个待处理任务，返回 stem。超时 running 自动重置为 pending。"""
        col = f"{stage}_status"
        now_ts = time.time()
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET {col}='pending', claimed_at=NULL, updated_at=?
                WHERE {col}='running' AND claimed_at < ?""", (_now(), now_ts - TASK_TIMEOUT))
            if stage == "preprocess":
                extra = "AND convert_status='done'"
            elif stage == "extract":
                # 支持 pending 和 partial（增量重试）
                row = conn.execute(f"""SELECT stem FROM papers
                    WHERE ({col}='pending' OR {col}='partial') AND skip_reason IS NULL AND preprocess_status='done' LIMIT 1""").fetchone()
                if not row: return None
                stem = row["stem"]
                conn.execute(f"UPDATE papers SET {col}='running', claimed_at=?, updated_at=? WHERE stem=?",
                             (now_ts, _now(), stem))
                return stem
            else:
                extra = ""
            row = conn.execute(f"""SELECT stem FROM papers
                WHERE {col}='pending' AND skip_reason IS NULL {extra} LIMIT 1""").fetchone()
            if not row: return None
            stem = row["stem"]
            conn.execute(f"UPDATE papers SET {col}='running', claimed_at=?, updated_at=? WHERE stem=?",
                         (now_ts, _now(), stem))
        return stem

    # ── 状态标记 ──

    def mark_done(self, stem: str, stage: str):
        col, at_col = f"{stage}_status", f"{stage}_at"
        with self._conn() as conn:
            conn.execute(f"UPDATE papers SET {col}='done', {at_col}=?, error_msg=NULL, skip_reason=NULL, updated_at=? WHERE stem=?",
                         (_now(), _now(), stem))

    def mark_failed(self, stem: str, stage: str, error_msg: str):
        col = f"{stage}_status"
        with self._conn() as conn:
            conn.execute(f"""UPDATE papers SET {col}='failed', retry_count=retry_count+1,
                error_msg=?, updated_at=? WHERE stem=?""", (str(error_msg)[:500], _now(), stem))

    def skip(self, stem: str, reason: str):
        with self._conn() as conn:
            conn.execute("""UPDATE papers SET skip_reason=?, preprocess_status='skipped',
                extract_status='skipped', updated_at=? WHERE stem=?""", (reason[:200], _now(), stem))

    def release(self, stem: str, stage: str):
        col = f"{stage}_status"
        with self._conn() as conn:
            conn.execute(f"UPDATE papers SET {col}='pending', claimed_at=NULL, updated_at=? WHERE stem=?", (_now(), stem))

    # ── 状态查询 ──

    def was_success(self, stem: str, stage: str) -> bool:
        col = f"{stage}_status"
        with self._conn() as conn:
            row = conn.execute(f"SELECT {col} FROM papers WHERE stem=?", (stem,)).fetchone()
            return row is not None and row[col] == 'done'

    def get_processed(self, stage: str) -> list[str]:
        col = f"{stage}_status"
        with self._conn() as conn:
            return [r["stem"] for r in conn.execute(f"SELECT stem FROM papers WHERE {col} IN ('done','partial')").fetchall()]

    def reset_failed(self, stage: str):
        col = f"{stage}_status"
        with self._conn() as conn:
            n = conn.execute(f"UPDATE papers SET {col}='pending', updated_at=? WHERE {col}='failed'", (_now(),)).rowcount
        print(f"重置 {n} 个 {stage} 失败任务")

    # ── 数据记录 ──

    def set_char_count(self, stem: str, char_count: int):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET char_count=?, updated_at=? WHERE stem=?", (char_count, _now(), stem))

    def set_preprocess_tokens(self, stem: str, prompt_tokens: int = 0, completion_tokens: int = 0):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET preprocess_prompt_tokens=?, preprocess_completion_tokens=?, updated_at=? WHERE stem=?",
                         (prompt_tokens, completion_tokens, _now(), stem))

    def set_extract_model(self, stem: str, model: str, prompt_tokens: int = 0, completion_tokens: int = 0):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET extract_model=?, extract_prompt_tokens=?, extract_completion_tokens=?, updated_at=? WHERE stem=?",
                         (model, prompt_tokens, completion_tokens, _now(), stem))

    def check_and_set_hash(self, stem: str, content_hash: str) -> str | None:
        """写入 content_hash，返回重复文件的 stem（若有），无则 None。"""
        with self._conn() as conn:
            existing = conn.execute("SELECT stem FROM papers WHERE content_hash=?", (content_hash,)).fetchone()
            if existing:
                return None if existing["stem"] == stem else existing["stem"]
            try:
                conn.execute("UPDATE papers SET content_hash=?, updated_at=? WHERE stem=?", (content_hash, _now(), stem))
            except Exception:
                row = conn.execute("SELECT stem FROM papers WHERE content_hash=?", (content_hash,)).fetchone()
                if row and row["stem"] != stem:
                    return row["stem"]
        return None

    def get_failed_groups(self, stem: str) -> list[str] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT extract_failed_groups FROM papers WHERE stem=?", (stem,)).fetchone()
            if row and row["extract_failed_groups"]:
                return [g.strip() for g in row["extract_failed_groups"].split(",") if g.strip()]
        return None

    def set_extract_failed_groups(self, stem: str, failed_groups: list[str]):
        with self._conn() as conn:
            conn.execute("UPDATE papers SET extract_failed_groups=?, extract_status='partial', updated_at=? WHERE stem=?",
                         (",".join(failed_groups), _now(), stem))

    def reset_skipped(self):
        with self._conn() as conn:
            n = conn.execute("""UPDATE papers SET skip_reason=NULL,
                preprocess_status=CASE WHEN preprocess_status='skipped' THEN 'pending' ELSE preprocess_status END,
                updated_at=? WHERE skip_reason IS NOT NULL""", (_now(),)).rowcount
        print(f"重置 {n} 个跳过记录")

    # ── 统计 ──

    def get_stats(self) -> dict:
        with self._conn() as conn:
            return dict(conn.execute("""SELECT
                COALESCE(SUM(convert_status='done'),0) AS conv_done,
                COALESCE(SUM(convert_status='pending'),0) AS conv_pending,
                COALESCE(SUM(convert_status='failed'),0) AS conv_fail,
                COALESCE(SUM(preprocess_status='done'),0) AS pre_done,
                COALESCE(SUM(preprocess_status='pending'),0) AS pre_pending,
                COALESCE(SUM(preprocess_status='failed'),0) AS pre_fail,
                COALESCE(SUM(extract_status='done'),0) AS ext_done,
                COALESCE(SUM(extract_status='pending'),0) AS ext_pending,
                COALESCE(SUM(extract_status='failed'),0) AS ext_fail,
                COALESCE(SUM(skip_reason IS NOT NULL),0) AS skipped,
                COUNT(*) AS total
            FROM papers""").fetchone())

    def print_stats(self):
        s = self.get_stats()
        print(f"\n{'='*50}")
        print(f"总计: {s['total']} 篇  (已跳过: {s['skipped']})")
        print(f"  convert:    done={s['conv_done']}  pending={s['conv_pending']}  failed={s['conv_fail']}")
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
        print(f"{'='*50}\n")

    def print_token_details(self, stage: str = "extract", limit: int = 20):
        """打印单篇 token 消耗 TOP N"""
        col_in = f"{stage}_prompt_tokens"
        col_out = f"{stage}_completion_tokens"
        with self._conn() as conn:
            rows = conn.execute(f"""SELECT stem, {col_in}, {col_out}
                FROM papers WHERE {col_in} IS NOT NULL
                ORDER BY ({col_in}+{col_out}) DESC LIMIT ?""", (limit,)).fetchall()
        print(f"\n{'='*50}")
        print(f"{stage} 单篇 Token TOP {limit}:")
        for r in rows:
            total = (r[1] or 0) + (r[2] or 0)
            print(f"  {r[0][:60]:<60}  入{r[1] or 0:>8,}  出{r[2] or 0:>8,}  总{total:>10,}")
        print(f"{'='*50}\n")
