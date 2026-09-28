"""只读解析游戏的 PACw 归档和 4 KiB LZSS 载荷。"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PAC_MAGIC = b"PACw"
PAC_HEADER_SIZE = 12
PAC_ENTRY_SIZE = 76
MAX_ENTRY_COUNT = 100_000
MAX_UNPACKED_SIZE = 512 * 1024 * 1024


class PacFormatError(ValueError):
    """PAC 索引或 LZSS 数据不合法。"""


@dataclass(frozen=True)
class PacEntry:
    name: str
    offset: int
    unpacked_size: int
    packed_size: int


@dataclass(frozen=True)
class LzssResult:
    data: bytes
    consumed_size: int
    packed_size: int

    @property
    def trailing_size(self) -> int:
        return self.packed_size - self.consumed_size


class PacArchive:
    def __init__(self, data: bytes) -> None:
        if len(data) < PAC_HEADER_SIZE or data[:4] != PAC_MAGIC:
            raise PacFormatError("文件不是 PACw 归档")
        count, self.version = struct.unpack_from("<II", data, 4)
        if count > MAX_ENTRY_COUNT:
            raise PacFormatError(f"PAC 条目数量 {count} 超出安全上限")
        index_end = PAC_HEADER_SIZE + count * PAC_ENTRY_SIZE
        if index_end > len(data):
            raise PacFormatError("PAC 索引超出文件边界")

        entries: list[PacEntry] = []
        for index in range(count):
            offset = PAC_HEADER_SIZE + index * PAC_ENTRY_SIZE
            raw_name = data[offset : offset + 64].split(b"\0", 1)[0]
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as error:
                raise PacFormatError(f"第 {index} 项文件名不是 ASCII") from error
            data_offset, unpacked_size, packed_size = struct.unpack_from(
                "<III", data, offset + 64
            )
            if unpacked_size > MAX_UNPACKED_SIZE:
                raise PacFormatError(f"{name} 解压长度超出安全上限")
            if data_offset + packed_size > len(data):
                raise PacFormatError(f"{name} 数据超出 PAC 文件边界")
            entries.append(PacEntry(name, data_offset, unpacked_size, packed_size))

        self._data = data
        self.entries = tuple(entries)
        self._by_name = {entry.name.casefold(): entry for entry in entries}

    def find(self, name: str) -> PacEntry:
        try:
            return self._by_name[name.casefold()]
        except KeyError as error:
            raise KeyError(f"PAC 中不存在 {name}") from error

    def extract(self, entry_or_name: PacEntry | str) -> LzssResult:
        entry = self.find(entry_or_name) if isinstance(entry_or_name, str) else entry_or_name
        payload = self._data[entry.offset : entry.offset + entry.packed_size]
        if entry.packed_size == entry.unpacked_size:
            return LzssResult(payload, len(payload), len(payload))
        return decode_lzss(payload, entry.unpacked_size)


def decode_lzss(packed: bytes, expected_size: int) -> LzssResult:
    """解压游戏 PAC 使用的 Okumura 风格 LZSS，达到目标长度即停止。"""

    if not 0 <= expected_size <= MAX_UNPACKED_SIZE:
        raise PacFormatError("LZSS 目标长度超出安全范围")

    ring = bytearray(b" " * 4096)
    ring_position = 4096 - 18
    source_position = 0
    flags = 0
    output = bytearray()
    token_limit = max(expected_size + 1, len(packed) * 8 + 1)
    token_count = 0

    while len(output) < expected_size:
        token_count += 1
        if token_count > token_limit:
            raise PacFormatError("LZSS 令牌数量超出安全上限")
        flags >>= 1
        if flags & 0x100 == 0:
            if source_position >= len(packed):
                raise PacFormatError("LZSS 标志字节意外结束")
            flags = packed[source_position] | 0xFF00
            source_position += 1

        if flags & 1:
            if source_position >= len(packed):
                raise PacFormatError("LZSS 原文字节意外结束")
            value = packed[source_position]
            source_position += 1
            output.append(value)
            ring[ring_position] = value
            ring_position = (ring_position + 1) & 0xFFF
            continue

        if source_position + 1 >= len(packed):
            raise PacFormatError("LZSS 回溯令牌意外结束")
        match_position = packed[source_position]
        length_code = packed[source_position + 1]
        source_position += 2
        match_position |= (length_code & 0xF0) << 4
        match_length = (length_code & 0x0F) + 3
        for index in range(match_length):
            value = ring[(match_position + index) & 0xFFF]
            output.append(value)
            ring[ring_position] = value
            ring_position = (ring_position + 1) & 0xFFF
            if len(output) == expected_size:
                break

    return LzssResult(bytes(output), source_position, len(packed))

