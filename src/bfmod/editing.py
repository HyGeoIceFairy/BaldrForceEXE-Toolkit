"""武器编辑草稿、撤销重做和语义预设。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .save_slot import SaveSlot, replace_save_slot_attachment
from .weapon_progress import (
    DEVELOPMENT_COMPLETE_STATE,
    WeaponProgressRecord,
    WeaponProgressUpdate,
    edit_weapon_progress_attachment,
)

OFFICIAL_WEAPON_COUNT = 63
PLAYER_RECORD_STRIDE = 3
PROGRESS_RECORD_COUNT = 213
PRESET_SCHEMA = 1


class EditingError(ValueError):
    """编辑目标、预设或会话状态不合法。"""


@dataclass(frozen=True)
class EditableWeapon:
    official_number: int
    name_ja: str
    base_heat: int
    progress: WeaponProgressRecord

    @property
    def normal_minimum_heat(self) -> int:
        return int(self.base_heat / 3)

    @property
    def normal_maximum_heat_reduction(self) -> int:
        return self.base_heat - self.normal_minimum_heat


@dataclass(frozen=True)
class WeaponTarget:
    development_state: int
    level_index: int
    level_progress: int
    level_progress_baseline: int
    heat_reduction: int

    @classmethod
    def from_record(cls, record: WeaponProgressRecord) -> WeaponTarget:
        return cls(
            development_state=record.development_state,
            level_index=record.level_index,
            level_progress=record.level_progress,
            level_progress_baseline=record.level_progress_baseline,
            heat_reduction=record.heat_reduction,
        )


@dataclass(frozen=True)
class WeaponChange:
    weapon: EditableWeapon
    original: WeaponTarget
    target: WeaponTarget


class EditorSession:
    """单个存档槽位的内存草稿；一组批量操作只占一个撤销步骤。"""

    def __init__(self, weapons: tuple[EditableWeapon, ...]) -> None:
        if len(weapons) != OFFICIAL_WEAPON_COUNT:
            raise EditingError("编辑会话必须包含 63 个官方武器")
        numbers = tuple(weapon.official_number for weapon in weapons)
        if numbers != tuple(range(OFFICIAL_WEAPON_COUNT)):
            raise EditingError("官方武器编号必须为连续的 0..62")
        for weapon in weapons:
            expected_index = weapon.official_number * PLAYER_RECORD_STRIDE
            if weapon.progress.index != expected_index:
                raise EditingError(
                    f"武器 {weapon.official_number} 的成长记录不是已证明的 3*n 映射"
                )
            if weapon.base_heat < 0:
                raise EditingError("首版不支持基础热度为负的武器定义")

        self._weapons = weapons
        self._targets: dict[int, WeaponTarget] = {}
        self._undo: list[dict[int, WeaponTarget]] = []
        self._redo: list[dict[int, WeaponTarget]] = []

    @property
    def weapons(self) -> tuple[EditableWeapon, ...]:
        return self._weapons

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def changes(self) -> tuple[WeaponChange, ...]:
        changes: list[WeaponChange] = []
        for number in sorted(self._targets):
            weapon = self._weapons[number]
            original = WeaponTarget.from_record(weapon.progress)
            target = self._targets[number]
            if target != original:
                changes.append(WeaponChange(weapon, original, target))
        return tuple(changes)

    def target_for(self, official_number: int) -> WeaponTarget:
        self._validate_numbers((official_number,))
        return self._targets.get(
            official_number,
            WeaponTarget.from_record(self._weapons[official_number].progress),
        )

    def stage_unlock(self, official_numbers: tuple[int, ...]) -> None:
        self._mutate(
            official_numbers,
            lambda _weapon, target: replace(
                target, development_state=DEVELOPMENT_COMPLETE_STATE
            ),
        )

    def stage_level(
        self,
        official_numbers: tuple[int, ...],
        *,
        level_index: int,
        level_progress: int = 0,
    ) -> None:
        if not 0 <= level_index <= 2:
            raise EditingError("武器等级必须在 1..3 之间")
        if not 0 <= level_progress <= 10_000:
            raise EditingError("等级成长值必须在 0..10000 之间")
        self._mutate(
            official_numbers,
            lambda _weapon, target: replace(
                target,
                level_index=level_index,
                level_progress=level_progress,
                level_progress_baseline=level_progress,
            ),
        )

    def stage_master(self, official_numbers: tuple[int, ...]) -> None:
        self.stage_level(official_numbers, level_index=2, level_progress=0)

    def stage_normal_completion(self, official_numbers: tuple[int, ...]) -> None:
        """用一个撤销步骤完成开发、满级和正常最低热度。"""

        def complete(weapon: EditableWeapon, target: WeaponTarget) -> WeaponTarget:
            return replace(
                target,
                development_state=DEVELOPMENT_COMPLETE_STATE,
                level_index=2,
                level_progress=0,
                level_progress_baseline=0,
                heat_reduction=weapon.normal_maximum_heat_reduction,
            )

        self._mutate(official_numbers, complete)

    def stage_normal_minimum_heat(self, official_numbers: tuple[int, ...]) -> None:
        def set_minimum(weapon: EditableWeapon, target: WeaponTarget) -> WeaponTarget:
            return replace(
                target,
                heat_reduction=weapon.normal_maximum_heat_reduction,
            )

        self._mutate(official_numbers, set_minimum)

    def stage_heat_reduction(
        self, official_numbers: tuple[int, ...], *, heat_reduction: int
    ) -> None:
        numbers = self._validate_numbers(official_numbers)
        for number in numbers:
            self._validate_normal_heat_reduction(
                self._weapons[number],
                heat_reduction,
            )
        self._mutate(
            numbers,
            lambda _weapon, target: replace(
                target, heat_reduction=heat_reduction
            ),
        )

    def discard_all(self) -> None:
        if not self._targets:
            return
        self._undo.append(dict(self._targets))
        self._targets = {}
        self._redo.clear()

    def discard_changes(self, official_numbers: tuple[int, ...]) -> int:
        """将选中武器恢复为载入值，并把整批操作记作一个撤销步骤。"""

        numbers = self._validate_numbers(official_numbers)
        new_targets = dict(self._targets)
        removed = sum(number in new_targets for number in numbers)
        for number in numbers:
            new_targets.pop(number, None)
        if new_targets != self._targets:
            self._undo.append(dict(self._targets))
            self._targets = new_targets
            self._redo.clear()
        return removed

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
                record_index=change.weapon.progress.index,
                development_state=(
                    change.target.development_state
                    if change.target.development_state != change.original.development_state
                    else None
                ),
                level_index=(
                    change.target.level_index
                    if change.target.level_index != change.original.level_index
                    else None
                ),
                level_progress=(
                    change.target.level_progress
                    if change.target.level_progress != change.original.level_progress
                    else None
                ),
                level_progress_baseline=(
                    change.target.level_progress_baseline
                    if change.target.level_progress_baseline
                    != change.original.level_progress_baseline
                    else None
                ),
                heat_reduction=(
                    change.target.heat_reduction
                    if change.target.heat_reduction != change.original.heat_reduction
                    else None
                ),
            )
            for change in self.changes
        )

    def render_save(self, slot: SaveSlot) -> bytes:
        updates = self.progress_updates()
        if not updates:
            raise EditingError("没有待应用修改")
        attachment = edit_weapon_progress_attachment(
            slot.weapon_progress_attachment,
            record_count=PROGRESS_RECORD_COUNT,
            updates=updates,
        )
        return replace_save_slot_attachment(slot, 1, attachment)

    def export_preset(self, *, name: str) -> str:
        if not self.changes:
            raise EditingError("没有可导出的修改")
        payload = {
            "schema": PRESET_SCHEMA,
            "name": name.strip() or "未命名预设",
            "weapons": [
                {
                    "official_number": change.weapon.official_number,
                    "target": {
                        field_name: target_value
                        for field_name, target_value in asdict(change.target).items()
                        if target_value != getattr(change.original, field_name)
                    },
                }
                for change in self.changes
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def import_preset(self, text: str) -> None:
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise EditingError(f"预设不是有效 JSON：{error.msg}") from error
        if not isinstance(payload, dict) or payload.get("schema") != PRESET_SCHEMA:
            raise EditingError("不支持的预设格式版本")
        entries = payload.get("weapons")
        if not isinstance(entries, list) or not entries:
            raise EditingError("预设没有武器修改")

        imported: dict[int, WeaponTarget] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("official_number"), int
            ):
                raise EditingError("预设武器编号无效")
            number = entry["official_number"]
            self._validate_numbers((number,))
            if number in imported:
                raise EditingError(f"预设重复包含武器 {number}")
            target = entry.get("target")
            if not isinstance(target, dict) or not target:
                raise EditingError("预设武器目标无效")
            allowed_fields = set(WeaponTarget.__dataclass_fields__)
            if not set(target).issubset(allowed_fields):
                raise EditingError("预设包含未知武器字段")
            if any(not isinstance(value, int) for value in target.values()):
                raise EditingError("预设武器字段类型错误")
            current = self._targets.get(
                number,
                WeaponTarget.from_record(self._weapons[number].progress),
            )
            parsed = replace(current, **target)
            self._validate_target(parsed)
            if parsed.heat_reduction != current.heat_reduction:
                self._validate_normal_heat_reduction(
                    self._weapons[number],
                    parsed.heat_reduction,
                )
            original = WeaponTarget.from_record(self._weapons[number].progress)
            if parsed.development_state not in (
                original.development_state,
                DEVELOPMENT_COMPLETE_STATE,
            ):
                raise EditingError("预设只能保持开发状态或标记为开发完成")
            imported[number] = parsed

        new_targets = dict(self._targets)
        new_targets.update(imported)
        if new_targets != self._targets:
            self._undo.append(dict(self._targets))
            self._targets = new_targets
            self._redo.clear()

    def _mutate(
        self,
        official_numbers: tuple[int, ...],
        operation: Callable[[EditableWeapon, WeaponTarget], WeaponTarget],
    ) -> None:
        numbers = self._validate_numbers(official_numbers)
        new_targets = dict(self._targets)
        for number in numbers:
            weapon = self._weapons[number]
            current = new_targets.get(number, WeaponTarget.from_record(weapon.progress))
            target = operation(weapon, current)
            self._validate_target(target)
            if target.heat_reduction != current.heat_reduction:
                self._validate_normal_heat_reduction(weapon, target.heat_reduction)
            original = WeaponTarget.from_record(weapon.progress)
            if target == original:
                new_targets.pop(number, None)
            else:
                new_targets[number] = target
        if new_targets != self._targets:
            self._undo.append(dict(self._targets))
            self._targets = new_targets
            self._redo.clear()

    def _validate_numbers(self, numbers: tuple[int, ...]) -> tuple[int, ...]:
        if not numbers:
            raise EditingError("至少选择一个武器")
        unique = tuple(dict.fromkeys(numbers))
        if any(not 0 <= number < OFFICIAL_WEAPON_COUNT for number in unique):
            raise EditingError("官方武器编号超出 0..62")
        return unique

    @staticmethod
    def _validate_target(target: WeaponTarget) -> None:
        if not 0 <= target.development_state <= 0xFF:
            raise EditingError("开发状态超出范围")
        if not 0 <= target.level_index <= 2:
            raise EditingError("等级索引超出范围")
        if not 0 <= target.level_progress <= 10_000:
            raise EditingError("等级成长值超出范围")
        if not 0 <= target.level_progress_baseline <= 10_000:
            raise EditingError("等级成长结算基线超出范围")
        if not 0 <= target.heat_reduction <= 130:
            raise EditingError("降热量超出范围")

    @staticmethod
    def _validate_normal_heat_reduction(
        weapon: EditableWeapon,
        heat_reduction: int,
    ) -> None:
        maximum = min(130, weapon.normal_maximum_heat_reduction)
        if not 0 <= heat_reduction <= maximum:
            raise EditingError(
                f"武器 {weapon.official_number} 的降热量必须在 0..{maximum} 之间，"
                "不能低于游戏正常最低热度"
            )
