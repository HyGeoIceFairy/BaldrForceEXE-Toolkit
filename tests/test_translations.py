from __future__ import annotations

import unittest

from bfmod.force_crash_translations import FORCE_CRASH_DESCRIPTIONS_ZH, FORCE_CRASH_NAMES_ZH
from bfmod.translations import WEAPON_DESCRIPTIONS_ZH, WEAPON_NAMES_ZH


class TranslationTests(unittest.TestCase):
    def test_translation_tables_cover_all_official_weapons(self) -> None:
        self.assertEqual(len(WEAPON_NAMES_ZH), 63)
        self.assertEqual(len(WEAPON_DESCRIPTIONS_ZH), 63)
        self.assertTrue(all(name.strip() for name in WEAPON_NAMES_ZH))
        self.assertTrue(all(not description for description in WEAPON_DESCRIPTIONS_ZH))

    def test_public_build_does_not_bundle_game_weapon_names(self) -> None:
        self.assertEqual(WEAPON_NAMES_ZH[0], "武器 #00")
        self.assertEqual(WEAPON_NAMES_ZH[62], "武器 #62")

    def test_force_crash_reference_translations_cover_independent_record_range(self) -> None:
        self.assertEqual(len(FORCE_CRASH_NAMES_ZH), 21)
        self.assertEqual(len(FORCE_CRASH_DESCRIPTIONS_ZH), 21)
        self.assertEqual(FORCE_CRASH_NAMES_ZH[0], "Force Crash #00")
        self.assertEqual(FORCE_CRASH_NAMES_ZH[20], "Force Crash #20")
        self.assertTrue(all(name.strip() for name in FORCE_CRASH_NAMES_ZH))
        self.assertTrue(all(not description for description in FORCE_CRASH_DESCRIPTIONS_ZH))


if __name__ == "__main__":
    unittest.main()
