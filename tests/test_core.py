from __future__ import annotations

import random
import struct
import unittest

from bfmod.bfwt import parse_bfwt_html
from bfmod.huffman import HuffmanFormatError, decode_container, encode_container
from bfmod.pac import PacArchive, PacFormatError, decode_lzss


class HuffmanTests(unittest.TestCase):
    def test_round_trip_representative_inputs(self) -> None:
        randomizer = random.Random(0xBFE)
        inputs = (
            b"",
            b"\0" * 4096,
            bytes(range(256)) * 8,
            bytes(randomizer.randrange(0, 256) for _ in range(20_000)),
        )
        for original in inputs:
            with self.subTest(size=len(original)):
                encoded = encode_container(original)
                decoded = decode_container(encoded, expected_size=len(original))
                self.assertEqual(decoded.decoded, original)

    def test_rejects_short_or_wrong_version_container(self) -> None:
        with self.assertRaisesRegex(HuffmanFormatError, "最小头"):
            decode_container(b"\0" * 9)
        with self.assertRaisesRegex(HuffmanFormatError, "不支持的容器版本"):
            decode_container(struct.pack("<II", 99, 0) + b"\0\0")

    def test_rejects_unexpected_or_excessive_decoded_size(self) -> None:
        encoded = encode_container(b"abc")
        with self.assertRaisesRegex(HuffmanFormatError, "与预期"):
            decode_container(encoded, expected_size=4)
        with self.assertRaisesRegex(HuffmanFormatError, "安全上限"):
            decode_container(encoded, max_decoded_size=2)

    def test_rejects_truncated_tree_or_payload(self) -> None:
        with self.assertRaisesRegex(HuffmanFormatError, "意外结束"):
            decode_container(struct.pack("<II", 100, 1) + b"\xff\xff")
        encoded = encode_container(bytes(range(32)))
        with self.assertRaisesRegex(HuffmanFormatError, "意外结束"):
            decode_container(encoded[:-1])

class BfwtTests(unittest.TestCase):
    def test_bfwt_has_contiguous_weapon_numbers(self) -> None:
        rows = []
        for number in range(63):
            name = "アームジャブ" if number == 0 else f"武器{number}"
            cells = ("", str(number), name, "", "", "", "", "", "")
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")

        weapons = parse_bfwt_html("".join(rows).encode("cp932"))
        self.assertEqual([weapon.official_number for weapon in weapons], list(range(63)))
        self.assertEqual(weapons[0].normalized_name_ja, "アームジャブ")


class PacTests(unittest.TestCase):
    def test_uncompressed_equipment_info_extracts(self) -> None:
        payload = "出の早いパンチ".encode("cp932")
        entry_offset = 12 + 76
        name = b"EquipmentInfo.DAT".ljust(64, b"\0")
        entry = name + struct.pack("<III", entry_offset, len(payload), len(payload))
        archive = PacArchive(b"PACw" + struct.pack("<II", 1, 1) + entry + payload)
        result = archive.extract("EquipmentInfo.DAT")
        self.assertEqual(result.data, payload)
        self.assertTrue(result.data.startswith("出の早いパンチ".encode("cp932")))
        self.assertEqual(result.trailing_size, 0)

    def test_rejects_pac_entry_outside_archive(self) -> None:
        name = b"EquipmentInfo.DAT".ljust(64, b"\0")
        entry = name + struct.pack("<III", 88, 10, 10)
        with self.assertRaisesRegex(PacFormatError, "超出 PAC 文件边界"):
            PacArchive(b"PACw" + struct.pack("<II", 1, 1) + entry)

    def test_rejects_truncated_lzss_stream(self) -> None:
        with self.assertRaisesRegex(PacFormatError, "意外结束"):
            decode_lzss(b"", expected_size=1)


if __name__ == "__main__":
    unittest.main()
