from __future__ import annotations

import os
import unittest
from pathlib import Path

from bfmod.bfwt import load_bfwt
from bfmod.equipment_info import parse_equipment_info
from bfmod.force_crash import FORCE_CRASH_COUNT, FORCE_CRASH_RECORD_START
from bfmod.huffman import decode_container
from bfmod.installation import InstallationError, resolve_game_root
from bfmod.pac import PacArchive
from bfmod.save_slot import decode_save_slot, encode_save_slot
from bfmod.waz import parse_waz
from bfmod.weapon_progress import decode_weapon_progress_attachment

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _optional_game_root() -> Path | None:
    try:
        return resolve_game_root(None)
    except InstallationError:
        return None


GAME_ROOT = _optional_game_root() if os.environ.get("BFMOD_RUN_GAME_TESTS") == "1" else None


@unittest.skipUnless(
    GAME_ROOT is not None,
    "设置 BFMOD_RUN_GAME_TESTS=1 后才运行真实安装集成测试",
)
class InstalledGameTests(unittest.TestCase):
    def test_existing_config_decodes_to_known_size(self) -> None:
        assert GAME_ROOT is not None
        decoded = decode_container(
            (GAME_ROOT / "Save" / "Config.dat").read_bytes(),
            expected_size=0xA8200,
        )
        self.assertEqual(len(decoded.decoded), 0xA8200)

    def test_equipment_info_extracts_from_table_pac(self) -> None:
        assert GAME_ROOT is not None
        archive = PacArchive((GAME_ROOT / "Table.pac").read_bytes())
        result = archive.extract("EquipmentInfo.DAT")
        table = parse_equipment_info(result.data)
        self.assertEqual(len(table.records), 85)
        self.assertTrue(table.official_weapon_records[0].description_ja.startswith("出の早いパンチ"))
        self.assertEqual(table.reserved_player_record.description_ja, "")

    def test_table_and_exe_pac_equipment_layouts_have_only_known_text_difference(self) -> None:
        assert GAME_ROOT is not None
        tables = {
            archive_name: parse_equipment_info(
                PacArchive((GAME_ROOT / archive_name).read_bytes())
                .extract("EquipmentInfo.DAT")
                .data
            )
            for archive_name in ("Table.pac", "Exe.pac")
        }

        differing_records = [
            index
            for index, (table_record, executable_record) in enumerate(
                zip(tables["Table.pac"].records, tables["Exe.pac"].records, strict=True)
            )
            if table_record.raw != executable_record.raw
        ]
        self.assertEqual(differing_records, [62])
        self.assertIn("使いやすいく", tables["Table.pac"].records[62].description_ja)
        self.assertIn("使いやすく", tables["Exe.pac"].records[62].description_ja)

    def test_existing_save_slots_decode_and_rebuild_semantically(self) -> None:
        assert GAME_ROOT is not None
        for save_path in sorted((GAME_ROOT / "Save").glob("Save*.dat")):
            with self.subTest(save=save_path.name):
                slot = decode_save_slot(save_path.read_bytes())
                rebuilt = decode_save_slot(encode_save_slot(slot, slot.main_data))
                self.assertEqual(rebuilt.main_data, slot.main_data)
                self.assertEqual(rebuilt.attachments, slot.attachments)

    def test_tooru_waz_fully_matches_proven_nested_layout(self) -> None:
        assert GAME_ROOT is not None
        waz = parse_waz((GAME_ROOT / "Dat" / "Waza" / "TOORU.WAZ").read_bytes())

        self.assertEqual(waz.declared_record_count, 213)
        self.assertEqual(len(waz.records), 213)
        self.assertEqual(waz.records[0].name, "ｱｰﾑｼﾞｬﾌﾞ")
        self.assertIn("Lv2", waz.records[1].name)
        self.assertIn("Lv3", waz.records[2].name)
        self.assertEqual(waz.records[188].name, "ｸﾞﾗﾋﾞﾃｨﾌｨｰﾙﾄﾞ Lv3")
        self.assertEqual(waz.records[0].child_count, 1)
        self.assertEqual(waz.records[0].children[0].detail_count, 3)
        self.assertEqual(waz.records[-1].file_offset + len(waz.records[-1].raw), len(waz.raw))

    def test_force_crash_resources_match_the_proven_independent_record_range(self) -> None:
        assert GAME_ROOT is not None
        waz = parse_waz((GAME_ROOT / "Dat" / "Waza" / "TOORU.WAZ").read_bytes())
        info = parse_equipment_info(
            PacArchive((GAME_ROOT / "Table.pac").read_bytes())
            .extract("EquipmentInfo.DAT")
            .data
        )

        force_crashes = waz.records[
            FORCE_CRASH_RECORD_START : FORCE_CRASH_RECORD_START + FORCE_CRASH_COUNT
        ]
        self.assertEqual(len(force_crashes), 21)
        self.assertTrue(all(record.fixed_raw[0x28] == 1 for record in force_crashes))
        self.assertEqual(len(info.extra_records), 21)
        self.assertEqual(force_crashes[0].name, "ｷｬﾉﾝﾌﾞﾚｲｶｰ")
        self.assertEqual(force_crashes[-1].name, "ﾏﾙﾁﾌﾟﾙﾐｻｲﾙ")
        self.assertTrue(all(record.description_ja for record in info.extra_records))

    def test_force_crash_chinese_catalog_keeps_original_japanese_text(self) -> None:
        from bfmod.workspace import load_workspace

        assert GAME_ROOT is not None
        workspace = load_workspace(GAME_ROOT)
        self.assertEqual(len(workspace.force_crash_catalog), 21)
        self.assertEqual(workspace.force_crash_catalog[0].name_zh, "Force Crash #00")
        self.assertEqual(workspace.force_crash_catalog[20].name_zh, "Force Crash #20")
        self.assertTrue(
            all(
                entry.name_zh
                and not entry.description_zh
                and entry.name_ja
                and entry.description_ja
                for entry in workspace.force_crash_catalog
            )
        )

    def test_existing_slot_contains_valid_weapon_state_progress_and_heat_reduction(self) -> None:
        assert GAME_ROOT is not None
        save_path = GAME_ROOT / "Save" / "Save002.dat"
        if not save_path.is_file():
            self.skipTest("当前安装没有 Save002.dat 研究样本")

        slot = decode_save_slot(save_path.read_bytes())
        progress = decode_weapon_progress_attachment(
            slot.weapon_progress_attachment,
            record_count=213,
        )
        waz = parse_waz((GAME_ROOT / "Dat" / "Waza" / "TOORU.WAZ").read_bytes())

        record = progress.records[0]
        self.assertIn(record.development_state, range(0x100))
        self.assertIn(record.level_index, range(3))
        self.assertIn(record.level_progress, range(10_001))
        self.assertIn(record.level_progress_baseline, range(10_001))
        self.assertIn(record.heat_reduction, range(131))
        self.assertEqual(waz.records[0].base_heat, 5)
        self.assertEqual(
            record.effective_heat(waz.records[0].base_heat),
            max(5 - record.heat_reduction, 1),
        )


class ProjectReferenceDataTests(unittest.TestCase):
    def test_checked_out_bfwt_has_official_weapon_sequence(self) -> None:
        table_path = PROJECT_ROOT / "BFWT.htm"
        if not table_path.is_file():
            self.skipTest("项目目录没有本地 BFWT.htm 参考资料")
        weapons = load_bfwt(table_path)
        self.assertEqual([weapon.official_number for weapon in weapons], list(range(63)))
        self.assertEqual(weapons[0].normalized_name_ja, "アームジャブ")


if __name__ == "__main__":
    unittest.main()
