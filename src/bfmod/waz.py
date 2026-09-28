"""只读解析 WAZ 的三层可变长记录，不推测战斗字段语义。"""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack_from

from .integer_math import signed_divide_by_three

WAZ_HEADER_SIZE = 2
WAZ_RECORD_FIXED_SIZE = 0x6C
WAZ_RECORD_CHILD_COUNT_OFFSET = 0x6A
WAZ_CHILD_FIXED_SIZE = 0x18C
WAZ_CHILD_DETAIL_COUNT_OFFSET = 0x188
WAZ_DETAIL_SIZE = 0x30


class WazFormatError(ValueError):
    """WAZ 记录数、边界或 CP932 名称不合法。"""


@dataclass(frozen=True)
class WazDetail:
    file_offset: int
    raw: bytes


@dataclass(frozen=True)
class WazChild:
    file_offset: int
    fixed_raw: bytes
    details: tuple[WazDetail, ...]

    @property
    def detail_count(self) -> int:
        return int(unpack_from("<b", self.fixed_raw, WAZ_CHILD_DETAIL_COUNT_OFFSET)[0])

    @property
    def raw(self) -> bytes:
        return self.fixed_raw + b"".join(detail.raw for detail in self.details)


@dataclass(frozen=True)
class WazRecord:
    index: int
    file_offset: int
    name: str
    fixed_raw: bytes
    children: tuple[WazChild, ...]

    @property
    def child_count(self) -> int:
        return int(unpack_from("<b", self.fixed_raw, WAZ_RECORD_CHILD_COUNT_OFFSET)[0])

    @property
    def base_heat(self) -> int:
        """返回程序热度计算函数在运行时偏移 ``+0x2A`` 读取的基准值。"""

        return int(unpack_from("<h", self.fixed_raw, 0x2A)[0])

    @property
    def normal_minimum_heat(self) -> int:
        """返回游戏公式对基准热度做向零除三后的正常下限。"""

        return signed_divide_by_three(self.base_heat)

    @property
    def raw(self) -> bytes:
        return self.fixed_raw + b"".join(child.raw for child in self.children)


@dataclass(frozen=True)
class WazArchive:
    declared_record_count: int
    records: tuple[WazRecord, ...]
    raw: bytes


def _take(data: bytes, position: int, size: int, message: str) -> tuple[bytes, int]:
    end = position + size
    if end > len(data):
        raise WazFormatError(message)
    return data[position:end], end


def _decode_name(fixed_raw: bytes, index: int) -> str:
    name_field = fixed_raw[:0x28]
    terminator = name_field.find(b"\0")
    if terminator < 0:
        raise WazFormatError(f"WAZ 第 {index} 条记录名称缺少 NUL 终止符")
    name_bytes = name_field[:terminator]
    try:
        return name_bytes.decode("cp932", errors="strict")
    except UnicodeDecodeError as error:
        raise WazFormatError(f"WAZ 第 {index} 条记录名称不是有效 CP932 文本") from error


def parse_waz(data: bytes) -> WazArchive:
    """按游戏读函数证明的 ``0x6C + children`` 布局完整消费 WAZ。"""

    if len(data) < WAZ_HEADER_SIZE:
        raise WazFormatError("WAZ 缺少 2 字节记录数")

    declared_record_count = unpack_from("<H", data)[0]
    position = WAZ_HEADER_SIZE
    records: list[WazRecord] = []

    for record_index in range(declared_record_count):
        record_offset = position
        fixed_raw, position = _take(
            data,
            position,
            WAZ_RECORD_FIXED_SIZE,
            f"WAZ 第 {record_index} 条记录固定区不完整",
        )
        children: list[WazChild] = []

        child_count = unpack_from("<b", fixed_raw, WAZ_RECORD_CHILD_COUNT_OFFSET)[0]
        if child_count < 0:
            raise WazFormatError(f"WAZ 第 {record_index} 条记录的子记录数为负数")

        for child_index in range(child_count):
            child_offset = position
            child_fixed_raw, position = _take(
                data,
                position,
                WAZ_CHILD_FIXED_SIZE,
                f"WAZ 第 {record_index} 条记录的第 {child_index} 个子记录不完整",
            )
            details: list[WazDetail] = []
            detail_count = unpack_from(
                "<b", child_fixed_raw, WAZ_CHILD_DETAIL_COUNT_OFFSET
            )[0]
            if detail_count < 0:
                raise WazFormatError(
                    f"WAZ 第 {record_index} 条记录的第 {child_index} 个子记录明细数为负数"
                )
            for detail_index in range(detail_count):
                detail_offset = position
                detail_raw, position = _take(
                    data,
                    position,
                    WAZ_DETAIL_SIZE,
                    f"WAZ 第 {record_index} 条记录的第 {child_index} 个子记录明细不完整"
                    f"（第 {detail_index} 条）",
                )
                details.append(WazDetail(detail_offset, detail_raw))
            children.append(
                WazChild(child_offset, child_fixed_raw, tuple(details))
            )

        records.append(
            WazRecord(
                record_index,
                record_offset,
                _decode_name(fixed_raw, record_index),
                fixed_raw,
                tuple(children),
            )
        )

    if position != len(data):
        raise WazFormatError(f"WAZ 存在 {len(data) - position} 字节尾随数据")

    return WazArchive(declared_record_count, tuple(records), data)
