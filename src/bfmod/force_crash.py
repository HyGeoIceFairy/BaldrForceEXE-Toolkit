"""Force Crash 目录、解锁草稿与语义预设。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .save_slot import SaveSlot, replace_save_slot_attachment
from .weapon_progress import (
    DEVELOPMENT_COMPLETE_STATE,
    WeaponProgressRecord,
    WeaponProgressUpdate,
    edit_weapon_progress_attachment,
)

FORCE_CRASH_RECORD_START = 192
FORCE_CRASH_COUNT = 21
PROGRESS_RECORD_COUNT = 213
FORCE_CRASH_PRESET_SCHEMA = 1


class ForceCrashError(ValueError):
    """Force Crash 目录、选择或预设不满足已证明映射。"""


@dataclass(frozen=True)
class ForceCrashDefinition:
    number: int
    record_index: int
    name_ja: str
    description_ja: str
    progress: WeaponProgressRecord

    @property
    def is_unlocked(self) -> bool:
        return self.progress.development_state == DEVELOPMENT_COMPLETE_STATE


@dataclass(frozen=True)
class ForceCrashChange:
    force_crash: ForceCrashDefinition
    original_state: int
    target_state: int


class ForceCrashSession:
    """单个槽位的 Force Crash 解锁草稿；批量操作只占一个撤销步骤。"""

    def __init__(self, force_crashes: tuple[ForceCrashDefinition, ...]) -> None:
        if len(force_crashes) != FORCE_CRASH_COUNT:
            raise ForceCrashError("Force Crash 目录必须包含 21 项")
        for number, definition in enumerate(force_crashes):
            expected_record = FORCE_CRASH_RECORD_START + number
            if (
                definition.number != number
                or definition.record_index != expected_record
                or definition.progress.index != expected_record
            ):
                raise ForceCrashError(
                    "Force Crash 必须按编号 0..20 映射到成长记录 192..212"
                )

        self._force_crashes = force_crashes
        self._targets: dict[int, int] = {}
        self._undo: list[dict[int, int]] = []
        self._redo: list[dict[int, int]] = []

    @property
    def force_crashes(self) -> tuple[ForceCrashDefinition, ...]:
        return self._force_crashes

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def changes(self) -> tuple[ForceCrashChange, ...]:
        return tuple(
            ForceCrashChange(
                self._force_crashes[number],
                self._force_crashes[number].progress.development_state,
                target,
            )
            for number, target in sorted(self._targets.items())
            if target != self._force_crashes[number].progress.development_state
        )

    def target_state_for(self, number: int) -> int:
        self._validate_numbers((number,))
        return self._targets.get(
            number,
            self._force_crashes[number].progress.development_state,
        )

    def stage_unlock(self, numbers: tuple[int, ...]) -> None:
        selected = self._validate_numbers(numbers)
        targets = dict(self._targets)
        for number in selected:
            original = self._force_crashes[number].progress.development_state
            if original == DEVELOPMENT_COMPLETE_STATE:
                targets.pop(number, None)
            else:
                targets[number] = DEVELOPMENT_COMPLETE_STATE
        self._replace_targets(targets)

    def discard_changes(self, numbers: tuple[int, ...]) -> int:
        selected = self._validate_numbers(numbers)
        targets = dict(self._targets)
        removed = sum(number in targets for number in selected)
        for number in selected:
            targets.pop(number, None)
        self._replace_targets(targets)
        return removed

    def discard_all(self) -> None:
        self._replace_targets({})

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(dict(self._targets))
        self._targets = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(dict(self._targets))
        self._targets = self._redo.pop()
        return True

    def progress_updates(self) -> tuple[WeaponProgressUpdate, ...]:
        return tuple(
            WeaponProgressUpdate(
                record_index=change.force_crash.record_index,
                development_state=change.target_state,
            )
            for change in self.changes
        )

    def render_save(self, slot: SaveSlot) -> bytes:
        updates = self.progress_updates()
        if not updates:
            raise ForceCrashError("没有待应用修改")
        attachment = edit_weapon_progress_attachment(
            slot.weapon_progress_attachment,
            record_count=PROGRESS_RECORD_COUNT,
            updates=updates,
        )
        return replace_save_slot_attachment(slot, 1, attachment)

    def export_preset(self, *, name: str) -> str:
        if not self.changes:
            raise ForceCrashError("没有可导出的 Force Crash 修改")
        payload = {
            "schema": FORCE_CRASH_PRESET_SCHEMA,
            "kind": "force_crash",
            "name": name.strip() or "未命名 Force Crash 预设",
            "force_crashes": [change.force_crash.number for change in self.changes],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def import_preset(self, text: str) -> None:
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise ForceCrashError(f"Force Crash 预设不是有效 JSON：{error.msg}") from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != FORCE_CRASH_PRESET_SCHEMA
            or payload.get("kind") != "force_crash"
        ):
            raise ForceCrashError("不支持的 Force Crash 预设格式")
        numbers = payload.get("force_crashes")
        if (
            not isinstance(numbers, list)
            or not numbers
            or any(not isinstance(number, int) for number in numbers)
        ):
            raise ForceCrashError("Force Crash 预设没有有效项目")
        selected = self._validate_numbers(tuple(numbers))
        if len(selected) != len(numbers):
            raise ForceCrashError("Force Crash 预设包含重复项目")
        self.stage_unlock(selected)

    def _replace_targets(self, targets: dict[int, int]) -> None:
        if targets == self._targets:
            return
        self._undo.append(dict(self._targets))
        self._targets = targets
        self._redo.clear()

    @staticmethod
    def _validate_numbers(numbers: tuple[int, ...]) -> tuple[int, ...]:
        if not numbers:
            raise ForceCrashError("至少选择一个 Force Crash")
        unique = tuple(dict.fromkeys(numbers))
        if any(not 0 <= number < FORCE_CRASH_COUNT for number in unique):
            raise ForceCrashError("Force Crash 编号超出 0..20")
        return unique
