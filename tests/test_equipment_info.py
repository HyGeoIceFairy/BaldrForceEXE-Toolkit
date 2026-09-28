from __future__ import annotations

import unittest

from bfmod.equipment_info import (
    EQUIPMENT_INFO_SIZE,
    EQUIPMENT_RECORD_SIZE,
    EXTRA_EQUIPMENT_START,
    OFFICIAL_WEAPON_COUNT,
    RESERVED_PLAYER_RECORD_INDEX,
    EquipmentInfoFormatError,
    parse_equipment_info,
)


def _record(text: str = "") -> bytes:
    encoded = text.encode("cp932")
    if len(encoded) > EQUIPMENT_RECORD_SIZE:
        raise ValueError("测试文本超出记录长度")
    return encoded.ljust(EQUIPMENT_RECORD_SIZE, b" ")


def _equipment_info_blob() -> bytes:
    records = [_record(f"公式武器説明 {index}") for index in range(OFFICIAL_WEAPON_COUNT)]
    records.append(bytes(EQUIPMENT_RECORD_SIZE))
    records.extend(
        _record(f"追加兵装説明 {index}")
        for index in range(EXTRA_EQUIPMENT_START, 85)
    )
    blob = b"".join(records)
    assert len(blob) == EQUIPMENT_INFO_SIZE
    return blob


class EquipmentInfoTests(unittest.TestCase):
    def test_parses_proven_record_layout_without_inventing_record_63_semantics(self) -> None:
        table = parse_equipment_info(_equipment_info_blob())

        self.assertEqual(len(table.records), 85)
        self.assertEqual(len(table.official_weapon_records), 63)
        self.assertEqual(table.official_weapon_records[0].description_ja, "公式武器説明 0")
        self.assertEqual(table.official_weapon_records[-1].index, 62)
        self.assertEqual(table.reserved_player_record.index, RESERVED_PLAYER_RECORD_INDEX)
        self.assertEqual(table.reserved_player_record.description_ja, "")
        self.assertEqual(table.extra_records[0].index, EXTRA_EQUIPMENT_START)
        self.assertEqual(len(table.extra_records), 21)

    def test_preserves_every_raw_record_for_diagnostics(self) -> None:
        blob = _equipment_info_blob()
        table = parse_equipment_info(blob)

        self.assertEqual(b"".join(record.raw for record in table.records), blob)

    def test_rejects_wrong_total_size(self) -> None:
        with self.assertRaisesRegex(EquipmentInfoFormatError, "20400"):
            parse_equipment_info(_equipment_info_blob()[:-1])

    def test_rejects_invalid_cp932_content(self) -> None:
        blob = bytearray(_equipment_info_blob())
        blob[0:2] = b"\x81\x00"

        with self.assertRaisesRegex(EquipmentInfoFormatError, "第 0 条"):
            parse_equipment_info(bytes(blob))


if __name__ == "__main__":
    unittest.main()
