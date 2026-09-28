"""桌面界面使用的加载、预览和安全应用服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backups import (
    BackupInfo,
    create_backup,
    restore_backup_file,
    verify_backup_compatibility,
)
from .editing import EditorSession
from .force_crash import ForceCrashSession
from .save_slot import decode_save_slot
from .storage import (
    CommitResult,
    StorageError,
    commit_file,
    recover_transactions,
    running_processes,
)
from .versioning import identify_installation
from .weapon_progress import (
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    WeaponProgressUpdate,
    decode_weapon_progress_attachment,
)
from .workspace import GameWorkspace, LoadedSlot


class ApplicationError(RuntimeError):
    """应用服务拒绝不安全或不受支持的操作。"""


@dataclass(frozen=True)
class ApplyResult:
    backup: BackupInfo
    commit: CommitResult


@dataclass(frozen=True)
class RestoreResult:
    safety_backup: BackupInfo
    commit: CommitResult


def apply_force_crash_session(
    workspace: GameWorkspace,
    loaded_slot: LoadedSlot,
    session: ForceCrashSession,
    *,
    backup_root: str | Path,
    note: str = "",
) -> ApplyResult:
    """备份并安全提交一个槽位的 Force Crash 解锁草稿。"""

    summary = [
        {
            "force_crash_number": change.force_crash.number,
            "record_index": change.force_crash.record_index,
            "name_ja": change.force_crash.name_ja,
        }
        for change in session.changes
    ]
    return _apply_progress_session(
        workspace,
        loaded_slot,
        session,
        backup_root=backup_root,
        note=note,
        operation_name="Force Crash 解锁",
        summary=summary,
    )


def create_manual_backup(
    workspace: GameWorkspace,
    *,
    backup_root: str | Path,
    note: str = "",
) -> BackupInfo:
    """重新识别版本和检查游戏状态后创建可恢复的完整 Save 快照。"""

    current_identity = identify_installation(workspace.game_root)
    active = running_processes(("BaldrForce.exe", "Start-BaldrForce.exe"))
    if active:
        raise ApplicationError(f"请先退出游戏：{', '.join(active)}")
    return create_backup(
        workspace.game_root / "Save",
        Path(backup_root) / "snapshots",
        operation="手动完整备份",
        note=note,
        metadata={"installation_fingerprints": current_identity.fingerprints},
    )


def apply_session(
    workspace: GameWorkspace,
    loaded_slot: LoadedSlot,
    session: EditorSession,
    *,
    backup_root: str | Path,
    note: str = "",
) -> ApplyResult:
    """重新检查全部门禁，创建完整快照，再提交一个普通槽位。"""

    summary = [
        {
            "official_number": change.weapon.official_number,
            "name_ja": change.weapon.name_ja,
        }
        for change in session.changes
    ]
    return _apply_progress_session(
        workspace,
        loaded_slot,
        session,
        backup_root=backup_root,
        note=note,
        operation_name="武器修改",
        summary=summary,
    )


def _apply_progress_session(
    workspace: GameWorkspace,
    loaded_slot: LoadedSlot,
    session: EditorSession | ForceCrashSession,
    *,
    backup_root: str | Path,
    note: str,
    operation_name: str,
    summary: list[dict[str, object]],
) -> ApplyResult:
    """共享版本门禁、完整备份、候选白名单验证和事务提交。"""

    if loaded_slot not in workspace.slots:
        raise ApplicationError("所选槽位不属于当前工作区")
    if not loaded_slot.is_editable:
        raise ApplicationError("系统特殊槽位尚未证明语义，首版只读")
    if not session.changes:
        raise ApplicationError("没有待应用修改")

    current_identity = identify_installation(workspace.game_root)
    if not current_identity.write_supported:
        raise ApplicationError(
            "游戏版本不再匹配已验证版本：" + "；".join(current_identity.reasons)
        )
    active = running_processes(("BaldrForce.exe", "Start-BaldrForce.exe"))
    if active:
        raise ApplicationError(f"请先退出游戏：{', '.join(active)}")

    updates = session.progress_updates()
    candidate = session.render_save(loaded_slot.slot)
    _validate_candidate(loaded_slot, updates, candidate)
    root = Path(backup_root)
    unresolved = tuple(
        record
        for record in recover_transactions(
            root / "transactions",
            save_directory=workspace.game_root / "Save",
        )
        if record.state == "needs_attention"
    )
    if unresolved:
        raise ApplicationError(
            "存在无法自动核对的历史事务，禁止新的写入："
            + "；".join(record.message for record in unresolved)
        )
    backup = create_backup(
        workspace.game_root / "Save",
        root / "snapshots",
        operation=f"应用 {loaded_slot.path.name} {operation_name}",
        note=note,
        metadata={
            "changes": summary,
            "installation_fingerprints": current_identity.fingerprints,
        },
    )
    try:
        commit = commit_file(
            loaded_slot.path,
            candidate,
            expected_source_sha256=loaded_slot.sha256,
            backup_root=root / "transactions",
            validator=lambda data: _validate_candidate(loaded_slot, updates, data),
        )
    except StorageError:
        raise
    return ApplyResult(backup, commit)


def restore_slot(
    workspace: GameWorkspace,
    loaded_slot: LoadedSlot,
    backup_directory: str | Path,
    *,
    backup_root: str | Path,
    expected_backup_sha256: str,
) -> RestoreResult:
    """恢复一个普通槽位；恢复前先保存当前整份 Save 的一致性快照。"""

    if loaded_slot not in workspace.slots or not loaded_slot.is_editable:
        raise ApplicationError("只能恢复当前工作区中的普通存档槽位")
    current_identity = identify_installation(workspace.game_root)
    if not current_identity.write_supported:
        raise ApplicationError("未知游戏版本禁止恢复到真实安装")
    verify_backup_compatibility(backup_directory, current_identity.fingerprints)
    active = running_processes(("BaldrForce.exe", "Start-BaldrForce.exe"))
    if active:
        raise ApplicationError(f"请先退出游戏：{', '.join(active)}")

    backup_path = Path(backup_directory).resolve(strict=True) / "Save" / loaded_slot.path.name
    if not backup_path.is_file():
        raise ApplicationError(f"所选备份不含 {loaded_slot.path.name}")
    try:
        _validate_restorable_slot(backup_path.read_bytes())
    except ValueError as error:
        raise ApplicationError(f"备份中的目标槽位结构无效：{error}") from error

    root = Path(backup_root)
    unresolved = tuple(
        record
        for record in recover_transactions(
            root / "transactions",
            save_directory=workspace.game_root / "Save",
        )
        if record.state == "needs_attention"
    )
    if unresolved:
        raise ApplicationError("存在无法自动核对的历史事务，禁止恢复")
    safety_backup = create_backup(
        workspace.game_root / "Save",
        root / "snapshots",
        operation=f"恢复前保护 {loaded_slot.path.name}",
        note=f"即将从 {Path(backup_directory).name} 恢复",
        metadata={"installation_fingerprints": current_identity.fingerprints},
    )
    commit = restore_backup_file(
        backup_directory,
        loaded_slot.path.name,
        loaded_slot.path,
        expected_destination_sha256=loaded_slot.sha256,
        expected_candidate_sha256=expected_backup_sha256,
        transaction_backup_root=root / "transactions",
        expected_fingerprints=current_identity.fingerprints,
        validator=_validate_restorable_slot,
    )
    return RestoreResult(safety_backup, commit)


def _validate_restorable_slot(candidate: bytes) -> None:
    """确认恢复候选仍是含已证明武器成长块的普通槽位。"""

    backup_slot = decode_save_slot(candidate)
    decode_weapon_progress_attachment(
        backup_slot.weapon_progress_attachment,
        record_count=213,
    )


def _validate_candidate(
    loaded_slot: LoadedSlot,
    updates: tuple[WeaponProgressUpdate, ...],
    candidate: bytes,
) -> None:
    rebuilt = decode_save_slot(candidate)
    original = loaded_slot.slot
    if rebuilt.main_payload != original.main_payload:
        raise ApplicationError("候选存档意外改变了主数据载荷")
    if (
        rebuilt.attachments[0] != original.attachments[0]
        or rebuilt.attachments[2:] != original.attachments[2:]
    ):
        raise ApplicationError("候选存档意外改变了非武器附属块")
    progress = decode_weapon_progress_attachment(
        rebuilt.weapon_progress_attachment,
        record_count=213,
    )
    allowed_offsets: set[int] = set()
    for update in updates:
        start = PROGRESS_RECORD_OFFSET + update.record_index * PROGRESS_RECORD_SIZE
        if update.development_state is not None:
            allowed_offsets.add(start)
        if update.level_index is not None:
            allowed_offsets.add(start + 1)
        if update.level_progress is not None:
            allowed_offsets.update(range(start + 0x0C, start + 0x10))
        if update.level_progress_baseline is not None:
            allowed_offsets.update(range(start + 0x10, start + 0x14))
        if update.heat_reduction is not None:
            allowed_offsets.update(range(start + 0x14, start + 0x16))
    changed_offsets = {
        offset
        for offset, (before, after) in enumerate(
            zip(loaded_slot.progress.decoded, progress.decoded, strict=True)
        )
        if before != after
    }
    if not changed_offsets.issubset(allowed_offsets):
        unexpected = sorted(changed_offsets - allowed_offsets)
        raise ApplicationError(
            "候选存档改变了字段白名单之外的成长数据："
            + ", ".join(hex(offset) for offset in unexpected[:8])
        )
    updates_by_index = {update.record_index: update for update in updates}
    for index, (before, after) in enumerate(
        zip(loaded_slot.progress.records, progress.records, strict=True)
    ):
        record_update = updates_by_index.get(index)
        if record_update is None:
            if after.raw != before.raw:
                raise ApplicationError(f"候选存档意外改变成长记录 {index}")
            continue
        for field_name in (
            "development_state",
            "level_index",
            "level_progress",
            "level_progress_baseline",
            "heat_reduction",
        ):
            target = getattr(record_update, field_name)
            if target is not None and getattr(after, field_name) != target:
                raise ApplicationError(
                    f"候选存档的成长记录 {index} 字段 {field_name} 与草稿不一致"
                )
