from __future__ import annotations

import struct
import unittest

from bfmod.huffman import encode_container
from bfmod.save_slot import SaveSlotFormatError, decode_save_slot, encode_save_slot


def _build_slot(main_data: bytes, attachments: tuple[bytes, bytes, bytes, bytes]) -> bytes:
    encoded_main = encode_container(main_data)
    version, decoded_size = struct.unpack_from("<II", encoded_main)
    main_payload = encoded_main[8:]
    sizes = (len(main_payload), *(len(item) for item in attachments))
    return struct.pack("<7I", version, decoded_size, *sizes) + main_payload + b"".join(attachments)


class SaveSlotTests(unittest.TestCase):
    def test_decodes_main_data_and_preserves_all_attachments(self) -> None:
        main_data = bytes(range(256)) * 4
        attachments = (b"image", b"metadata", b"thumbnail", b"optional")
        encoded = _build_slot(main_data, attachments)

        slot = decode_save_slot(encoded)

        self.assertEqual(slot.version, 100)
        self.assertEqual(slot.main_data, main_data)
        self.assertEqual(slot.attachments, attachments)
        self.assertEqual(slot.encoded_size, len(encoded))

    def test_round_trip_preserves_slot_bytes_when_main_data_is_unchanged(self) -> None:
        encoded = _build_slot(b"mostly zero\0" * 100, (b"a", b"b" * 3, b"c" * 5, b""))
        slot = decode_save_slot(encoded)

        rebuilt = encode_save_slot(slot, slot.main_data)

        self.assertEqual(decode_save_slot(rebuilt).main_data, slot.main_data)
        self.assertEqual(decode_save_slot(rebuilt).attachments, slot.attachments)

    def test_replacing_main_data_does_not_change_attachments(self) -> None:
        attachments = (b"screen-data", b"slot-metadata", b"thumbnail-data", b"extra-data")
        slot = decode_save_slot(_build_slot(b"before", attachments))

        rebuilt = decode_save_slot(encode_save_slot(slot, b"after!"))

        self.assertEqual(rebuilt.main_data, b"after!")
        self.assertEqual(rebuilt.attachments, attachments)

    def test_rejects_truncated_header_or_mismatched_block_sizes(self) -> None:
        with self.assertRaisesRegex(SaveSlotFormatError, "头部"):
            decode_save_slot(b"\0" * 27)

        encoded = bytearray(_build_slot(b"main", (b"a", b"b", b"c", b"d")))
        struct.pack_into("<I", encoded, 12, 999_999)
        with self.assertRaisesRegex(SaveSlotFormatError, "文件长度"):
            decode_save_slot(bytes(encoded))

    def test_rejects_trailing_bytes_not_declared_by_header(self) -> None:
        encoded = _build_slot(b"main", (b"a", b"b", b"c", b"d")) + b"unexpected"
        with self.assertRaisesRegex(SaveSlotFormatError, "文件长度"):
            decode_save_slot(encoded)


if __name__ == "__main__":
    unittest.main()
