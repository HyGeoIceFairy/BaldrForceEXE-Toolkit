from __future__ import annotations

import unittest
from dataclasses import dataclass
from struct import pack_into

from bfmod.huffman import encode_container
from bfmod.weapon_progress import (
    PROGRESS_BLOCK_SIZE,
    PROGRESS_RECORD_OFFSET,
    WeaponProgressFormatError,
    decode_weapon_progress_attachment,
)


@dataclass(frozen=True)
class _ProgressRecordFields:
    development_state: int
    level_index: int
    weapon_use_count: int
    development_exp: int
    level_progress: int
    level_progress_baseline: int
    heat_reduction: int


def _encoded_attachment(
    records: list[_ProgressRecordFields],
) -> bytes:
    decoded = bytearray(PROGRESS_BLOCK_SIZE)
    for index, record in enumerate(records):
        offset = PROGRESS_RECORD_OFFSET + index * 0x18
        decoded[offset] = record.development_state
        decoded[offset + 1] = record.level_index
        pack_into("<i", decoded, offset + 0x04, record.weapon_use_count)
        pack_into("<i", decoded, offset + 0x08, record.development_exp)
        pack_into("<i", decoded, offset + 0x0C, record.level_progress)
        pack_into("<i", decoded, offset + 0x10, record.level_progress_baseline)
        pack_into("<h", decoded, offset + 0x14, record.heat_reduction)
    return encode_container(bytes(decoded))[8:]


class WeaponProgressTests(unittest.TestCase):
    def test_decodes_proven_progress_fields_without_rewriting_attachment(self) -> None:
        encoded = _encoded_attachment(
            [
                _ProgressRecordFields(5, 0, 5, 40, 1151, 845, 2),
                _ProgressRecordFields(2, 2, 8, 52, 10_000, 581, 80),
            ]
        )

        table = decode_weapon_progress_attachment(encoded, record_count=2)

        self.assertEqual(len(table.records), 2)
        self.assertEqual(table.records[0].development_state, 5)
        self.assertTrue(table.records[0].is_development_complete)
        self.assertEqual(table.records[0].level_index, 0)
        self.assertEqual(table.records[0].weapon_use_count, 5)
        self.assertEqual(table.records[0].development_exp, 40)
        self.assertEqual(table.records[0].level_progress, 1151)
        self.assertEqual(table.records[0].level_progress_baseline, 845)
        self.assertEqual(table.records[0].heat_reduction, 2)
        self.assertEqual(table.records[0].effective_heat(5), 3)
        self.assertFalse(table.records[1].is_development_complete)
        self.assertTrue(table.records[1].is_development_available)
        self.assertEqual(table.records[1].reported_level_progress, 10_000)
        self.assertEqual(table.records[1].effective_heat(90), 30)
        self.assertEqual(table.encoded, encoded)
        self.assertEqual(len(table.prefix), PROGRESS_RECORD_OFFSET)
        self.assertEqual(
            len(table.suffix),
            PROGRESS_BLOCK_SIZE - PROGRESS_RECORD_OFFSET - 2 * 0x18,
        )

    def test_rejects_wrong_decoded_size_or_impossible_record_count(self) -> None:
        encoded = _encoded_attachment([])
        with self.assertRaisesRegex(WeaponProgressFormatError, "记录数"):
            decode_weapon_progress_attachment(encoded, record_count=10_000)

    def test_rejects_trailing_encoded_data(self) -> None:
        with self.assertRaisesRegex(WeaponProgressFormatError, "未声明尾部数据"):
            decode_weapon_progress_attachment(
                _encoded_attachment([]) + b"\0",
                record_count=0,
            )


if __name__ == "__main__":
    unittest.main()
