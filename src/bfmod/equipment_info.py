"""解析 ``EquipmentInfo.DAT`` 中已由程序代码证明的定长说明记录。"""

from __future__ import annotations

from dataclasses import dataclass

EQUIPMENT_RECORD_SIZE = 0xF0
PLAYER_EQUIPMENT_RECORD_COUNT = 0x40
EXTRA_EQUIPMENT_RECORD_COUNT = 0x15
EQUIPMENT_RECORD_COUNT = (
    PLAYER_EQUIPMENT_RECORD_COUNT + EXTRA_EQUIPMENT_RECORD_COUNT
)
EQUIPMENT_INFO_SIZE = EQUIPMENT_RECORD_COUNT * EQUIPMENT_RECORD_SIZE

# BFWT 官方表只定义编号 0..62。程序会复制 64 条玩家区记录，但当前资源中的
# 第 63 条为空；在找到代码或游戏行为证据前，不能把它虚构成可编辑武器。
OFFICIAL_WEAPON_COUNT = 63
RESERVED_PLAYER_RECORD_INDEX = 63
EXTRA_EQUIPMENT_START = PLAYER_EQUIPMENT_RECORD_COUNT


class EquipmentInfoFormatError(ValueError):
    """装备说明表长度或 CP932 文本不合法。"""


@dataclass(frozen=True)
class EquipmentInfoRecord:
    index: int
    description_ja: str
    raw: bytes


@dataclass(frozen=True)
class EquipmentInfoTable:
    records: tuple[EquipmentInfoRecord, ...]

    @property
    def official_weapon_records(self) -> tuple[EquipmentInfoRecord, ...]:
        return self.records[:OFFICIAL_WEAPON_COUNT]

    @property
    def reserved_player_record(self) -> EquipmentInfoRecord:
        return self.records[RESERVED_PLAYER_RECORD_INDEX]

    @property
    def extra_records(self) -> tuple[EquipmentInfoRecord, ...]:
        return self.records[EXTRA_EQUIPMENT_START:]


def parse_equipment_info(data: bytes) -> EquipmentInfoTable:
    """解析 64 条玩家区和 21 条附加区说明，不推测其余字段语义。"""

    if len(data) != EQUIPMENT_INFO_SIZE:
        raise EquipmentInfoFormatError(
            f"EquipmentInfo.DAT 应为 {EQUIPMENT_INFO_SIZE} 字节，实际为 {len(data)}"
        )

    records: list[EquipmentInfoRecord] = []
    for index in range(EQUIPMENT_RECORD_COUNT):
        start = index * EQUIPMENT_RECORD_SIZE
        raw = data[start : start + EQUIPMENT_RECORD_SIZE]
        text_bytes = raw.split(b"\0", 1)[0].rstrip(b" ")
        try:
            description = text_bytes.decode("cp932", errors="strict")
        except UnicodeDecodeError as error:
            raise EquipmentInfoFormatError(
                f"EquipmentInfo.DAT 第 {index} 条记录不是有效 CP932 文本"
            ) from error
        records.append(EquipmentInfoRecord(index, description, raw))

    return EquipmentInfoTable(tuple(records))
