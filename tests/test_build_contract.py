from __future__ import annotations

import re
import unittest
from pathlib import Path

import bfmod

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BuildContractTests(unittest.TestCase):
    def test_user_facing_versions_match_package_version(self) -> None:
        version = bfmod.__version__

        for filename in (
            "README.md",
            "SECURITY.md",
            "THIRD_PARTY_NOTICES.txt",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
        ):
            with self.subTest(filename=filename):
                text = (PROJECT_ROOT / filename).read_text(encoding="utf-8-sig")
                self.assertIn(version, text)

    def test_build_script_bundles_all_user_facing_documents(self) -> None:
        script = (PROJECT_ROOT / "build.ps1").read_text(encoding="utf-8-sig")

        for filename in (
            "README.md",
            "RELEASE_NOTES.md",
            "SECURITY.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
        ):
            with self.subTest(filename=filename):
                self.assertIn(f'".\\{filename}"', script)

    def test_build_script_checks_all_pe_version_fields_against_package_version(self) -> None:
        script = (PROJECT_ROOT / "build.ps1").read_text(encoding="utf-8-sig")

        for field in ("filevers", "prodvers", "FileVersion", "ProductVersion"):
            with self.subTest(field=field):
                self.assertIn(field, script)
        self.assertIn("版本资源", script)

    def test_build_script_licenses_follow_actual_runtime_versions(self) -> None:
        script = (PROJECT_ROOT / "build.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("PySide6.__version__", script)
        self.assertIn('h=$PySideVersion', script)
        self.assertIn('Python-$PythonVersion.txt', script)
        self.assertIn('GNU LESSER GENERAL PUBLIC LICENSE', script)
        self.assertIn('GNU GENERAL PUBLIC LICENSE', script)

    def test_build_gates_archive_on_frozen_application_startup(self) -> None:
        script = (PROJECT_ROOT / "build.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"--smoke-test"', script)
        self.assertIn('WaitForExit(30000)', script)
        self.assertLess(script.index('"--smoke-test"'), script.index('Compress-Archive'))

    def test_version_info_fields_match_package_version(self) -> None:
        text = (PROJECT_ROOT / "version_info.txt").read_text(encoding="utf-8-sig")
        version = bfmod.__version__
        numeric = tuple(int(part) for part in version.split(".")) + (0,)

        self.assertIn(f"filevers={numeric}", text)
        self.assertIn(f"prodvers={numeric}", text)
        for field in ("FileVersion", "ProductVersion"):
            self.assertRegex(
                text,
                re.escape(f"StringStruct('{field}', '{version}')"),
            )


if __name__ == "__main__":
    unittest.main()
