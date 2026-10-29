"""Release eligibility and artifact metadata must fail closed before publication."""

import io
import json
import tarfile
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.mkchad_release import package_version, validate_ref, verify_dist


class ReleaseTests(unittest.TestCase):
    def test_only_exact_version_tag_releases(self):
        version = "0.5.2+mkchad.1"
        self.assertFalse(validate_ref("refs/heads/maintenance", version, "maintenance"))
        self.assertTrue(validate_ref("refs/tags/v" + version, version, "maintenance"))
        for ref in [
            "refs/heads/main",
            "refs/tags/v0.5.2",
            "refs/tags/v0.5.2+mkchad.2",
            "refs/tags/v0.5.2+mkchad.1/extra",
        ]:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                validate_ref(ref, version, "maintenance")

    def test_only_positive_mkchad_local_versions(self):
        for version in [
            "0.5.2",
            "0.5.2.mkchad.1",
            "0.5.2+mkchad.0",
            "0.5.2+mkchad.01",
            "0.5.2+mkchad.1rc1",
        ]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_ref("refs/tags/v" + version, version, "maintenance")

    def test_version_read_does_not_import_package(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "version.py"
            path.write_text("raise RuntimeError('must not import')\n__version__ = '1.2.3+mkchad.4'")
            self.assertEqual(package_version(path), "1.2.3+mkchad.4")
            path = Path(tmp) / "package.json"
            path.write_text('{"version":"1.2.3+mkchad.4"}')
            self.assertEqual(package_version(path), "1.2.3+mkchad.4")

    def test_metadata_mismatch_never_writes_manifests(self):
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            metadata = b"Name: example-package\nVersion: 1.2.3+mkchad.1\n"
            with zipfile.ZipFile(dist / "example.whl", "w") as archive:
                archive.writestr("example.dist-info/METADATA", metadata)
            with tarfile.open(dist / "example.tar.gz", "w:gz") as archive:
                member = tarfile.TarInfo("example/PKG-INFO")
                member.size = len(metadata)
                archive.addfile(member, io.BytesIO(metadata))
            with self.assertRaises(ValueError):
                verify_dist(dist, "example_package", "1.2.3+mkchad.2", "test-commit")
            self.assertFalse((dist / "SHA256SUMS").exists())
            with patch("scripts.mkchad_release.installed_version", return_value="test"):
                verify_dist(dist, "example_package", "1.2.3+mkchad.1", "test-commit")
            self.assertEqual(
                json.loads((dist / "build-provenance.json").read_text())["source_commit"],
                "test-commit",
            )
            self.assertEqual(len((dist / "SHA256SUMS").read_text().splitlines()), 3)

    def test_missing_artifacts_are_rejected(self):
        with TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            verify_dist(Path(tmp), "example", "1.2.3+mkchad.1", "test-commit")
