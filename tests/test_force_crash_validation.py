from __future__ import annotations

import unittest
from hashlib import sha256
from json import loads
from pathlib import Path
from struct import pack_into
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from bfmod.force_crash_validation import (
    ForceCrashValidationError,
    build_force_crash_validation_record,
    inspect_force_crash_candidate,
    write_force_crash_validation_record,
)
from bfmod.huffman import encode_container
from bfmod.save_slot import decode_save_slot, replace_save_slot_attachment
from bfmod.snapshot import sha256_file
from bfmod.weapon_progress import (
    PROGRESS_BLOCK_SIZE,
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    WeaponProgressTable,
    WeaponProgressUpdate,
    decode_weapon_progress_attachment,
    edit_weapon_progress_attachment,
)


class ForceCrashValidationTests(unittest.TestCase):
    def test_reports_exact_target_state_byte_without_writing_source_or_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            candidate_progress = edit_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
                updates=(WeaponProgressUpdate(192, development_state=5),),
            )
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )
            before_source_hash = sha256_file(source)
            before_candidate_hash = sha256_file(candidate)

            report = inspect_force_crash_candidate(source, candidate)

            self.assertEqual(report.source_sha256, before_source_hash)
            self.assertEqual(report.candidate_sha256, before_candidate_hash)
            self.assertEqual(report.changed_force_crash_records, (192,))
            expected_offset = PROGRESS_RECORD_OFFSET + 192 * PROGRESS_RECORD_SIZE
            self.assertEqual(report.changed_decoded_offsets, (expected_offset,))
            self.assertEqual(sha256_file(source), before_source_hash)
            self.assertEqual(sha256_file(candidate), before_candidate_hash)

    def test_rejects_any_change_outside_force_crash_development_state_whitelist(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            table = decode_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
            )
            decoded = bytearray(table.decoded)
            decoded[PROGRESS_RECORD_OFFSET + 192 * PROGRESS_RECORD_SIZE + 1] = 1
            candidate_progress = encode_container(bytes(decoded))[8:]
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )

            with self.assertRaisesRegex(ForceCrashValidationError, "白名单"):
                inspect_force_crash_candidate(source, candidate)

    def test_rejects_truncated_candidate_before_pairwise_offset_comparison(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            candidate_progress = edit_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
                updates=(WeaponProgressUpdate(192, development_state=5),),
            )
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )
            real_decode = decode_weapon_progress_attachment
            call_count = 0

            def decode_with_truncated_candidate(
                encoded: bytes,
                *,
                record_count: int,
            ) -> WeaponProgressTable:
                nonlocal call_count
                call_count += 1
                table = real_decode(encoded, record_count=record_count)
                if call_count == 2:
                    return WeaponProgressTable(
                        table.records,
                        table.prefix,
                        table.suffix,
                        table.decoded[:-1],
                        table.encoded,
                    )
                return table

            with (
                patch(
                    "bfmod.force_crash_validation.decode_weapon_progress_attachment",
                    side_effect=decode_with_truncated_candidate,
                ),
                self.assertRaisesRegex(ForceCrashValidationError, "解码长度"),
            ):
                inspect_force_crash_candidate(source, candidate)

    def test_report_hashes_the_exact_bytes_that_passed_validation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            candidate_progress = edit_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
                updates=(WeaponProgressUpdate(192, development_state=5),),
            )
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )
            expected_source = source.read_bytes()
            expected_candidate = candidate.read_bytes()
            source_resolved = source.resolve()
            candidate_resolved = candidate.resolve()
            original_open = Path.open
            binary_open_counts = {
                source_resolved: 0,
                candidate_resolved: 0,
            }

            def open_once(
                path: Path,
                mode: str = "r",
                buffering: int = -1,
                encoding: str | None = None,
                errors: str | None = None,
                newline: str | None = None,
            ) -> Any:
                resolved = path.resolve()
                if mode == "rb" and resolved in binary_open_counts:
                    binary_open_counts[resolved] += 1
                    if binary_open_counts[resolved] > 1:
                        raise AssertionError(f"重复读取：{resolved}")
                return original_open(
                    path,
                    mode=mode,
                    buffering=buffering,
                    encoding=encoding,
                    errors=errors,
                    newline=newline,
                )

            with patch.object(Path, "open", autospec=True, side_effect=open_once):
                report = inspect_force_crash_candidate(source, candidate)

            self.assertEqual(binary_open_counts[source_resolved], 1)
            self.assertEqual(binary_open_counts[candidate_resolved], 1)
            self.assertEqual(report.source_sha256, sha256(expected_source).hexdigest().upper())
            self.assertEqual(
                report.candidate_sha256,
                sha256(expected_candidate).hexdigest().upper(),
            )

    def test_builds_an_auditable_pending_manual_validation_record(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            candidate_progress = edit_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
                updates=(WeaponProgressUpdate(212, development_state=5),),
            )
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )
            report = inspect_force_crash_candidate(source, candidate)

            record_text = build_force_crash_validation_record(
                report,
                slot_name="Save002.dat",
            )
            record = loads(record_text)

            self.assertEqual(record["schema"], 1)
            self.assertEqual(record["capability"], "force_crash_offline_unlock")
            self.assertEqual(record["automated_validation"], "passed")
            self.assertEqual(record["changed_force_crash_records"], [212])
            self.assertEqual(record["target_slot_name"], "Save002.dat")
            self.assertEqual(record["manual_validation"]["overall"], "pending")
            self.assertEqual(
                set(record["manual_validation"]["checks"]),
                {"displayed", "equippable", "activatable", "resaved", "persisted"},
            )
            self.assertTrue(
                all(
                    value == "pending"
                    for value in record["manual_validation"]["checks"].values()
                )
            )

    def test_validation_record_writer_never_overwrites_existing_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.dat"
            candidate = root / "candidate.dat"
            output = root / "evidence" / "record.json"
            source.write_bytes(_save_slot_bytes())
            source_slot = decode_save_slot(source.read_bytes())
            candidate_progress = edit_weapon_progress_attachment(
                source_slot.weapon_progress_attachment,
                record_count=213,
                updates=(WeaponProgressUpdate(192, development_state=5),),
            )
            candidate.write_bytes(
                replace_save_slot_attachment(source_slot, 1, candidate_progress)
            )
            report = inspect_force_crash_candidate(source, candidate)

            written = write_force_crash_validation_record(
                report,
                output,
                slot_name="Save002.dat",
            )

            self.assertEqual(written, output.resolve())
            original = output.read_text(encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_force_crash_validation_record(
                    report,
                    output,
                    slot_name="Save002.dat",
                )
            self.assertEqual(output.read_text(encoding="utf-8"), original)


def _save_slot_bytes() -> bytes:
    main = encode_container(bytes(range(64)))
    main_payload = main[8:]
    progress = encode_container(b"\0" * PROGRESS_BLOCK_SIZE)[8:]
    attachments = (b"first", progress, b"third", b"fourth")
    header = bytearray(28)
    pack_into(
        "<7I",
        header,
        0,
        100,
        64,
        len(main_payload),
        *(len(item) for item in attachments),
    )
    return bytes(header) + main_payload + b"".join(attachments)


if __name__ == "__main__":
    unittest.main()
