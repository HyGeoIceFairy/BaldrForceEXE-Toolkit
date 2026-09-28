from __future__ import annotations

import unittest
from dataclasses import replace

from bfmod.editing import EditableWeapon, EditingError, EditorSession
from bfmod.weapon_progress import WeaponProgressRecord


def _weapons() -> tuple[EditableWeapon, ...]:
    return tuple(
        EditableWeapon(
            official_number=number,
            name_ja=f"武器 {number}",
            base_heat=5 + number * 2,
            progress=WeaponProgressRecord(
                index=number * 3,
                development_state=1,
                level_index=0,
                weapon_use_count=number,
                development_exp=number * 10,
                level_progress=number,
                level_progress_baseline=number,
                heat_reduction=0,
                raw=b"",
            ),
        )
        for number in range(63)
    )


class EditorSessionTests(unittest.TestCase):
    def test_bulk_semantic_edits_are_one_undo_step_and_redoable(self) -> None:
        session = EditorSession(_weapons())
        session.stage_unlock((0, 2, 2))

        self.assertEqual([change.weapon.official_number for change in session.changes], [0, 2])
        self.assertTrue(all(change.target.development_state == 5 for change in session.changes))
        self.assertTrue(session.undo())
        self.assertEqual(session.changes, ())
        self.assertTrue(session.redo())
        self.assertEqual(len(session.changes), 2)

    def test_discard_selected_changes_is_one_undoable_step(self) -> None:
        session = EditorSession(_weapons())
        session.stage_normal_completion((0, 1, 2))

        self.assertEqual(session.discard_changes((0, 2)), 2)
        self.assertEqual(
            [change.weapon.official_number for change in session.changes],
            [1],
        )
        self.assertTrue(session.undo())
        self.assertEqual(
            [change.weapon.official_number for change in session.changes],
            [0, 1, 2],
        )

    def test_master_synchronizes_progress_baseline_and_normal_heat_uses_game_floor(self) -> None:
        session = EditorSession(_weapons())
        session.stage_master((0,))
        session.stage_normal_minimum_heat((0, 1))

        first = session.target_for(0)
        self.assertEqual(
            (first.level_index, first.level_progress, first.level_progress_baseline),
            (2, 0, 0),
        )
        self.assertEqual(first.heat_reduction, 4)  # base 5 -> trunc(5 / 3) == 1
        self.assertEqual(session.target_for(1).heat_reduction, 5)  # base 7 -> 2
        self.assertEqual(
            [update.record_index for update in session.progress_updates()], [0, 3]
        )

    def test_preset_is_semantic_versioned_and_import_only_updates_draft(self) -> None:
        session = EditorSession(_weapons())
        session.stage_unlock((4,))
        session.stage_normal_minimum_heat((4,))
        text = session.export_preset(name="测试")

        restored = EditorSession(_weapons())
        restored.import_preset(text)

        self.assertEqual(restored.target_for(4), session.target_for(4))
        self.assertEqual(len(restored.changes), 1)
        self.assertTrue(restored.can_undo)

    def test_unlock_only_preset_does_not_overwrite_other_slot_progress(self) -> None:
        source = EditorSession(_weapons())
        source.stage_unlock((4,))
        text = source.export_preset(name="只解锁")
        self.assertNotIn("level_progress", text)

        destination_weapons = list(_weapons())
        destination_weapons[4] = replace(
            destination_weapons[4],
            progress=replace(
                destination_weapons[4].progress,
                level_index=1,
                level_progress=7777,
                level_progress_baseline=7000,
                heat_reduction=9,
            ),
        )
        destination = EditorSession(tuple(destination_weapons))
        destination.import_preset(text)

        target = destination.target_for(4)
        self.assertEqual(target.development_state, 5)
        self.assertEqual(target.level_index, 1)
        self.assertEqual(target.level_progress, 7777)
        self.assertEqual(target.level_progress_baseline, 7000)
        self.assertEqual(target.heat_reduction, 9)

    def test_normal_completion_combines_unlock_master_and_heat_as_one_undo_step(self) -> None:
        session = EditorSession(_weapons())
        session.stage_normal_completion((0, 1, 2))
        first = session.target_for(0)
        self.assertEqual(first.development_state, 5)
        self.assertEqual(first.level_index, 2)
        self.assertEqual(first.level_progress, 0)
        self.assertEqual(first.heat_reduction, 4)
        self.assertTrue(session.undo())
        self.assertEqual(session.changes, ())

    def test_rejects_bad_mapping_bad_selection_and_unsafe_preset_values(self) -> None:
        weapons = list(_weapons())
        weapons[4] = replace(
            weapons[4], progress=replace(weapons[4].progress, index=13)
        )
        with self.assertRaisesRegex(EditingError, "3.n"):
            EditorSession(tuple(weapons))

        session = EditorSession(_weapons())
        with self.assertRaisesRegex(EditingError, "至少"):
            session.stage_unlock(())
        with self.assertRaises(EditingError):
            session.import_preset(
                '{"schema":1,"weapons":[{"official_number":0,"target":'
                '{"heat_reduction":999}}]}'
            )
        with self.assertRaisesRegex(EditingError, "正常最低热度"):
            session.stage_heat_reduction((0,), heat_reduction=5)
        with self.assertRaisesRegex(EditingError, "正常最低热度"):
            session.import_preset(
                '{"schema":1,"weapons":[{"official_number":0,"target":'
                '{"heat_reduction":5}}]}'
            )
        with self.assertRaisesRegex(EditingError, "开发状态"):
            session.import_preset(
                '{"schema":1,"weapons":[{"official_number":0,"target":'
                '{"development_state":2}}]}'
            )


if __name__ == "__main__":
    unittest.main()
