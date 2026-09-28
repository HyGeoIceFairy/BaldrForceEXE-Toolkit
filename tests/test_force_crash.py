from __future__ import annotations

import json
import unittest
from dataclasses import replace
from struct import pack, pack_into

from bfmod.force_crash import (
    FORCE_CRASH_COUNT,
    FORCE_CRASH_RECORD_START,
    ForceCrashDefinition,
    ForceCrashError,
    ForceCrashSession,
)
from bfmod.huffman import encode_container
from bfmod.save_slot import decode_save_slot
from bfmod.weapon_progress import (
    PROGRESS_BLOCK_SIZE,
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    WeaponProgressRecord,
    decode_weapon_progress_attachment,
)


def _force_crashes() -> tuple[ForceCrashDefinition, ...]:
    return tuple(
        ForceCrashDefinition(
            number=number,
            record_index=FORCE_CRASH_RECORD_START + number,
            name_ja=f"Force Crash {number}",
            description_ja=f"说明 {number}",
            progress=WeaponProgressRecord(
                index=FORCE_CRASH_RECORD_START + number,
                development_state=5 if number == 0 else 1,
                level_index=0,
                weapon_use_count=0,
                development_exp=0,
                level_progress=0,
                level_progress_baseline=0,
                heat_reduction=0,
                raw=b"\0" * 0x18,
            ),
        )
        for number in range(FORCE_CRASH_COUNT)
    )


def _save_with_force_crash_progress() -> bytes:
    decoded = bytearray(PROGRESS_BLOCK_SIZE)
    for record_index in range(213):
        offset = PROGRESS_RECORD_OFFSET + record_index * PROGRESS_RECORD_SIZE
        decoded[offset] = 5 if record_index == FORCE_CRASH_RECORD_START else 1
        decoded[offset + 1] = 0
        pack_into("<i", decoded, offset + 0x04, record_index)
    progress_attachment = encode_container(bytes(decoded))[8:]
    main_container = encode_container(b"main payload")
    main_payload = main_container[8:]
    attachments = (b"first", progress_attachment, b"third", b"fourth")
    sizes = (len(main_payload), *(len(item) for item in attachments))
    return pack("<7I", 100, len(b"main payload"), *sizes) + main_payload + b"".join(
        attachments
    )


class ForceCrashSessionTests(unittest.TestCase):
    def test_render_save_only_changes_selected_force_crash_state_bytes(self) -> None:
        original = decode_save_slot(_save_with_force_crash_progress())
        before = decode_weapon_progress_attachment(
            original.weapon_progress_attachment,
            record_count=213,
        )
        definitions = tuple(
            replace(definition, progress=before.records[definition.record_index])
            for definition in _force_crashes()
        )
        session = ForceCrashSession(definitions)
        session.stage_unlock((1, 20))

        rebuilt = decode_save_slot(session.render_save(original))
        after = decode_weapon_progress_attachment(
            rebuilt.weapon_progress_attachment,
            record_count=213,
        )

        expected_offsets = {
            PROGRESS_RECORD_OFFSET + 193 * PROGRESS_RECORD_SIZE,
            PROGRESS_RECORD_OFFSET + 212 * PROGRESS_RECORD_SIZE,
        }
        changed_offsets = {
            offset
            for offset, (old, new) in enumerate(zip(before.decoded, after.decoded, strict=True))
            if old != new
        }
        self.assertEqual(changed_offsets, expected_offsets)
        self.assertEqual(rebuilt.main_payload, original.main_payload)
        self.assertEqual(rebuilt.attachments[0], original.attachments[0])
        self.assertEqual(rebuilt.attachments[2:], original.attachments[2:])

    def test_bulk_unlock_is_one_undo_step_and_only_updates_selected_records(self) -> None:
        session = ForceCrashSession(_force_crashes())

        session.stage_unlock((1, 20, 1))

        self.assertEqual([change.force_crash.number for change in session.changes], [1, 20])
        self.assertEqual(
            [update.record_index for update in session.progress_updates()],
            [193, 212],
        )
        self.assertTrue(
            all(update.development_state == 5 for update in session.progress_updates())
        )
        self.assertTrue(session.undo())
        self.assertEqual(session.changes, ())
        self.assertTrue(session.redo())
        self.assertEqual(len(session.changes), 2)

    def test_unlocking_default_item_is_a_noop(self) -> None:
        session = ForceCrashSession(_force_crashes())
        session.stage_unlock((0,))
        self.assertEqual(session.changes, ())

    def test_preset_is_versioned_semantic_and_imports_as_draft(self) -> None:
        session = ForceCrashSession(_force_crashes())
        session.stage_unlock((2, 7))
        text = session.export_preset(name="Force Crash 测试")
        payload = json.loads(text)

        restored = ForceCrashSession(_force_crashes())
        restored.import_preset(text)

        self.assertEqual(payload["kind"], "force_crash")
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["force_crashes"], [2, 7])
        self.assertEqual(restored.progress_updates(), session.progress_updates())
        self.assertTrue(restored.can_undo)

    def test_rejects_wrong_catalog_mapping_and_weapon_preset(self) -> None:
        definitions = list(_force_crashes())
        definitions[3] = replace(definitions[3], record_index=12)
        with self.assertRaisesRegex(ForceCrashError, "192..212"):
            ForceCrashSession(tuple(definitions))

        session = ForceCrashSession(_force_crashes())
        with self.assertRaisesRegex(ForceCrashError, "预设"):
            session.import_preset('{"schema":1,"weapons":[]}')


if __name__ == "__main__":
    unittest.main()
