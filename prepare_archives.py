"""
prepare_archives.py - 将 archives/ 中的 ZIP 准备为 pipeline 可处理的 PDF 批次。

默认布局:
    archives/batch_0001.zip -> papers/batch_0001/**/*.pdf

该步骤只准备输入文件，不写入状态库。PDF 去重和状态注册由 convert 阶段完成。
"""

import argparse
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv

load_dotenv()

from utils import compact_sha256_file, load_config


BASE_DIR = Path(__file__).parent.resolve()
MANIFEST_NAME = ".knowledgeex_archive_manifest.json"


def _relative_archive_path(value: str | Path) -> Path:
    text = str(value).replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or not text or ".." in path.parts:
        raise ValueError(f"无效的 ZIP 相对路径: {value}")
    if path.name.lower() != ".zip" and path.suffix.lower() != ".zip":
        path = path.with_suffix(".zip")
    return path


def _archive_identity(archive: Path, archive_root: Path) -> dict:
    stat = archive.stat()
    return {
        "archive": archive.relative_to(archive_root).as_posix(),
        "archive_sha256_96": compact_sha256_file(archive),
        "archive_size": stat.st_size,
        "archive_mtime_ns": stat.st_mtime_ns,
    }


def _find_archives(archive_root: Path, selected: list[str]) -> list[Path]:
    if selected:
        paths = [archive_root / _relative_archive_path(value) for value in selected]
    else:
        paths = [
            path for path in archive_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".zip"
        ]
    return sorted(paths)


def _safe_pdf_members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    output_names: set[str] = set()
    for info in zf.infolist():
        member_name = info.filename.replace("\\", "/")
        source_path = PurePosixPath(member_name)
        if info.is_dir() or source_path.suffix.lower() != ".pdf":
            continue
        if source_path.is_absolute() or ".." in source_path.parts:
            raise ValueError(f"ZIP 包含不安全的 PDF 路径: {info.filename}")
        if not source_path.parts or source_path.parts[0] == "__MACOSX":
            continue
        relative = Path(*source_path.parts)
        normalized_name = relative.as_posix().casefold()
        if normalized_name in output_names:
            raise ValueError(f"ZIP 中存在输出路径冲突的 PDF: {relative.as_posix()}")
        output_names.add(normalized_name)
        members.append((info, relative))
    return members


def _read_manifest(batch_dir: Path) -> dict | None:
    path = batch_dir / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _prepared_batch_is_current(batch_dir: Path, identity: dict) -> bool:
    manifest = _read_manifest(batch_dir)
    if not manifest:
        return False
    matches_source = all(
        manifest.get(key) == identity.get(key)
        for key in ("archive", "archive_sha256_96")
    )
    expected_count = manifest.get("pdf_count")
    actual_count = sum(1 for path in batch_dir.rglob("*") if path.suffix.lower() == ".pdf")
    return matches_source and expected_count == actual_count and actual_count > 0


def _extract_one(archive: Path, archive_root: Path, output_root: Path, force: bool) -> tuple[str, int]:
    identity = _archive_identity(archive, archive_root)
    if archive.name.lower() == ".zip":
        raise ValueError("ZIP 文件名不能仅为 .zip")
    relative_batch = archive.relative_to(archive_root).with_suffix("")
    batch_dir = output_root / relative_batch

    if batch_dir.exists() and _prepared_batch_is_current(batch_dir, identity) and not force:
        manifest = _read_manifest(batch_dir) or {}
        return "skipped", int(manifest.get("pdf_count", 0))
    if batch_dir.exists() and not force:
        raise FileExistsError(
            f"{batch_dir} 已存在但不能确认来自当前 ZIP；如需重建请添加 --force"
        )

    batch_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{batch_dir.name}.preparing-", dir=batch_dir.parent))
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            members = _safe_pdf_members(zf)
            if not members:
                raise ValueError("ZIP 中未发现 PDF")
            for info, relative in members:
                target = temp_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

        manifest = {
            **identity,
            "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pdf_count": len(members),
        }
        (temp_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if batch_dir.exists():
            shutil.rmtree(batch_dir)
        temp_dir.replace(batch_dir)
        return "extracted", len(members)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def prepare_archives(archive_root: Path, output_root: Path, selected: list[str],
                     force: bool = False) -> int:
    archive_root = archive_root.resolve()
    output_root = output_root.resolve()
    if not archive_root.is_dir():
        print(f"ZIP 目录不存在: {archive_root}")
        return 1

    try:
        archives = _find_archives(archive_root, selected)
    except ValueError as exc:
        print(f"ZIP 参数错误: {exc}")
        return 2
    if not archives:
        print(f"{archive_root} 下没有待准备的 ZIP 文件")
        return 1

    missing = [archive for archive in archives if not archive.is_file()]
    if missing:
        for archive in missing:
            print(f"找不到 ZIP: {archive}")
        return 1

    output_root.mkdir(parents=True, exist_ok=True)
    extracted = skipped = failed = pdf_total = 0
    print(f"ZIP 来源: {archive_root}")
    print(f"PDF 输出: {output_root}")
    for archive in archives:
        relative_name = archive.relative_to(archive_root).as_posix()
        try:
            status, count = _extract_one(archive, archive_root, output_root, force)
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            failed += 1
            print(f"[失败] {relative_name}: {exc}")
            continue
        pdf_total += count
        if status == "skipped":
            skipped += 1
            print(f"[跳过] {relative_name}: 已准备 {count} 个 PDF")
        else:
            extracted += 1
            print(f"[完成] {relative_name}: 解压 {count} 个 PDF")
            if force:
                print("       注意: --force 只替换 PDF 输入；若旧批次已抽取，请改用新的 ZIP 批次名。")

    print(
        f"\nprepare 完成: 新解压 {extracted} 个批次，跳过 {skipped} 个批次，"
        f"失败 {failed} 个批次，涉及 {pdf_total} 个 PDF"
    )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 archives/ 下 ZIP 解压为 papers/ 批次输入")
    parser.add_argument("--archives-dir", default="archives", help="ZIP 来源目录，默认 archives")
    parser.add_argument(
        "--archive", action="append", default=[], metavar="RELATIVE_ZIP",
        help="只准备指定 ZIP 的相对路径，可省略 .zip，可重复使用",
    )
    parser.add_argument("--force", action="store_true", help="删除并重建指定 ZIP 已有的目标批次")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()
    archive_root = Path(args.archives_dir)
    if not archive_root.is_absolute():
        archive_root = BASE_DIR / archive_root
    output_root = (BASE_DIR / cfg["pdf_dir"]).resolve()
    return prepare_archives(archive_root, output_root, args.archive, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
