"""存档目录的一致性快照与逐文件校验。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SnapshotError(RuntimeError):
    """快照不能证明是完整、一致的存档副本。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def collect_file_records(directory: Path) -> tuple[dict[str, Any], ...]:
    """递归收集稳定排序的文件尺寸、时间与内容指纹。"""

    ensure_plain_path_chain(directory, description="存档根目录")
    records: list[dict[str, Any]] = []
    paths: list[Path] = []
    for current_directory, directory_names, file_names in os.walk(
        directory, followlinks=False
    ):
        current = Path(current_directory)
        for name in (*directory_names, *file_names):
            path = current / name
            if _is_link_or_reparse_point(path):
                raise SnapshotError(
                    "存档目录包含文件链接或重解析点，无法证明备份边界："
                    f"{path.relative_to(directory)}"
                )
        paths.extend(current / name for name in file_names)

    for path in sorted(
        paths,
        key=lambda item: item.relative_to(directory).as_posix().casefold(),
    ):
        ensure_plain_path(path, description="存档文件")
        stat = path.stat()
        records.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "size": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            }
        )
    return tuple(records)


def _is_link_or_reparse_point(path: Path) -> bool:
    file_stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(file_stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def ensure_plain_path(
    path: Path,
    *,
    description: str,
    allow_missing: bool = False,
) -> None:
    """拒绝链接或 Windows 重解析点，且在解析路径之前保留根项身份。"""

    try:
        unsafe = _is_link_or_reparse_point(path)
    except FileNotFoundError as error:
        if allow_missing:
            return
        raise SnapshotError(f"{description}不存在：{path}") from error
    except OSError as error:
        raise SnapshotError(f"无法检查{description}的文件系统边界：{error}") from error
    if unsafe:
        raise SnapshotError(f"{description}不能是文件链接或 Windows 重解析点：{path}")


def ensure_plain_path_chain(
    path: Path,
    *,
    description: str,
    allow_missing: bool = False,
) -> None:
    """在解析路径前检查其全部现存组件，避免中间链接隐藏真实边界。"""

    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        try:
            unsafe = _is_link_or_reparse_point(component)
        except FileNotFoundError as error:
            if allow_missing:
                return
            raise SnapshotError(f"{description}不存在：{component}") from error
        except OSError as error:
            raise SnapshotError(
                f"无法检查{description}的文件系统边界：{error}"
            ) from error
        if unsafe:
            raise SnapshotError(
                f"{description}不能经过文件链接或 Windows 重解析点：{component}"
            )


def _content_signature(records: tuple[dict[str, Any], ...]) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (str(record["path"]), int(record["size"]), str(record["sha256"]))
        for record in records
    )


def create_verified_snapshot(
    save_directory: str | Path,
    target: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """复制完整 Save，在源和目标都通过哈希复核后发布目标目录。"""

    source_path = Path(save_directory)
    ensure_plain_path_chain(source_path, description="存档根目录")
    source = source_path.resolve(strict=True)
    if not source.is_dir():
        raise SnapshotError(f"存档路径不是目录：{source}")

    target_path = Path(target)
    ensure_plain_path_chain(
        target_path,
        description="快照目标路径",
        allow_missing=True,
    )
    final_target = target_path.resolve(strict=False)
    staging_target = final_target.with_name(f".{final_target.name}.incomplete")
    if final_target.exists():
        raise FileExistsError(f"快照目录已存在：{final_target}")
    if staging_target.exists():
        raise FileExistsError(f"未完成快照目录已存在：{staging_target}")

    final_target.parent.mkdir(parents=True, exist_ok=True)
    staging_save = staging_target / "Save"
    staging_save.mkdir(parents=True)
    before = collect_file_records(source)
    if not before:
        raise SnapshotError(f"存档目录没有文件：{source}")

    for record in before:
        relative = Path(str(record["path"]))
        destination = staging_save / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)

    after = collect_file_records(source)
    copied = collect_file_records(staging_save)
    before_signature = _content_signature(before)
    if before_signature != _content_signature(after):
        raise SnapshotError("存档源文件在复制期间发生变化；保留未完成目录用于诊断")
    if before_signature != _content_signature(copied):
        raise SnapshotError("快照文件校验失败；保留未完成目录用于诊断")

    manifest: dict[str, Any] = {
        "schema": 1,
        "state": "verified",
        "created_utc": datetime.now(UTC).isoformat(),
        "source_save_directory": str(source),
        "files": list(copied),
    }
    if metadata:
        manifest["metadata"] = dict(metadata)
    (staging_target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    staging_target.rename(final_target)
    return manifest
