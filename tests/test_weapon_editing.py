from __future__ import annotations

import unittest
from struct import pack, pack_into

from bfmod.huffman import encode_container
from bfmod.save_slot import decode_save_slot, replace_save_slot_attachment
from bfmod.weapon_progress import (
    DEVELOPMENT_COMPLETE_STATE,
    PROGRESS_BLOCK_SIZE,
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    WeaponProgressFormatError,
    WeaponProgressUpdate,
    decode_weapon_progress_attachment,
    edit_weapon_progress_attachment,
)


def _progress_attachment() -> bytes:
    decoded = bytearray((index * 37 + 11) % 256 for index in range(PROGRESS_BLOCK_SIZE))
    for record_index in range(213):
        offset = PROGRESS_RECORD_OFFSET + record_index * PROGRESS_RECORD_SIZE
        decoded[offset] = 1
        decoded[offset + 1] = 0
        pack_into("<i", decoded, offset + 0x04, record_index)
        pack_into("<i", decoded, offset + 0x08, record_index * 10)
        pack_into("<i", decoded, offset + 0x0C, record_index * 100)
        pack_into("<i", decoded, offset + 0x10, record_index * 100)
        pack_into("<h", decoded, offset + 0x14, record_index % 100)
    return encode_container(bytes(decoded))[8:]


def _save_slot(attachments: tuple[bytes, bytes, bytes, bytes]) -> bytes:
    main_container = encode_container(bytes(range(251)) * 8)
    version, decoded_size = 100, 251 * 8
    main_payload = main_container[8:]
    sizes = (len(main_payload), *(len(item) for item in attachments))
    return pack("<7I", version, decoded_size, *sizes) + main_payload + b"".join(attachments)


class WeaponProgressEditingTests(unittest.TestCase):
    def test_edits_only_whitelisted_bytes_and_preserves_unknown_record_bytes(self) -> None:
        encoded = _progress_attachment()
        before = decode_weapon_progress_attachment(encoded, record_count=213)

        edited_encoded = edit_weapon_progress_attachment(
            encoded,
            record_count=213,
            updates=(
                WeaponProgressUpdate(
                    record_index=6,
                    development_state=DEVELOPMENT_COMPLETE_STATE,
                    level_index=2,
                    level_progress=0,
                    level_progress_baseline=0,
                    heat_reduction=130,
                ),
            ),
        )
        after = decode_weapon_progress_attachment(edited_encoded, record_count=213)

        self.assertEqual(after.records[6].development_state, DEVELOPMENT_COMPLETE_STATE)
        self.assertEqual(after.records[6].level_index, 2)
        self.assertEqual(after.records[6].level_progress, 0)
        self.assertEqual(after.records[6].level_progress_baseline, 0)
        self.assertEqual(after.records[6].heat_reduction, 130)

        changed_offsets = {
            index
            for index, (old, new) in enumerate(zip(before.decoded, after.decoded, strict=True))
            if old != new
        }
        record_start = PROGRESS_RECORD_OFFSET + 6 * PROGRESS_RECORD_SIZE
        allowed_offsets = {
            record_start,
            record_start + 1,
            *range(record_start + 0x0C, record_start + 0x16),
        }
        self.assertLessEqual(changed_offsets, allowed_offsets)

    def test_no_updates_returns_original_encoded_bytes(self) -> None:
        encoded = _progress_attachment()
        self.assertIs(
            edit_weapon_progress_attachment(encoded, record_count=213, updates=()),
            encoded,
        )

    def test_rejects_duplicate_records_and_values_outside_proven_ranges(self) -> None:
        encoded = _progress_attachment()
        invalid_cases = (
            (WeaponProgressUpdate(record_index=-1, development_state=5),),
            (WeaponProgressUpdate(record_index=213, development_state=5),),
            (WeaponProgressUpdate(record_index=0, level_index=3),),
            (WeaponProgressUpdate(record_index=0, heat_reduction=131),),
            (WeaponProgressUpdate(record_index=0, development_state=2),),
            (
                WeaponProgressUpdate(record_index=0, development_state=5),
                WeaponProgressUpdate(record_index=0, heat_reduction=10),
            ),
        )
        for updates in invalid_cases:
            with self.subTest(updates=updates):
                with self.assertRaises(WeaponProgressFormatError):
                    edit_weapon_progress_attachment(
                        encoded, record_count=213, updates=updates
                    )

    def test_replaces_only_weapon_attachment_without_reencoding_main_data(self) -> None:
        attachment = _progress_attachment()
        source = _save_slot((b"first", attachment, b"third", b"fourth"))
        slot = decode_save_slot(source)
        edited_attachment = edit_weapon_progress_attachment(
            attachment,
            record_count=213,
            updates=(WeaponProgressUpdate(record_index=0, development_state=5),),
        )

        rebuilt = replace_save_slot_attachment(slot, 1, edited_attachment)
        decoded = decode_save_slot(rebuilt)

        self.assertEqual(decoded.main_payload, slot.main_payload)
        self.assertEqual(decoded.main_data, slot.main_data)
        self.assertEqual(decoded.attachments[0], slot.attachments[0])
        self.assertEqual(decoded.attachments[1], edited_attachment)
        self.assertEqual(decoded.attachments[2:], slot.attachments[2:])

    def test_rejects_invalid_attachment_index(self) -> None:
        slot = decode_save_slot(
            _save_slot((b"first", _progress_attachment(), b"third", b"fourth"))
        )
        with self.assertRaisesRegex(IndexError, "附属块"):
            replace_save_slot_attachment(slot, 4, b"bad")


if __name__ == "__main__":
    unittest.main()
