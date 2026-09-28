"""修改器备份的枚举、完整性检查与单文件恢复。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .snapshot import (
    SnapshotError,
    collect_file_records,
    create_verified_snapshot,
    ensure_plain_path,
    ensure_plain_path_chain,
)
from .storage import CommitResult, commit_file, running_processes


@dataclass(frozen=True)
class BackupInfo:
    directory: Path
    created_utc: str
    source_save_directory: str
    file_count: int
    note: str
    operation: str


@dataclass(frozen=True)
class BackupFileInfo:
    relative_path: str
    size: int
    sha256: str


def create_backup(
    save_directory: str | Path,
    backup_root: str | Path,
    *,
    operation: str,
    note: str = "",
    metadata: dict[str, object] | None = None,
) -> BackupInfo:
    """在唯一目录中创建并发布完整 Save 快照。"""

    source_path = Path(save_directory)
    root_path = Path(backup_root)
    ensure_plain_path_chain(source_path, description="存档根目录")
    ensure_plain_path_chain(
        root_path,
        description="备份根目录",
        allow_missing=True,
    )
    source = source_path.resolve(strict=True)
    root = root_path.resolve(strict=False)
    _validate_backup_root(source, root)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = root / f"{timestamp}-{uuid.uuid4().hex[:8]}"
    combined_metadata: dict[str, object] = {
        "operation": operation,
        "note": note.strip(),
    }
    if metadata:
        combined_metadata.update(metadata)
    manifest = create_verified_snapshot(
        source_path,
        root_path / target.name,
        metadata=combined_metadata,
    )
    return _info_from_manifest(target, manifest)


def _validate_backup_root(source: Path, backup_root: Path) -> None:
    try:
        backup_root.relative_to(source)
    except ValueError:
        pass
    else:
        raise SnapshotError("备份目录不能位于游戏 Save 目录内部")
    try:
        source.relative_to(backup_root)
    except ValueError:
        return
    raise SnapshotError("备份目录不能包含整个游戏 Save 目录")


def list_backups(backup_root: str | Path) -> tuple[BackupInfo, ...]:
    root_path = Path(backup_root)
    if not root_path.is_dir():
        return ()
    try:
        ensure_plain_path_chain(root_path, description="备份根目录")
    except SnapshotError:
        return ()
    root = root_path.resolve(strict=True)
    backups: list[BackupInfo] = []
    for directory in root.iterdir():
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not manifest_path.is_file():
            continue
        try:
            ensure_plain_path(directory, description="备份目录")
            ensure_plain_path(manifest_path, description="备份清单")
            manifest = _load_manifest(manifest_path)
            backups.append(_info_from_manifest(directory, manifest))
        except (OSError, ValueError, json.JSONDecodeError, SnapshotError):
            continue
    return tuple(sorted(backups, key=lambda item: item.created_utc, reverse=True))


def _verify_backup_data(
    backup_directory: str | Path,
) -> tuple[BackupInfo, dict[str, Any]]:
    """校验清单格式、相对路径、文件集合、尺寸及 SHA-256。"""

    directory_path = Path(backup_directory)
    ensure_plain_path_chain(directory_path, description="备份目录")
    directory = directory_path.resolve(strict=True)
    if not directory.is_dir():
        raise SnapshotError(f"备份路径不是目录：{directory}")
    save_directory = directory / "Save"
    manifest_path = directory / "manifest.json"
    ensure_plain_path(save_directory, description="备份 Save 根目录")
    ensure_plain_path(manifest_path, description="备份清单")
    manifest = _load_manifest(manifest_path)
    info = _info_from_manifest(directory, manifest)
    records = manifest["files"]
    assert isinstance(records, list)
    expected_paths: set[str] = set()
    expected_records: dict[str, tuple[int, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise SnapshotError("备份清单文件记录类型无效")
        relative_text = record.get("path")
        size = record.get("size")
        expected_hash = record.get("sha256")
        if not isinstance(relative_text, str) or not isinstance(size, int) or not isinstance(
            expected_hash, str
        ):
            raise SnapshotError("备份清单文件记录字段无效")
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise SnapshotError("备份清单包含越界路径")
        normalized = relative.as_posix()
        if normalized.casefold() in {item.casefold() for item in expected_paths}:
            raise SnapshotError("备份清单包含重复路径")
        expected_paths.add(normalized)
        expected_records[normalized.casefold()] = (size, expected_hash.upper())

    try:
        actual_records = collect_file_records(save_directory)
    except (OSError, ValueError) as error:
        raise SnapshotError(f"无法安全枚举备份文件：{error}") from error
    actual_by_path = {
        str(record["path"]).casefold(): record for record in actual_records
    }
    if set(actual_by_path) != set(expected_records):
        raise SnapshotError("备份文件集合与清单不一致")
    for normalized, (expected_size, expected_hash) in expected_records.items():
        actual = actual_by_path[normalized]
        if int(actual["size"]) != expected_size:
            raise SnapshotError(f"备份文件尺寸不符：{actual['path']}")
        if str(actual["sha256"]) != expected_hash:
            raise SnapshotError(f"备份文件哈希不符：{actual['path']}")
    return info, manifest


def verify_backup(backup_directory: str | Path) -> BackupInfo:
    """校验清单格式、相对路径、文件集合、尺寸及 SHA-256。"""

    info, _manifest = _verify_backup_data(backup_directory)
    return info


def list_backup_files(backup_directory: str | Path) -> tuple[BackupFileInfo, ...]:
    """完整校验备份并返回清单中的稳定排序文件列表。"""

    _verified, manifest = _verify_backup_data(backup_directory)
    records = manifest["files"]
    assert isinstance(records, list)
    files = tuple(
        BackupFileInfo(
            relative_path=str(record["path"]),
            size=int(record["size"]),
            sha256=str(record["sha256"]).upper(),
        )
        for record in records
        if isinstance(record, dict)
    )
    return tuple(sorted(files, key=lambda item: item.relative_path.casefold()))


def verify_backup_compatibility(
    backup_directory: str | Path,
    expected_fingerprints: Mapping[str, str | None],
) -> BackupInfo:
    """完整校验备份，并要求其安装指纹与当前已验证安装完全一致。"""

    info, manifest = _verify_backup_data(backup_directory)
    _validate_installation_fingerprints(manifest, expected_fingerprints)
    return info


def _validate_installation_fingerprints(
    manifest: Mapping[str, Any],
    expected_fingerprints: Mapping[str, str | None],
) -> None:
    """要求已载入清单的游戏安装指纹与当前已验证安装完全一致。"""

    metadata = manifest.get("metadata")
    recorded = metadata.get("installation_fingerprints") if isinstance(metadata, dict) else None
    if not isinstance(recorded, dict) or not all(
        isinstance(relative, str) and isinstance(digest, str)
        for relative, digest in recorded.items()
    ):
        raise SnapshotError("备份未记录可验证的游戏版本指纹，禁止恢复")
    normalized_recorded = {
        str(relative): str(digest).upper() for relative, digest in recorded.items()
    }
    normalized_expected = {
        relative: digest.upper()
        for relative, digest in expected_fingerprints.items()
        if digest is not None
    }
    if normalized_recorded != normalized_expected:
        raise SnapshotError("备份所属游戏版本与当前安装不匹配，禁止恢复")


def restore_backup_file(
    backup_directory: str | Path,
    relative_path: str,
    destination: str | Path,
    *,
    expected_destination_sha256: str,
    expected_candidate_sha256: str,
    transaction_backup_root: str | Path,
    expected_fingerprints: Mapping[str, str | None],
    validator: Callable[[bytes], None] | None = None,
    process_checker: Callable[[Iterable[str]], tuple[str, ...]] = running_processes,
) -> CommitResult:
    """完整校验备份后，通过安全提交链路恢复其中一个文件。"""

    backup_path = Path(backup_directory)
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise SnapshotError("恢复路径越界")
    _info, manifest = _verify_backup_data(backup_path)
    _validate_installation_fingerprints(manifest, expected_fingerprints)
    records = manifest["files"]
    assert isinstance(records, list)
    matching_files = {
        str(item["path"]).casefold(): BackupFileInfo(
            relative_path=str(item["path"]),
            size=int(item["size"]),
            sha256=str(item["sha256"]).upper(),
        )
        for item in records
        if isinstance(item, dict)
    }
    selected = matching_files.get(relative.as_posix().casefold())
    if selected is None or selected.relative_path != relative.as_posix():
        raise SnapshotError("恢复文件不在已验证的备份清单中")
    directory = backup_path.resolve(strict=True)
    candidate_path = directory / "Save" / Path(*relative.parts)
    ensure_plain_path(candidate_path, description="恢复文件")
    try:
        candidate_path.resolve(strict=True).relative_to(
            (directory / "Save").resolve(strict=True)
        )
    except (OSError, ValueError) as error:
        raise SnapshotError("恢复路径越界") from error
    candidate = candidate_path.read_bytes()
    candidate_hash = hashlib.sha256(candidate).hexdigest().upper()
    if len(candidate) != selected.size or candidate_hash != selected.sha256:
        raise SnapshotError("恢复文件在校验后发生变化")
    if candidate_hash != expected_candidate_sha256.upper():
        raise SnapshotError("恢复候选与用户确认的预览不一致，请重新预览并确认")
    return commit_file(
        destination,
        candidate,
        expected_source_sha256=expected_destination_sha256,
        backup_root=transaction_backup_root,
        process_checker=process_checker,
        validator=validator,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    ensure_plain_path(path, description="备份清单")
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SnapshotError("备份清单根节点无效")
    if value.get("schema") != 1 or value.get("state") != "verified":
        raise SnapshotError("备份未标记为已校验或格式版本不受支持")
    if not isinstance(value.get("created_utc"), str):
        raise SnapshotError("备份清单缺少创建时间")
    if not isinstance(value.get("source_save_directory"), str):
        raise SnapshotError("备份清单缺少来源路径")
    if not isinstance(value.get("files"), list) or not value["files"]:
        raise SnapshotError("备份清单没有文件")
    return value


def _info_from_manifest(directory: Path, manifest: dict[str, Any]) -> BackupInfo:
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return BackupInfo(
        directory=directory,
        created_utc=str(manifest["created_utc"]),
        source_save_directory=str(manifest["source_save_directory"]),
        file_count=len(manifest["files"]),
        note=str(metadata.get("note", "")),
        operation=str(metadata.get("operation", "手动备份")),
    )
