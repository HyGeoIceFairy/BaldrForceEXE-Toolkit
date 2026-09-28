from __future__ import annotations

import unittest
from struct import pack

from bfmod.waz import WazFormatError, parse_waz


def _fixed_record(name: str, *, child_count: int) -> bytes:
    name_bytes = name.encode("cp932")
    return (
        name_bytes
        + b"\0"
        + b"\xcc" * (0x28 - len(name_bytes) - 1)
        + b"\0" * (0x6A - 0x28)
        + bytes([child_count, 0xCC])
    )


def _child_record(*, detail_count: int) -> bytes:
    result = bytearray(b"\xcc" * 0x18C)
    result[0x188] = detail_count
    return bytes(result)


def _waz_blob() -> bytes:
    first = (
        _fixed_record("武器A", child_count=1)
        + _child_record(detail_count=2)
        + bytes(range(0x30))
        + bytes(range(0x30))
    )
    second = _fixed_record("武器B(Lv2)", child_count=0)
    return pack("<H", 2) + first + second


class WazTests(unittest.TestCase):
    def test_parses_proven_nested_layout_and_preserves_raw_bytes(self) -> None:
        original = _waz_blob()

        archive = parse_waz(original)

        self.assertEqual(archive.declared_record_count, 2)
        self.assertEqual([record.name for record in archive.records], ["武器A", "武器B(Lv2)"])
        self.assertEqual(archive.records[0].file_offset, 2)
        self.assertEqual(archive.records[0].base_heat, 0)
        self.assertEqual(archive.records[0].normal_minimum_heat, 0)
        self.assertEqual(len(archive.records[0].children), 1)
        self.assertEqual(len(archive.records[0].children[0].details), 2)
        self.assertEqual(archive.records[0].children[0].detail_count, 2)
        self.assertEqual(archive.records[1].child_count, 0)
        self.assertEqual(archive.raw, original)
        self.assertEqual(
            pack("<H", archive.declared_record_count)
            + b"".join(record.raw for record in archive.records),
            original,
        )

    def test_rejects_truncated_fixed_record(self) -> None:
        with self.assertRaisesRegex(WazFormatError, "第 0 条记录固定区不完整"):
            parse_waz(pack("<H", 1) + b"\0" * 8)

    def test_rejects_truncated_child_or_detail_record(self) -> None:
        child_truncated = pack("<H", 1) + _fixed_record("A", child_count=1)
        with self.assertRaisesRegex(WazFormatError, "第 0 条记录的第 0 个子记录不完整"):
            parse_waz(child_truncated)

        detail_truncated = (
            pack("<H", 1)
            + _fixed_record("A", child_count=1)
            + _child_record(detail_count=1)
            + b"\0" * 4
        )
        with self.assertRaisesRegex(WazFormatError, "第 0 条记录的第 0 个子记录明细不完整"):
            parse_waz(detail_truncated)

    def test_rejects_invalid_cp932_name_and_trailing_data(self) -> None:
        invalid_name = bytearray(_fixed_record("A", child_count=0))
        invalid_name[:2] = b"\x81\x00"
        with self.assertRaisesRegex(WazFormatError, "名称不是有效 CP932 文本"):
            parse_waz(pack("<H", 1) + invalid_name)

        with self.assertRaisesRegex(WazFormatError, "存在 1 字节尾随数据"):
            parse_waz(_waz_blob() + b"\0")

    def test_preserves_cp932_character_ending_in_cc_and_requires_terminator(self) -> None:
        name_ending_in_cc = bytearray(_fixed_record("の", child_count=0))
        archive = parse_waz(pack("<H", 1) + name_ending_in_cc)
        self.assertEqual(archive.records[0].name, "の")

        missing_terminator = bytearray(_fixed_record("A", child_count=0))
        missing_terminator[:0x28] = b"A" * 0x28
        with self.assertRaisesRegex(WazFormatError, "缺少 NUL 终止符"):
            parse_waz(pack("<H", 1) + missing_terminator)

    def test_rejects_negative_signed_child_and_detail_counts(self) -> None:
        negative_children = bytearray(_fixed_record("A", child_count=0))
        negative_children[0x6A] = 0xFF
        with self.assertRaisesRegex(WazFormatError, "子记录数为负数"):
            parse_waz(pack("<H", 1) + negative_children)

        negative_details = bytearray(_child_record(detail_count=0))
        negative_details[0x188] = 0xFF
        with self.assertRaisesRegex(WazFormatError, "明细数为负数"):
            parse_waz(
                pack("<H", 1)
                + _fixed_record("A", child_count=1)
                + negative_details
            )

    def test_rejects_short_header(self) -> None:
        with self.assertRaisesRegex(WazFormatError, "缺少 2 字节记录数"):
            parse_waz(b"\0")


if __name__ == "__main__":
    unittest.main()
