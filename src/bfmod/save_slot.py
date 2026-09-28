"""普通存档槽位容器的严格解析和主数据替换。"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .huffman import CONTAINER_VERSION, HuffmanFormatError, decode_container, encode_container

SLOT_HEADER_SIZE = 7 * 4
ATTACHMENT_COUNT = 4
MAX_BLOCK_SIZE = 64 * 1024 * 1024


class SaveSlotFormatError(ValueError):
    """槽位头、分块长度或主数据无效。"""


@dataclass(frozen=True)
class SaveSlot:
    version: int
    main_data: bytes
    main_payload: bytes
    attachments: tuple[bytes, bytes, bytes, bytes]
    encoded_size: int

    @property
    def weapon_progress_attachment(self) -> bytes:
        """返回已证明承载武器成长块的 attachment 1（第二个附属块）。"""

        return self.attachments[1]


def decode_save_slot(encoded: bytes) -> SaveSlot:
    """解析 28 字节槽位头，解码主数据并原样保留四个附属块。"""

    if len(encoded) < SLOT_HEADER_SIZE:
        raise SaveSlotFormatError("存档短于 28 字节槽位头部")
    version, decoded_size, main_size, *attachment_sizes = struct.unpack_from(
        "<7I", encoded
    )
    if version != CONTAINER_VERSION:
        raise SaveSlotFormatError(f"不支持的存档版本 {version}")
    sizes = (main_size, *attachment_sizes)
    if any(size > MAX_BLOCK_SIZE for size in sizes):
        raise SaveSlotFormatError("存档分块长度超出安全上限")
    declared_size = SLOT_HEADER_SIZE + sum(sizes)
    if declared_size != len(encoded):
        raise SaveSlotFormatError(
            f"存档头声明的文件长度 {declared_size} 与实际长度 {len(encoded)} 不同"
        )

    position = SLOT_HEADER_SIZE
    main_payload = encoded[position : position + main_size]
    position += main_size
    attachments: list[bytes] = []
    for size in attachment_sizes:
        attachments.append(encoded[position : position + size])
        position += size

    try:
        main_container = decode_container(
            struct.pack("<II", version, decoded_size) + main_payload,
            expected_size=decoded_size,
        )
    except HuffmanFormatError as error:
        raise SaveSlotFormatError(f"存档主数据无效：{error}") from error
    consumed_size = (main_container.consumed_bits + 7) // 8
    if consumed_size != len(main_payload):
        raise SaveSlotFormatError(
            f"存档主数据含 {len(main_payload) - consumed_size} 字节未声明尾部数据"
        )

    return SaveSlot(
        version=version,
        main_data=main_container.decoded,
        main_payload=main_payload,
        attachments=(attachments[0], attachments[1], attachments[2], attachments[3]),
        encoded_size=len(encoded),
    )


def encode_save_slot(slot: SaveSlot, main_data: bytes) -> bytes:
    """重新编码主数据，并逐字节保留所有不透明附属块。"""

    encoded_main = encode_container(main_data)
    version, decoded_size = struct.unpack_from("<II", encoded_main)
    main_payload = encoded_main[8:]
    sizes = (len(main_payload), *(len(block) for block in slot.attachments))
    return (
        struct.pack("<7I", version, decoded_size, *sizes)
        + main_payload
        + b"".join(slot.attachments)
    )


def replace_save_slot_attachment(slot: SaveSlot, index: int, attachment: bytes) -> bytes:
    """替换一个附属块，同时逐字节保留主数据载荷和其他附属块。"""

    if not 0 <= index < ATTACHMENT_COUNT:
        raise IndexError(f"附属块索引必须在 0..{ATTACHMENT_COUNT - 1} 之间")
    attachments = list(slot.attachments)
    attachments[index] = attachment
    sizes = (
        len(slot.main_payload),
        *(len(block) for block in attachments),
    )
    return (
        struct.pack("<7I", slot.version, len(slot.main_data), *sizes)
        + slot.main_payload
        + b"".join(attachments)
    )
