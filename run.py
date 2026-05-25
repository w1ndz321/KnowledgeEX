"""
run.py - KnowledgeEX 主程序。

推荐用法:
    python run.py all                              # convert -> preprocess -> extract
    python run.py all --paper biology/ace --debug  # 按相对于 PDF_DIR 的路径调试单篇
    python run.py all --to-stage convert           # 仅跑到 Markdown，随后 inspect
    python run.py all --from-stage preprocess      # 从已有 Markdown 继续
    python run.py convert --paper biology/ace --force # 单独重跑某阶段
    python run.py inspect --paper biology/ace      # 查看阶段产物和 evidence 状态
    python run.py status                           # 查看状态库统计

`--source` 表示待处理范围；任务键始终相对于 `PDF_DIR`，因此对同一论文
全量运行或按子目录运行都会使用同一条状态记录。
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from state import StateDB, resolve_db_path
from utils import (load_config, normalize_source_key, resolve_identity_source_root,
                   resolve_pipeline_paths, source_key_for_path)

BASE_DIR = Path(__file__).parent.resolve()
STAGES = ("convert", "preprocess", "extract")


def _add_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source", "--dir", dest="source", metavar="PDF_DIR",
        help="PDF 来源目录；--dir 为兼容别名",
    )
    parser.add_argument(
        "--paper", action="append", default=[], metavar="RELATIVE_PDF",
        help="仅处理相对于 PDF_DIR 的论文路径，可省略 .pdf，可重复使用",
    )


def _scope(source: str | None) -> tuple[dict, Path, Path, Path]:
    cfg = load_config()
    source_dir, markdown_dir = resolve_pipeline_paths(BASE_DIR, cfg, source)
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    relative = markdown_dir.relative_to(markdown_root)
    return cfg, source_dir, markdown_dir, json_root / relative


def _get_db(source: str | None) -> StateDB:
    cfg, source_dir, _, _ = _scope(source)
    identity_root = resolve_identity_source_root(BASE_DIR, cfg, source_dir)
    return StateDB(resolve_db_path(identity_root, BASE_DIR / cfg["db_dir"]))


def _selected_source_keys(args: argparse.Namespace, source_dir: Path,
                          markdown_dir: Path, json_dir: Path) -> set[str]:
    cfg = load_config()
    pdf_key_root = resolve_identity_source_root(BASE_DIR, cfg, source_dir)
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    requested = {normalize_source_key(value) for value in args.paper}
    if requested:
        return requested
    return (
        set(_artifact_map(source_dir, ".pdf", key_root=pdf_key_root)) |
        set(_artifact_map(markdown_dir, ".md", key_root=markdown_root)) |
        set(_artifact_map(json_dir, ".json", {"debug"}, key_root=json_root))
    )


def _snapshot_existing(args: argparse.Namespace, stages: tuple[str, ...] | list[str]) -> Path | None:
    """在 `--force` 覆盖产物前归档当前版本，供结果对比和回退检查。"""
    cfg, source_dir, markdown_dir, json_dir = _scope(args.source)
    source_keys = _selected_source_keys(args, source_dir, markdown_dir, json_dir)
    if not source_keys:
        return None

    db = _get_db(args.source)
    records = db.get_records(source_keys)
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    markdown = _artifact_map(markdown_dir, ".md", key_root=markdown_root)
    outputs = _artifact_map(json_dir, ".json", {"debug"}, key_root=json_root)
    debug_dir = (BASE_DIR / cfg["json_output_dir"] / "debug").resolve()

    files: list[tuple[Path, Path]] = []
    if "convert" in stages:
        files.extend(
            (markdown[source_key], Path("markdown") / markdown[source_key].relative_to(markdown_root))
            for source_key in source_keys if source_key in markdown
        )
    files.extend(
        (outputs[source_key], Path("json_output") / outputs[source_key].relative_to(json_root))
        for source_key in source_keys if source_key in outputs
    )
    if debug_dir.exists():
        for source_key in source_keys:
            prefix = _debug_prefix(cfg, source_key)
            for path in sorted(prefix.parent.glob(f"{prefix.name}_*.json")):
                files.append((path, Path("debug") / path.relative_to(debug_dir)))

    if not files and not records:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snapshot_dir = BASE_DIR / "runs" / stamp
    for source, relative in files:
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "stages": list(stages),
        "source": str(source_dir),
        "source_keys": sorted(source_keys),
        "state_before_rerun": records,
    }
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已归档重跑前产物: {snapshot_dir}")
    return snapshot_dir


def _stage_args(args: argparse.Namespace, stage: str) -> list[str]:
    forwarded: list[str] = []
    if args.source:
        forwarded += ["--source", args.source]
    for paper in args.paper:
        forwarded += ["--only", normalize_source_key(paper)]
    if getattr(args, "force", False):
        forwarded.append("--force")
    if getattr(args, "reset_failed", False):
        forwarded.append("--reset-failed")
    if stage == "convert" and getattr(args, "time", False):
        forwarded.append("--time")
    if stage == "extract" and getattr(args, "debug", False):
        forwarded.append("--debug")
    return forwarded


def _run_stage(stage: str, args: argparse.Namespace) -> int:
    command = [sys.executable, str(BASE_DIR / f"{stage}.py"), *_stage_args(args, stage)]
    print(f"\n[{stage}] {' '.join(command)}", flush=True)
    try:
        return subprocess.run(command, cwd=str(BASE_DIR)).returncode
    except KeyboardInterrupt:
        print("\n运行已中止")
        return 130


def cmd_stage(stage: str, args: argparse.Namespace) -> int:
    if getattr(args, "force", False):
        _snapshot_existing(args, (stage,))
    return _run_stage(stage, args)


def cmd_all(args: argparse.Namespace) -> int:
    start = STAGES.index(args.from_stage)
    end = STAGES.index(args.to_stage)
    if start > end:
        print("错误: --from-stage 必须位于 --to-stage 之前")
        return 2

    chosen = STAGES[start:end + 1]
    print("=" * 62)
    print(f"KnowledgeEX 流程: {' -> '.join(chosen)}")
    if args.paper:
        print(f"论文筛选: {', '.join(normalize_source_key(value) for value in args.paper)}")
    if args.source:
        print(f"PDF 来源: {args.source}")
    print("=" * 62)
    if args.force:
        _snapshot_existing(args, chosen)
    for stage in chosen:
        returncode = _run_stage(stage, args)
        if returncode != 0:
            print(f"\n流程停止: {stage} 退出码 {returncode}")
            return returncode

    print("\n流程完成。使用 `python run.py inspect` 查看中间产物和抽取统计。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = _get_db(args.source)
    if not args.paper:
        db.print_stats()
        return 0

    requested_keys = {normalize_source_key(value) for value in args.paper}
    records = {row["source_key"]: row for row in db.get_records(requested_keys)}
    for requested in args.paper:
        source_key = normalize_source_key(requested)
        row = records.get(source_key)
        if not row:
            print(f"{source_key}: 状态库中不存在")
            continue
        print(
            f"{source_key}: convert={row['convert_status']} "
            f"preprocess={row['preprocess_status']} extract={row['extract_status']}"
        )
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    db = _get_db(args.source)
    if args.paper:
        db.reset_stage(args.stage, {normalize_source_key(value) for value in args.paper})
    else:
        db.reset_failed(args.stage)
    return 0


def _artifact_map(root: Path, suffix: str, excluded_dirs: set[str] | None = None,
                  key_root: Path | None = None) -> dict[str, Path]:
    if not root.exists():
        return {}
    excluded_dirs = excluded_dirs or set()
    return {
        source_key_for_path(path, key_root or root): path
        for path in sorted(root.glob(f"**/*{suffix}"))
        if not (set(path.relative_to(root).parts[:-1]) & excluded_dirs)
    }


def _debug_prefix(cfg: dict, source_key: str) -> Path:
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    debug_root = json_root / "debug"
    return debug_root / Path(source_key).with_suffix("")


def cmd_inspect(args: argparse.Namespace) -> int:
    cfg, source_dir, markdown_dir, json_dir = _scope(args.source)
    pdf_key_root = resolve_identity_source_root(BASE_DIR, cfg, source_dir)
    markdown_root = (BASE_DIR / cfg["markdown_dir"]).resolve()
    json_root = (BASE_DIR / cfg["json_output_dir"]).resolve()
    pdfs = _artifact_map(source_dir, ".pdf", key_root=pdf_key_root)
    markdown = _artifact_map(markdown_dir, ".md", key_root=markdown_root)
    outputs = _artifact_map(json_dir, ".json", {"debug"}, key_root=json_root)
    requested = {normalize_source_key(value) for value in args.paper}
    source_keys = sorted(requested or (set(pdfs) | set(markdown) | set(outputs)))
    if not source_keys:
        print("当前范围未发现 PDF、Markdown 或 JSON 产物")
        return 1

    db = _get_db(args.source)
    records = {row["source_key"]: row for row in db.get_records(source_keys)}
    print(f"PDF 来源: {source_dir}")
    print(f"Markdown: {markdown_dir}")
    print(f"JSON 输出: {json_dir}")
    for source_key in source_keys:
        print(f"\n[{source_key}]")
        row = records.get(source_key)
        if row:
            print(
                f"  状态: convert={row['convert_status']} "
                f"preprocess={row['preprocess_status']} extract={row['extract_status']}"
            )
        else:
            print("  状态: 未注册")
        if source_key in pdfs:
            print(f"  PDF: {pdfs[source_key]}")
        md_path = markdown.get(source_key)
        if md_path:
            text = md_path.read_text(encoding="utf-8")
            pages = text.count("<!-- PAGE ")
            print(f"  MD: {md_path} ({len(text):,} 字符, {pages} 页标记)")
        out_path = outputs.get(source_key)
        if out_path:
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
                entries = data.get("entries", [])
                counts = Counter(entry.get("type", "unknown") for entry in entries)
                located = sum(1 for entry in entries if entry.get("evidence", {}).get("page") is not None)
                title = data.get("metadata", {}).get("title", "")
                if title:
                    print(f"  标题: {title}")
                type_summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
                print(f"  JSON: {out_path} ({len(entries)} 条; page evidence {located}/{len(entries)})")
                if type_summary:
                    print(f"  类型: {type_summary}")
                relations = [entry for entry in entries if entry.get("type") == "relation"]
                if relations:
                    linked = sum(
                        1 for entry in relations
                        if (entry.get("payload", {}).get("head_entry_id") or entry.get("head"))
                        and (entry.get("payload", {}).get("tail_entry_id") or entry.get("tail"))
                    )
                    partial = sum(
                        1 for entry in relations
                        if bool(entry.get("payload", {}).get("head_entry_id") or entry.get("head"))
                        != bool(entry.get("payload", {}).get("tail_entry_id") or entry.get("tail"))
                    )
                    print(
                        f"  Relation links: fully_linked={linked}/{len(relations)} "
                        f"partial={partial} unlinked={len(relations) - linked - partial}"
                    )
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  JSON: {out_path} (读取失败: {exc})")
        debug_prefix = _debug_prefix(cfg, source_key)
        raw_files = sorted(debug_prefix.parent.glob(f"{debug_prefix.name}_*_raw.json"))
        if raw_files:
            print(f"  Debug raw: {len(raw_files)} 个响应文件，目录 {debug_prefix.parent}")
        audit_path = Path(f"{debug_prefix}_evidence_anchor_audit.json")
        if audit_path.exists():
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                print(
                    f"  Anchor audit: matched={audit.get('matched', 0)}/{audit.get('total', 0)} "
                    f"unmatched={audit.get('unmatched', 0)} "
                    f"methods={audit.get('match_method_counts', {})} ({audit_path})"
                )
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  Anchor audit: {audit_path} (读取失败: {exc})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KnowledgeEX 分步流水线主程序")
    commands = parser.add_subparsers(dest="command", required=True)

    all_parser = commands.add_parser("all", help="按顺序运行完整或部分流水线")
    _add_scope_args(all_parser)
    all_parser.add_argument("--from-stage", choices=STAGES, default="convert", help="开始阶段")
    all_parser.add_argument("--to-stage", choices=STAGES, default="extract", help="结束阶段")
    all_parser.add_argument("--force", action="store_true", help="重跑本次选中的已有产物")
    all_parser.add_argument("--debug", action="store_true", help="保存 extract 的原始 LLM 响应")
    all_parser.add_argument("--time", action="store_true", help="显示 convert 单篇耗时")
    all_parser.set_defaults(handler=cmd_all)

    for stage in STAGES:
        stage_parser = commands.add_parser(stage, help=f"仅运行 {stage} 阶段")
        _add_scope_args(stage_parser)
        stage_parser.add_argument("--force", action="store_true", help="重跑本次选中的已有产物")
        stage_parser.add_argument("--reset-failed", action="store_true", help="重试当前范围内的失败任务")
        if stage == "convert":
            stage_parser.add_argument("--time", action="store_true", help="显示单篇耗时")
        if stage == "extract":
            stage_parser.add_argument("--debug", action="store_true", help="保存原始 LLM 响应")
        stage_parser.set_defaults(handler=lambda parsed, current=stage: cmd_stage(current, parsed))

    status_parser = commands.add_parser("status", help="查看流水线状态")
    _add_scope_args(status_parser)
    status_parser.set_defaults(handler=cmd_status)

    inspect_parser = commands.add_parser("inspect", help="查看文件、条目和 evidence 产物")
    _add_scope_args(inspect_parser)
    inspect_parser.set_defaults(handler=cmd_inspect)

    reset_parser = commands.add_parser("reset", help="重置失败任务，或精确重置指定论文")
    _add_scope_args(reset_parser)
    reset_parser.add_argument("--stage", choices=STAGES, required=True, help="待重置阶段")
    reset_parser.set_defaults(handler=cmd_reset)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
