"""将安装、资源、槽位与武器领域数据组合为只读工作区。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .bfwt import WeaponDefinition, load_bfwt, normalize_japanese_name
from .editing import PROGRESS_RECORD_COUNT, EditableWeapon, EditorSession
from .equipment_info import EquipmentInfoRecord, parse_equipment_info
from .force_crash import (
    FORCE_CRASH_COUNT,
    FORCE_CRASH_RECORD_START,
    ForceCrashDefinition,
    ForceCrashSession,
)
from .force_crash_translations import FORCE_CRASH_DESCRIPTIONS_ZH, FORCE_CRASH_NAMES_ZH
from .installation import validate_game_root
from .pac import PacArchive
from .reference_data import WEAPON_REQUIREMENTS
from .save_slot import SaveSlot, decode_save_slot
from .translations import WEAPON_DESCRIPTIONS_ZH, WEAPON_NAMES_ZH
from .versioning import InstallationIdentity, identify_installation
from .waz import WazArchive, parse_waz
from .weapon_progress import WeaponProgressTable, decode_weapon_progress_attachment

_SAVE_SLOT_PATTERN = re.compile(r"Save(\d{3})\.dat", re.IGNORECASE)
SPECIAL_SLOT_NUMBERS = frozenset((998, 999))


class WorkspaceError(ValueError):
    """游戏资源与存档之间不满足首版已证明的映射。"""


@dataclass(frozen=True)
class RequirementView:
    weapon_number: int
    name_zh: str
    name_ja: str
    required_exp: int
    game_required_exp: int


@dataclass(frozen=True)
class WeaponCatalogEntry:
    official_number: int
    name_zh: str
    name_ja: str
    description_zh: str
    description_ja: str
    base_heat: int
    requirements: tuple[RequirementView, ...]


@dataclass(frozen=True)
class ForceCrashCatalogEntry:
    number: int
    record_index: int
    name_ja: str
    description_ja: str
    name_zh: str = ""
    description_zh: str = ""


@dataclass(frozen=True)
class LoadedSlot:
    number: int
    path: Path
    raw: bytes
    sha256: str
    slot: SaveSlot
    progress: WeaponProgressTable
    is_special: bool

    @property
    def display_name(self) -> str:
        suffix = "（系统特殊槽位，只读）" if self.is_special else ""
        return f"存档 {self.number:03d}{suffix}"

    @property
    def is_editable(self) -> bool:
        return not self.is_special


@dataclass(frozen=True)
class SlotLoadFailure:
    number: int
    path: Path
    message: str


@dataclass(frozen=True)
class GameWorkspace:
    game_root: Path
    reference_table: Path
    identity: InstallationIdentity
    catalog: tuple[WeaponCatalogEntry, ...]
    force_crash_catalog: tuple[ForceCrashCatalogEntry, ...]
    waz: WazArchive
    slots: tuple[LoadedSlot, ...]
    slot_failures: tuple[SlotLoadFailure, ...]

    def editor_for(self, slot: LoadedSlot) -> EditorSession:
        if slot not in self.slots:
            raise WorkspaceError("存档槽位不属于当前工作区")
        weapons = tuple(
            EditableWeapon(
                entry.official_number,
                entry.name_ja,
                entry.base_heat,
                slot.progress.records[entry.official_number * 3],
            )
            for entry in self.catalog
        )
        return EditorSession(weapons)

    def force_crash_editor_for(self, slot: LoadedSlot) -> ForceCrashSession:
        if slot not in self.slots:
            raise WorkspaceError("存档槽位不属于当前工作区")
        definitions = tuple(
            ForceCrashDefinition(
                entry.number,
                entry.record_index,
                entry.name_ja,
                entry.description_ja,
                slot.progress.records[entry.record_index],
            )
            for entry in self.force_crash_catalog
        )
        return ForceCrashSession(definitions)


def load_workspace(
    game_root: str | Path,
    reference_table: str | Path | None = None,
    *,
    allow_no_healthy_slots: bool = False,
) -> GameWorkspace:
    root = validate_game_root(game_root)
    reference = (
        Path(reference_table).resolve(strict=True)
        if reference_table is not None
        else root / "BFWT.htm"
    )
    waz = parse_waz((root / "Dat" / "Waza" / "TOORU.WAZ").read_bytes())
    if len(waz.records) != PROGRESS_RECORD_COUNT:
        raise WorkspaceError("TOORU.WAZ 不是已证明的 213 条玩家武器布局")

    equipment_info = parse_equipment_info(
        PacArchive((root / "Table.pac").read_bytes()).extract("EquipmentInfo.DAT").data
    )
    definitions = load_bfwt(reference) if reference.is_file() else None
    catalog = _build_catalog(definitions, waz, equipment_info.official_weapon_records)
    force_crash_catalog = _build_force_crash_catalog(
        waz,
        equipment_info.extra_records,
    )
    slots, slot_failures = _load_slots(root / "Save")
    if not slots and not allow_no_healthy_slots:
        if slot_failures:
            raise WorkspaceError(
                "所有 SaveNNN.dat 均无法解析："
                + "；".join(
                    f"{failure.path.name}: {failure.message}"
                    for failure in slot_failures
                )
            )
        raise WorkspaceError("存档目录中没有可识别的 SaveNNN.dat")
    return GameWorkspace(
        root,
        reference,
        identify_installation(root),
        catalog,
        force_crash_catalog,
        waz,
        slots,
        slot_failures,
    )


def _build_catalog(
    definitions: tuple[WeaponDefinition, ...] | None,
    waz: WazArchive,
    descriptions: tuple[EquipmentInfoRecord, ...],
) -> tuple[WeaponCatalogEntry, ...]:
    entries: list[WeaponCatalogEntry] = []
    for official_number in range(63):
        record = waz.records[official_number * 3]
        if definitions is not None:
            definition = definitions[official_number]
            if (
                definition.official_number != official_number
                or normalize_japanese_name(record.name) != definition.normalized_name_ja
            ):
                raise WorkspaceError(
                    f"WAZ 第 {record.index} 条与官方武器 {official_number} 名称不匹配"
                )
        requirements: list[RequirementView] = []
        for required_number, required_exp in WEAPON_REQUIREMENTS[official_number]:
            requirements.append(
                RequirementView(
                    required_number,
                    WEAPON_NAMES_ZH[required_number],
                    waz.records[required_number * 3].name,
                    required_exp,
                    required_exp + 25,
                )
            )
        description = descriptions[official_number].description_ja
        entries.append(
            WeaponCatalogEntry(
                official_number,
                WEAPON_NAMES_ZH[official_number],
                record.name,
                WEAPON_DESCRIPTIONS_ZH[official_number],
                description,
                record.base_heat,
                tuple(requirements),
            )
        )
    return tuple(entries)


def _build_force_crash_catalog(
    waz: WazArchive,
    descriptions: tuple[EquipmentInfoRecord, ...],
) -> tuple[ForceCrashCatalogEntry, ...]:
    """组合已由界面代码证明的 21 条独立 Force Crash 记录。"""

    if len(descriptions) != FORCE_CRASH_COUNT:
        raise WorkspaceError("装备说明表不是已证明的 21 条 Force Crash 布局")
    entries: list[ForceCrashCatalogEntry] = []
    for number in range(FORCE_CRASH_COUNT):
        record_index = FORCE_CRASH_RECORD_START + number
        record = waz.records[record_index]
        if record.fixed_raw[0x28] != 1:
            raise WorkspaceError(
                f"WAZ 第 {record_index} 条不是已证明的独立 Force Crash 编号"
            )
        entries.append(
            ForceCrashCatalogEntry(
                number,
                record_index,
                record.name,
                descriptions[number].description_ja,
                FORCE_CRASH_NAMES_ZH[number],
                FORCE_CRASH_DESCRIPTIONS_ZH[number],
            )
        )
    return tuple(entries)


def _load_slot(path: Path, number: int) -> LoadedSlot:
    raw = path.read_bytes()
    slot = decode_save_slot(raw)
    progress = decode_weapon_progress_attachment(
        slot.weapon_progress_attachment, record_count=PROGRESS_RECORD_COUNT
    )
    return LoadedSlot(
        number,
        path,
        raw,
        hashlib.sha256(raw).hexdigest().upper(),
        slot,
        progress,
        number in SPECIAL_SLOT_NUMBERS,
    )


def _load_slots(
    save_directory: Path,
) -> tuple[tuple[LoadedSlot, ...], tuple[SlotLoadFailure, ...]]:
    """隔离损坏槽位，使健康槽位和独立功能仍可使用。"""

    slots: list[LoadedSlot] = []
    failures: list[SlotLoadFailure] = []
    for path in sorted(save_directory.glob("Save*.dat")):
        match = _SAVE_SLOT_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        number = int(match.group(1))
        try:
            slots.append(_load_slot(path, number))
        except (OSError, ValueError) as error:
            failures.append(SlotLoadFailure(number, path, str(error)))
    return tuple(slots), tuple(failures)
