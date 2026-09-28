"""只读解析普通槽位 attachment 1（第二个附属块）的武器成长记录。"""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack_into, unpack_from

from .huffman import (
    CONTAINER_VERSION,
    HuffmanFormatError,
    decode_container,
    encode_container,
)
from .integer_math import signed_divide_by_three

PROGRESS_BLOCK_SIZE = 0x2934
PROGRESS_RECORD_OFFSET = 0x10C
PROGRESS_RECORD_SIZE = 0x18
DEVELOPMENT_AVAILABLE_STATE = 2
DEVELOPMENT_COMPLETE_STATE = 5
MAX_LEVEL_INDEX = 2
MAX_LEVEL_PROGRESS = 10_000
MAX_HEAT_REDUCTION = 130


class WeaponProgressFormatError(ValueError):
    """武器成长附属容器的边界、长度或记录数不合法。"""


@dataclass(frozen=True)
class WeaponProgressRecord:
    index: int
    development_state: int
    level_index: int
    weapon_use_count: int
    development_exp: int
    level_progress: int
    level_progress_baseline: int
    heat_reduction: int
    raw: bytes

    @property
    def is_development_available(self) -> bool:
        """是否处于开发界面允许进入开发确认流程的状态 2。"""

        return self.development_state == DEVELOPMENT_AVAILABLE_STATE

    @property
    def is_development_complete(self) -> bool:
        """是否处于程序完成开发时写入的状态 5。"""

        return self.development_state == DEVELOPMENT_COMPLETE_STATE

    @property
    def reported_level_progress(self) -> int:
        """返回界面数据函数报告的成长值；最高等级固定报告 10000。"""

        return self.level_progress if self.level_index < 2 else 10_000

    def effective_heat(self, base_heat: int) -> int:
        """复现游戏的 ``max(base - reduction, base / 3)`` 正常热度公式。"""

        return max(
            base_heat - self.heat_reduction,
            signed_divide_by_three(base_heat),
        )


@dataclass(frozen=True)
class WeaponProgressUpdate:
    """一条只触及已证明字段的成长记录更新。"""

    record_index: int
    development_state: int | None = None
    level_index: int | None = None
    level_progress: int | None = None
    level_progress_baseline: int | None = None
    heat_reduction: int | None = None


@dataclass(frozen=True)
class WeaponProgressTable:
    records: tuple[WeaponProgressRecord, ...]
    prefix: bytes
    suffix: bytes
    decoded: bytes
    encoded: bytes


def decode_weapon_progress_attachment(
    encoded: bytes,
    *,
    record_count: int,
) -> WeaponProgressTable:
    """解码嵌套 Huffman 块并读取已由代码证明的 ``0x18`` 字节记录。"""

    if record_count < 0:
        raise WeaponProgressFormatError("武器成长记录数不能为负数")
    records_end = PROGRESS_RECORD_OFFSET + record_count * PROGRESS_RECORD_SIZE
    if records_end > PROGRESS_BLOCK_SIZE:
        raise WeaponProgressFormatError("武器成长记录数超出固定块容量")

    try:
        container = decode_container(
            CONTAINER_VERSION.to_bytes(4, "little")
            + PROGRESS_BLOCK_SIZE.to_bytes(4, "little")
            + encoded,
            expected_size=PROGRESS_BLOCK_SIZE,
        )
    except HuffmanFormatError as error:
        raise WeaponProgressFormatError(f"武器成长块无效：{error}") from error

    consumed_size = (container.consumed_bits + 7) // 8
    if consumed_size != len(encoded):
        raise WeaponProgressFormatError(
            f"武器成长块含 {len(encoded) - consumed_size} 字节未声明尾部数据"
        )

    decoded = container.decoded
    records = tuple(
        _parse_record(decoded, index) for index in range(record_count)
    )
    return WeaponProgressTable(
        records,
        decoded[:PROGRESS_RECORD_OFFSET],
        decoded[records_end:],
        decoded,
        encoded,
    )


def edit_weapon_progress_attachment(
    encoded: bytes,
    *,
    record_count: int,
    updates: tuple[WeaponProgressUpdate, ...],
) -> bytes:
    """更新已证明字段并重新编码成长块；空更新保持原字节对象。"""

    if not updates:
        return encoded
    table = decode_weapon_progress_attachment(encoded, record_count=record_count)
    seen: set[int] = set()
    decoded = bytearray(table.decoded)

    for update in updates:
        _validate_update(update, record_count=record_count)
        if update.record_index in seen:
            raise WeaponProgressFormatError(
                f"武器成长记录 {update.record_index} 被重复更新"
            )
        seen.add(update.record_index)
        start = PROGRESS_RECORD_OFFSET + update.record_index * PROGRESS_RECORD_SIZE
        if update.development_state is not None:
            decoded[start] = update.development_state
        if update.level_index is not None:
            decoded[start + 1] = update.level_index
        if update.level_progress is not None:
            pack_into("<i", decoded, start + 0x0C, update.level_progress)
        if update.level_progress_baseline is not None:
            pack_into("<i", decoded, start + 0x10, update.level_progress_baseline)
        if update.heat_reduction is not None:
            pack_into("<h", decoded, start + 0x14, update.heat_reduction)

    return encode_container(bytes(decoded))[8:]


def _validate_update(update: WeaponProgressUpdate, *, record_count: int) -> None:
    if not 0 <= update.record_index < record_count:
        raise WeaponProgressFormatError("武器成长记录索引超出范围")
    if (
        update.development_state is not None
        and update.development_state != DEVELOPMENT_COMPLETE_STATE
    ):
        raise WeaponProgressFormatError("只允许把开发状态写为已证明的开发完成状态 5")
    if update.level_index is not None and not 0 <= update.level_index <= MAX_LEVEL_INDEX:
        raise WeaponProgressFormatError("等级索引必须在 0..2 之间")
    for field_name, value in (
        ("等级成长值", update.level_progress),
        ("等级成长结算基线", update.level_progress_baseline),
    ):
        if value is not None and not 0 <= value <= MAX_LEVEL_PROGRESS:
            raise WeaponProgressFormatError(f"{field_name}必须在 0..10000 之间")
    if update.heat_reduction is not None and not 0 <= update.heat_reduction <= MAX_HEAT_REDUCTION:
        raise WeaponProgressFormatError("降热量必须在 0..130 之间")


def _parse_record(decoded: bytes, index: int) -> WeaponProgressRecord:
    start = PROGRESS_RECORD_OFFSET + index * PROGRESS_RECORD_SIZE
    raw = decoded[start : start + PROGRESS_RECORD_SIZE]
    return WeaponProgressRecord(
        index=index,
        development_state=raw[0],
        level_index=raw[1],
        weapon_use_count=unpack_from("<i", raw, 0x04)[0],
        development_exp=unpack_from("<i", raw, 0x08)[0],
        level_progress=unpack_from("<i", raw, 0x0C)[0],
        level_progress_baseline=unpack_from("<i", raw, 0x10)[0],
        heat_reduction=unpack_from("<h", raw, 0x14)[0],
        raw=raw,
    )
