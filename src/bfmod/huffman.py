"""游戏版本 100 存档容器使用的静态 Huffman 编解码。"""

from __future__ import annotations

import heapq
import struct
from collections import Counter
from dataclasses import dataclass
from typing import TypeAlias

CONTAINER_VERSION = 100
MAX_TREE_DEPTH = 256
MAX_DECODED_SIZE = 32 * 1024 * 1024

Tree: TypeAlias = int | tuple["Tree", "Tree"]


class HuffmanFormatError(ValueError):
    """容器头、Huffman 树或位流不合法。"""


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.bit_offset = 0

    def read_bit(self) -> int:
        if self.bit_offset >= len(self._data) * 8:
            raise HuffmanFormatError("Huffman 位流意外结束")
        value = (self._data[self.bit_offset // 8] >> (7 - self.bit_offset % 8)) & 1
        self.bit_offset += 1
        return value

    def read_byte(self) -> int:
        value = 0
        for _ in range(8):
            value = (value << 1) | self.read_bit()
        return value


class _BitWriter:
    def __init__(self) -> None:
        self._data = bytearray()
        self.bit_offset = 0

    def write_bit(self, value: int) -> None:
        if value not in (0, 1):
            raise ValueError("位只能是 0 或 1")
        if self.bit_offset % 8 == 0:
            self._data.append(0)
        if value:
            self._data[-1] |= 1 << (7 - self.bit_offset % 8)
        self.bit_offset += 1

    def write_byte(self, value: int) -> None:
        if not 0 <= value <= 0xFF:
            raise ValueError("字节超出范围")
        for shift in range(7, -1, -1):
            self.write_bit((value >> shift) & 1)

    def to_bytes(self) -> bytes:
        return bytes(self._data)


@dataclass(frozen=True)
class HuffmanContainer:
    version: int
    decoded: bytes
    encoded_size: int
    consumed_bits: int
    leaf_count: int


def _read_tree(
    reader: _BitReader,
    *,
    depth: int = 0,
    leaves: set[int] | None = None,
) -> Tree:
    if depth > MAX_TREE_DEPTH:
        raise HuffmanFormatError("Huffman 树过深")
    if leaves is None:
        leaves = set()

    if reader.read_bit() == 0:
        symbol = reader.read_byte()
        if symbol in leaves:
            raise HuffmanFormatError(f"Huffman 树含重复叶子 0x{symbol:02X}")
        leaves.add(symbol)
        return symbol

    if len(leaves) >= 256:
        raise HuffmanFormatError("Huffman 树叶子数量超出字节符号范围")
    return (
        _read_tree(reader, depth=depth + 1, leaves=leaves),
        _read_tree(reader, depth=depth + 1, leaves=leaves),
    )


def _write_tree(writer: _BitWriter, tree: Tree) -> None:
    if isinstance(tree, int):
        writer.write_bit(0)
        writer.write_byte(tree)
        return
    writer.write_bit(1)
    _write_tree(writer, tree[0])
    _write_tree(writer, tree[1])


def _build_tree(data: bytes) -> Tree:
    counts = Counter(data)
    if not counts:
        return 0

    heap: list[tuple[int, int, int, Tree]] = []
    sequence = 0
    for symbol, frequency in sorted(counts.items()):
        heapq.heappush(heap, (frequency, symbol, sequence, symbol))
        sequence += 1

    while len(heap) > 1:
        left = heapq.heappop(heap)
        right = heapq.heappop(heap)
        tree: Tree = (left[3], right[3])
        heapq.heappush(
            heap,
            (left[0] + right[0], min(left[1], right[1]), sequence, tree),
        )
        sequence += 1
    return heap[0][3]


def _build_codes(tree: Tree) -> dict[int, tuple[int, ...]]:
    codes: dict[int, tuple[int, ...]] = {}

    def visit(node: Tree, prefix: tuple[int, ...]) -> None:
        if isinstance(node, int):
            codes[node] = prefix
            return
        visit(node[0], prefix + (0,))
        visit(node[1], prefix + (1,))

    visit(tree, ())
    return codes


def decode_container(
    encoded: bytes,
    *,
    expected_size: int | None = None,
    max_decoded_size: int = MAX_DECODED_SIZE,
) -> HuffmanContainer:
    """解码游戏的版本 100 Huffman 容器，并执行严格边界检查。"""

    if len(encoded) < 10:
        raise HuffmanFormatError("容器短于最小头和树长度")
    version, decoded_size = struct.unpack_from("<II", encoded, 0)
    if version != CONTAINER_VERSION:
        raise HuffmanFormatError(f"不支持的容器版本 {version}")
    if expected_size is not None and decoded_size != expected_size:
        raise HuffmanFormatError(
            f"解码长度 0x{decoded_size:X} 与预期 0x{expected_size:X} 不同"
        )
    if decoded_size > max_decoded_size:
        raise HuffmanFormatError(f"解码长度 {decoded_size} 超出安全上限")

    reader = _BitReader(encoded[8:])
    leaves: set[int] = set()
    tree = _read_tree(reader, leaves=leaves)
    output = bytearray(decoded_size)
    for index in range(decoded_size):
        node = tree
        while not isinstance(node, int):
            node = node[reader.read_bit()]
        output[index] = node

    return HuffmanContainer(
        version=version,
        decoded=bytes(output),
        encoded_size=len(encoded),
        consumed_bits=reader.bit_offset,
        leaf_count=len(leaves),
    )


def encode_container(decoded: bytes) -> bytes:
    """把原始数据编码为游戏可读取的版本 100 Huffman 容器。"""

    if len(decoded) > MAX_DECODED_SIZE:
        raise ValueError(f"输入长度 {len(decoded)} 超出安全上限")
    tree = _build_tree(decoded)
    codes = _build_codes(tree)
    writer = _BitWriter()
    _write_tree(writer, tree)
    for symbol in decoded:
        for bit in codes[symbol]:
            writer.write_bit(bit)
    return struct.pack("<II", CONTAINER_VERSION, len(decoded)) + writer.to_bytes()

