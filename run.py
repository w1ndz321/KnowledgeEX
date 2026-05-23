"""
run.py — paperEX 一键启动入口

用法:
    python run.py all                # 完整流水线（convert → preprocess → extract）
    python run.py convert            # 仅 PDF → MD
    python run.py preprocess         # 仅元数据 + 学科
    python run.py extract            # 仅知识抽取
    python run.py status             # 查看进度
    python run.py reset --stage S    # 重置某阶段为 pending（S=preprocess|extract）

参数:
    --force                         强制重处理
    --debug                         输出 LLM raw（extract 阶段）
    --dir DIR                       指定 PDF 子目录

环境变量（.env）控制全部参数，见 .env 注释。
"""

import os
import sys
import signal
import subprocess
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from state import StateDB, resolve_db_path
from utils import load_config

BASE_DIR = Path(__file__).parent
_pids = []


def _get_db():
    cfg = load_config()
    return StateDB(resolve_db_path(BASE_DIR / cfg["markdown_dir"], BASE_DIR / cfg["db_dir"]))


def _run_stage(script: str, extra_args: list[str] | None = None, wait: bool = True):
    """运行阶段脚本。wait=False 时并行启动，返回 Popen。"""
    cmd = [sys.executable, str(BASE_DIR / script)] + (extra_args or [])
    tag = f"[{script.replace('.py', '')}]"
    print(f"{tag} 启动: {' '.join(cmd)}")
    if wait:
        p = subprocess.run(cmd, cwd=str(BASE_DIR))
        if p.returncode != 0:
            print(f"\n✗ {script} 退出码 {p.returncode}")
            _kill_all()
            sys.exit(p.returncode)
        return None
    else:
        p = subprocess.Popen(cmd, cwd=str(BASE_DIR))
        _pids.append(p)
        return p


def _kill_all():
    for p in _pids:
        if p.poll() is None:
            p.terminate()
    _pids.clear()


def cmd_status():
    _get_db().print_stats()


def cmd_reset(args: list[str]):
    db = _get_db()
    stage = None
    for i, a in enumerate(args):
        if a == "--stage" and i + 1 < len(args):
            stage = args[i + 1]
    if stage not in ("convert", "preprocess", "extract"):
        print("用法: python run.py reset --stage <convert|preprocess|extract>"); return
    n = db.reset_failed(stage)
    print(f"已重置 {stage} 的 {n} 个失败任务 → pending")


def cmd_all(extra_args: list[str]):
    print("\n" + "="*60)
    print("  paperEX 流水线启动（三阶段并行）")
    print("  convert ⇢ preprocess ⇢ extract 自动流水")
    print("  Ctrl+C 停止所有")
    print("="*60 + "\n")

    # 过滤通用参数
    stage_args = [a for a in extra_args if a in ("--force", "--debug", "--time")]
    for i, a in enumerate(extra_args):
        if a == "--dir" and i + 1 < len(extra_args):
            stage_args += ["--dir", extra_args[i + 1]]

    # 三阶段同时启动，各阶段 claim_one 自动等待前序
    signal.signal(signal.SIGINT, lambda *_: (_kill_all(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_kill_all(), sys.exit(0)))

    procs = [
        _run_stage("convert.py", stage_args, wait=False),
        _run_stage("preprocess.py", stage_args, wait=False),
        _run_stage("extract.py", stage_args, wait=False),
    ]

    # 等待所有完成
    failed = False
    for i, p in enumerate(procs):
        p.wait()
        script = ["convert.py", "preprocess.py", "extract.py"][i]
        if p.returncode != 0:
            print(f"\n✗ {script} 退出码 {p.returncode}")
            failed = True

    if not failed:
        print("\n✓ 全部完成")
    cmd_status()


def main():
    if len(sys.argv) < 2:
        print(__doc__); return

    cmd = sys.argv[1]
    extra = sys.argv[2:]

    if cmd == "status":
        cmd_status()
    elif cmd == "reset":
        cmd_reset(extra)
    elif cmd == "convert":
        _run_stage("convert.py", [a for a in extra if a in ("--force", "--time")])
    elif cmd == "preprocess":
        _run_stage("preprocess.py", [a for a in extra if a in ("--force",)])
    elif cmd == "extract":
        _run_stage("extract.py", [a for a in extra if a in ("--debug", "--force",)])
    elif cmd == "all":
        cmd_all(extra)
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
