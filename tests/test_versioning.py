from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bfmod.versioning import KNOWN_FINGERPRINTS, identify_installation


class VersioningTests(unittest.TestCase):
    def test_write_requires_every_known_fingerprint(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for relative in KNOWN_FINGERPRINTS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            with patch("bfmod.versioning.sha256_file") as sha256:
                sha256.side_effect = lambda path: KNOWN_FINGERPRINTS[
                    path.relative_to(root).as_posix()
                ]
                identity = identify_installation(root)

        self.assertTrue(identity.is_known)
        self.assertTrue(identity.write_supported)

    def test_unknown_or_missing_file_is_read_only_with_reasons(self) -> None:
        with TemporaryDirectory() as temporary:
            identity = identify_installation(temporary)
        self.assertFalse(identity.write_supported)
        self.assertTrue(identity.read_supported)
        self.assertEqual(len(identity.reasons), len(KNOWN_FINGERPRINTS))


if __name__ == "__main__":
    unittest.main()
