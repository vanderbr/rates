# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "archive_irs_pdf_source.py"
SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-26-07.pdf"
IRB_URL = "https://www.irs.gov/pub/irs-irbs/irb96-02.pdf"
ENCODED_SPACE_URL = "https://www.irs.gov/pub/irs-drop/rr%20-13-18.pdf"
PDF_BYTES = b"%PDF-1.7\nsource bytes\n%%EOF\n"


def load_archive_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("archive_irs_pdf_source", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("archive module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class IrsPdfSourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.archiver = load_archive_module()

    def test_archives_pdf_under_year_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"

            entry = self.archiver.archive_pdf(
                archive_dir=archive_dir,
                year="2026",
                periods=("2026-04",),
                subjects=("section-7520-rates", "afr"),
                source_url=SOURCE_URL,
                title="  Rev.   Rul. 2026-7  ",
                retrieved_date="2026-06-17",
                body=PDF_BYTES,
            )

            self.assertEqual(
                "by-year/2026/"
                "2026-04_afr-7520_rev-rul-2026-7_rr-26-07.pdf",
                entry.path,
            )
            self.assertEqual(("afr", "section-7520-rates"), entry.subjects)
            self.assertEqual("Rev. Rul. 2026-7", entry.title)
            self.assertTrue((archive_dir / entry.path).is_file())
            manifest = json.loads(
                (archive_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("irs-revenue-rulings", manifest["archive_id"])
            self.assertEqual(1, len(manifest["entries"]))
            self.assertEqual(entry.sha256, manifest["entries"][0]["sha256"])
            index = (archive_dir / "INDEX.md").read_text(encoding="utf-8")
            self.assertIn("2026-04", index)
            self.assertIn("AFR, Section 7520", index)
            self.assertIn(
                "| 2026-04 | AFR, Section 7520 | Rev. Rul. 2026-7 | "
                "[PDF]"
                "(by-year/2026/2026-04_afr-7520_rev-rul-2026-7_rr-26-07.pdf)",
                index,
            )
            self.assertTrue(
                self.archiver.archive_contains_entry(
                    archive_dir,
                    ("2026-04",),
                    ("afr", "section-7520-rates"),
                    SOURCE_URL,
                )
            )
            self.assertFalse(
                self.archiver.archive_contains_entry(
                    archive_dir,
                    ("2026-05",),
                    ("afr", "section-7520-rates"),
                    SOURCE_URL,
                )
            )
            self.assertEqual(
                ("2026-04",),
                self.archiver.archived_periods_for_source(
                    archive_dir,
                    ("afr", "section-7520-rates"),
                    SOURCE_URL,
                ),
            )

    def test_accepts_irs_irb_pdf_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"

            entry = self.archiver.archive_pdf(
                archive_dir=archive_dir,
                year="1996",
                periods=("1996-01",),
                subjects=("section-7520-rates",),
                source_url=IRB_URL,
                title="Internal Revenue Bulletin 1996-2",
                retrieved_date="2026-06-17",
                body=PDF_BYTES,
            )

            self.assertEqual(
                "by-year/1996/"
                "1996-01_7520_internal-revenue-bulletin-1996-2_irb96-02.pdf",
                entry.path,
            )

    def test_accepts_irs_pdf_url_with_encoded_space(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"

            entry = self.archiver.archive_pdf(
                archive_dir=archive_dir,
                year="2013",
                periods=("2013-10",),
                subjects=("afr", "section-7520-rates"),
                source_url=ENCODED_SPACE_URL,
                title="Rev. Rul. 2013-18",
                retrieved_date="2026-06-17",
                body=PDF_BYTES,
            )

            self.assertEqual(
                "by-year/2013/"
                "2013-10_afr-7520_rev-rul-2013-18_rr-13-18.pdf",
                entry.path,
            )

    def test_reads_already_downloaded_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_pdf = Path(directory) / "source.pdf"
            input_pdf.write_bytes(PDF_BYTES)

            body = self.archiver.read_pdf_from_path(input_pdf)

        self.assertEqual(PDF_BYTES, body)

    def test_accepts_legacy_irs_pdf_preamble(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_pdf = Path(directory) / "source.pdf"
            body_with_preamble = b"\x00" * 32 + PDF_BYTES
            input_pdf.write_bytes(body_with_preamble)

            body = self.archiver.read_pdf_from_path(input_pdf)

        self.assertEqual(body_with_preamble, body)

    def test_rejects_non_irs_pdf_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"

            with self.assertRaises(self.archiver.ArchiveError) as context:
                self.archiver.archive_pdf(
                    archive_dir=archive_dir,
                    year="2026",
                    periods=("2026-04",),
                    subjects=("section-7520-rates",),
                    source_url="https://example.com/pub/irs-drop/rr-26-07.pdf",
                    title="Rev. Rul. 2026-7",
                    retrieved_date="2026-06-17",
                    body=PDF_BYTES,
                )

        self.assertEqual(self.archiver.ArchiveErrorCode.BAD_PDF_URL, context.exception.code)

    def test_conflicting_rearchive_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"
            self.archiver.archive_pdf(
                archive_dir=archive_dir,
                year="2026",
                periods=("2026-04",),
                subjects=("section-7520-rates",),
                source_url=SOURCE_URL,
                title="Rev. Rul. 2026-7",
                retrieved_date="2026-06-17",
                body=PDF_BYTES,
            )

            with self.assertRaises(self.archiver.ArchiveError) as context:
                self.archiver.archive_pdf(
                    archive_dir=archive_dir,
                    year="2026",
                    periods=("2026-04",),
                    subjects=("section-7520-rates",),
                    source_url=SOURCE_URL,
                    title="Rev. Rul. 2026-7",
                    retrieved_date="2026-06-17",
                    body=b"%PDF-1.7\nchanged bytes\n%%EOF\n",
                )

        self.assertEqual(self.archiver.ArchiveErrorCode.BAD_MANIFEST, context.exception.code)


if __name__ == "__main__":
    unittest.main()
