"""带备份、并发检测和恢复记录的单存档安全提交。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .snapshot import (
    SnapshotError,
    ensure_plain_path,
    ensure_plain_path_chain,
    sha256_file,
)

DEFAULT_GAME_PROCESS_NAMES = ("BaldrForce.exe", "Start-BaldrForce.exe")


class StorageError(RuntimeError):
    """存档提交无法满足安全前置条件或完成校验。"""


class ConcurrentModificationError(StorageError):
    """源存档在载入后被外部进程修改。"""


class GameRunningError(StorageError):
    """检测到游戏仍在运行，禁止离线覆盖。"""


@dataclass(frozen=True)
class CommitResult:
    source: Path
    backup_directory: Path
    transaction_path: Path
    original_sha256: str
    final_sha256: str


@dataclass(frozen=True)
class RecoveryRecord:
    transaction_path: Path
    source: Path | None
    state: str
    message: str


def running_processes(process_names: Iterable[str]) -> tuple[str, ...]:
    """使用 Windows tasklist 返回正在运行的目标进程名。"""

    wanted = {name.casefold() for name in process_names}
    if not wanted:
        return ()
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StorageError(f"无法可靠检查游戏进程：{error}") from error
    found: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('","', 1)[0].strip('"')
        if name.casefold() in wanted:
            found.add(name)
    return tuple(sorted(found, key=str.casefold))


def commit_file(
    source: str | Path,
    candidate: bytes,
    *,
    expected_source_sha256: str,
    backup_root: str | Path,
    game_process_names: tuple[str, ...] = DEFAULT_GAME_PROCESS_NAMES,
    process_checker: Callable[[Iterable[str]], tuple[str, ...]] = running_processes,
    validator: Callable[[bytes], None] | None = None,
) -> CommitResult:
    """验证候选和源指纹，创建完整备份，再以同目录临时文件替换源文件。"""

    source_input = Path(source)
    backup_root_input = Path(backup_root)
    try:
        ensure_plain_path_chain(source_input.parent, description="存档目录")
        ensure_plain_path(source_input, description="提交目标")
        ensure_plain_path_chain(
            backup_root_input,
            description="事务备份目录",
            allow_missing=True,
        )
    except SnapshotError as error:
        raise StorageError(str(error)) from error
    source_path = source_input.resolve(strict=True)
    if not source_path.is_file():
        raise StorageError(f"提交目标不是文件：{source_path}")
    backup_root_path = backup_root_input.resolve(strict=False)
    _validate_backup_location(source_path.parent, backup_root_path)

    active = process_checker(game_process_names)
    if active:
        raise GameRunningError(f"游戏仍在运行：{', '.join(active)}")
    expected = expected_source_sha256.upper()
    actual = sha256_file(source_path)
    if actual != expected:
        raise ConcurrentModificationError("源存档在载入后已发生变化，请重新载入")
    if not candidate:
        raise StorageError("候选存档为空")
    if validator is not None:
        try:
            validator(candidate)
        except Exception as error:
            raise StorageError(f"候选存档验证失败：{error}") from error

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_directory = backup_root_path / f"{timestamp}-{uuid.uuid4().hex[:8]}"
    backup_directory.mkdir(parents=True, exist_ok=False)
    backup_path = backup_directory / source_path.name
    shutil.copy2(source_path, backup_path)
    if sha256_file(backup_path) != expected:
        raise StorageError("备份哈希校验失败；源存档未修改")

    transaction_path = backup_directory / "transaction.json"
    transaction = {
        "schema": 1,
        "state": "prepared",
        "created_utc": datetime.now(UTC).isoformat(),
        "source": str(source_path),
        "backup": str(backup_path),
        "original_sha256": expected,
        "candidate_sha256": _sha256_bytes(candidate),
    }
    _write_json_sync(transaction_path, transaction)
    prepared_transaction_text = transaction_path.read_text(encoding="utf-8")

    temporary_path = source_path.with_name(
        f".{source_path.name}.bfmod-{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary_path.open("xb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        if validator is not None:
            validator(temporary_path.read_bytes())
        try:
            ensure_plain_path_chain(source_path.parent, description="存档目录")
            ensure_plain_path(source_path, description="提交目标")
        except SnapshotError as error:
            raise StorageError(str(error)) from error
        if sha256_file(source_path) != expected:
            raise ConcurrentModificationError("源存档在提交期间发生变化；未执行替换")
        active = process_checker(game_process_names)
        if active:
            raise GameRunningError(f"游戏仍在运行：{', '.join(active)}")
        try:
            ensure_plain_path_chain(source_input.parent, description="存档目录")
            ensure_plain_path(source_input, description="提交目标")
            ensure_plain_path_chain(backup_root_input, description="事务备份根目录")
            ensure_plain_path_chain(backup_directory, description="事务备份目录")
            ensure_plain_path(backup_path, description="事务备份文件")
            ensure_plain_path(transaction_path, description="事务清单")
        except SnapshotError as error:
            raise StorageError(str(error)) from error
        try:
            if source_input.resolve(strict=True) != source_path:
                raise StorageError("存档目录在提交期间发生变化；未执行替换")
            if backup_root_input.resolve(strict=True) != backup_root_path:
                raise StorageError("事务备份根目录在提交期间发生变化；未执行替换")
            if backup_directory.resolve(strict=True).parent != backup_root_path:
                raise StorageError("事务备份目录在提交期间越界；未执行替换")
        except OSError as error:
            raise StorageError(f"提交边界在最终复核时不可用：{error}") from error
        if sha256_file(source_path) != expected:
            raise ConcurrentModificationError("源存档在提交期间发生变化；未执行替换")
        if sha256_file(backup_path) != expected:
            raise StorageError("事务备份哈希在提交期间发生变化；未执行替换")
        if sha256_file(temporary_path) != transaction["candidate_sha256"]:
            raise StorageError("临时候选存档在提交期间发生变化；未执行替换")
        if transaction_path.read_text(encoding="utf-8") != prepared_transaction_text:
            raise StorageError("事务清单在提交期间发生变化；未执行替换")
        os.replace(temporary_path, source_path)
        final_hash = sha256_file(source_path)
        if final_hash != transaction["candidate_sha256"]:
            raise StorageError("替换后哈希校验失败，请从事务备份恢复")
        if validator is not None:
            final_data = source_path.read_bytes()
            if _sha256_bytes(final_data) != transaction["candidate_sha256"]:
                raise StorageError("替换后存档在验证前发生变化，请从事务备份恢复")
            validator(final_data)
        transaction["state"] = "committed"
        transaction["committed_utc"] = datetime.now(UTC).isoformat()
        _write_json_sync(transaction_path, transaction)
        committed_transaction_text = transaction_path.read_text(encoding="utf-8")
        _resolve_confirmed_transaction(
            transaction_path,
            committed_transaction_text,
            source_path,
            final_hash,
            backup_path,
            expected,
            root=backup_root_path,
            save_root=source_path.parent,
        )
        return CommitResult(
            source_path,
            backup_directory,
            transaction_path,
            expected,
            final_hash,
        )
    except (ConcurrentModificationError, StorageError):
        raise
    except Exception as error:
        raise StorageError(f"存档替换失败，已保留备份与恢复记录：{error}") from error
    finally:
        try:
            if temporary_path.exists():
                temporary_path.unlink()
        except OSError:
            pass


def recover_transactions(
    backup_root: str | Path,
    *,
    save_directory: str | Path,
) -> tuple[RecoveryRecord, ...]:
    """按源/候选哈希核对遗留事务；明确完成或未替换时自动解除记录。"""

    root_input = Path(backup_root)
    save_input = Path(save_directory)
    try:
        ensure_plain_path_chain(
            root_input,
            description="事务备份根目录",
            allow_missing=True,
        )
        ensure_plain_path_chain(save_input, description="存档根目录")
    except SnapshotError as error:
        return (
            RecoveryRecord(
                root_input,
                None,
                "needs_attention",
                f"事务根目录无法安全核对：{error}",
            ),
        )
    root = root_input.resolve(strict=False)
    save_root = save_input.resolve(strict=True)
    if not save_root.is_dir():
        return (
            RecoveryRecord(
                root_input,
                None,
                "needs_attention",
                "事务根目录无法安全核对：存档根路径不是目录",
            ),
        )
    if not root.is_dir():
        return ()
    records: list[RecoveryRecord] = []
    transaction_directories = sorted(
        {
            path.parent
            for pattern in (
                "*/transaction.json",
                "*/transaction.resolving.json",
            )
            for path in root.glob(pattern)
        }
    )
    for transaction_directory in transaction_directories:
        active_path = transaction_directory / "transaction.json"
        resolving_path = transaction_directory / "transaction.resolving.json"
        transaction_path = active_path
        try:
            existing_paths = tuple(
                path for path in (active_path, resolving_path) if path.exists()
            )
            if len(existing_paths) != 1:
                raise ValueError("同一事务目录中存在多个活动清单")
            transaction_path = existing_paths[0]
            ensure_plain_path(transaction_directory, description="事务目录")
            ensure_plain_path(transaction_path, description="事务清单")
            manifest_text = transaction_path.read_text(encoding="utf-8")
            payload = json.loads(manifest_text)
            if not isinstance(payload, dict) or payload.get("schema") != 1:
                raise ValueError("事务格式版本无效")
            source_value = payload.get("source")
            original_hash = payload.get("original_sha256")
            candidate_hash = payload.get("candidate_sha256")
            backup_value = payload.get("backup")
            if not all(
                isinstance(value, str)
                for value in (source_value, original_hash, candidate_hash, backup_value)
            ):
                raise ValueError("事务字段不完整")
            source_input = Path(str(source_value))
            backup_input = Path(str(backup_value))
            ensure_plain_path_chain(source_input, description="事务源文件")
            ensure_plain_path_chain(backup_input, description="事务备份文件")
            source = source_input.resolve(strict=True)
            backup = backup_input.resolve(strict=True)
            source.relative_to(save_root)
            backup.relative_to(transaction_directory.resolve(strict=True))
            if source.parent != save_root:
                raise ValueError("事务源文件不在声明的 Save 根目录内")
            if backup.parent != transaction_directory.resolve(strict=True):
                raise ValueError("事务备份文件不在事务目录内")
            if backup.name != source.name:
                raise ValueError("事务备份文件名与源槽位不一致")
            if not source.is_file() or not backup.is_file():
                raise ValueError("事务源文件或备份缺失")
            if sha256_file(backup) != str(original_hash).upper():
                raise ValueError("事务备份哈希不符")
            source_hash = sha256_file(source)
            if source_hash == str(original_hash).upper():
                _resolve_confirmed_transaction(
                    transaction_path,
                    manifest_text,
                    source,
                    source_hash,
                    backup,
                    str(original_hash).upper(),
                    root=root,
                    save_root=save_root,
                )
                records.append(
                    RecoveryRecord(
                        transaction_path,
                        source,
                        "not_committed",
                        "源文件仍为提交前版本，事务已安全解除",
                    )
                )
            elif source_hash == str(candidate_hash).upper():
                _resolve_confirmed_transaction(
                    transaction_path,
                    manifest_text,
                    source,
                    source_hash,
                    backup,
                    str(original_hash).upper(),
                    root=root,
                    save_root=save_root,
                )
                records.append(
                    RecoveryRecord(
                        transaction_path,
                        source,
                        "committed",
                        "源文件已是候选版本，事务已确认完成",
                    )
                )
            else:
                records.append(
                    RecoveryRecord(
                        transaction_path,
                        source,
                        "needs_attention",
                        "源文件既不是原版本也不是候选版本，禁止自动处理",
                    )
                )
        except Exception as error:
            records.append(
                RecoveryRecord(
                    transaction_path,
                    None,
                    "needs_attention",
                    f"事务无法安全核对：{error}",
                )
            )
    return tuple(records)


def _resolve_confirmed_transaction(
    transaction_path: Path,
    manifest_text: str,
    source: Path,
    expected_source_hash: str,
    backup: Path,
    expected_backup_hash: str,
    *,
    root: Path,
    save_root: Path,
) -> None:
    """原子认领事务，复核后转为不再参与恢复扫描的审计记录。"""

    active_path = transaction_path.parent / "transaction.json"
    resolving_path = transaction_path.parent / "transaction.resolving.json"
    resolved_path = transaction_path.parent / "transaction.resolved.json"
    if transaction_path == active_path:
        if resolving_path.exists() or resolved_path.exists():
            raise ValueError("事务目录已存在其他状态的清单")
        os.replace(active_path, resolving_path)
    elif transaction_path != resolving_path:
        raise ValueError("事务清单名称无效")

    try:
        _validate_claimed_transaction(
            resolving_path,
            manifest_text,
            source,
            expected_source_hash,
            backup,
            expected_backup_hash,
            root=root,
            save_root=save_root,
        )
        os.replace(resolving_path, resolved_path)
    except Exception:
        if resolving_path.exists():
            _reactivate_transaction(resolving_path, active_path)
        raise


def _validate_claimed_transaction(
    claimed_path: Path,
    manifest_text: str,
    source: Path,
    expected_source_hash: str,
    backup: Path,
    expected_backup_hash: str,
    *,
    root: Path,
    save_root: Path,
) -> None:
    """确认被认领清单及其两份数据仍处于已核对状态。"""

    ensure_plain_path_chain(root, description="事务备份根目录")
    ensure_plain_path_chain(claimed_path.parent, description="事务目录")
    ensure_plain_path(claimed_path, description="事务清单")
    ensure_plain_path_chain(source, description="事务源文件")
    ensure_plain_path_chain(backup, description="事务备份文件")
    claimed_path.parent.resolve(strict=True).relative_to(root.resolve(strict=True))
    if source.resolve(strict=True).parent != save_root.resolve(strict=True):
        raise ValueError("事务源文件在解除前越出声明的 Save 根目录")
    if claimed_path.read_text(encoding="utf-8") != manifest_text:
        raise ValueError("事务清单在核对期间发生变化")
    if sha256_file(source) != expected_source_hash:
        raise ValueError("事务源文件在核对期间发生变化")
    if sha256_file(backup) != expected_backup_hash:
        raise ValueError("事务备份文件在核对期间发生变化")


def _reactivate_transaction(claimed_path: Path, active_path: Path) -> None:
    """复核失败时恢复活动清单，绝不覆盖并发出现的另一份记录。"""

    if active_path.exists():
        raise ValueError("事务解除期间出现了另一份活动清单")
    os.replace(claimed_path, active_path)


def _validate_backup_location(save_directory: Path, backup_root: Path) -> None:
    save_resolved = save_directory.resolve(strict=True)
    backup_resolved = backup_root.resolve(strict=False)
    try:
        backup_resolved.relative_to(save_resolved)
    except ValueError:
        pass
    else:
        raise StorageError("备份目录不能位于游戏存档目录内部")
    try:
        save_resolved.relative_to(backup_resolved)
    except ValueError:
        return
    raise StorageError("备份目录不能包含整个游戏存档目录")


def _write_json_sync(path: Path, data: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()
