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
SCRIPT_PATH = REPO_ROOT / "scripts" / "report_historical_irs_source_leads.py"


def load_report_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("report_historical_irs_source_leads", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("source lead module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HistoricalIrsSourceLeadReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reporter = load_report_module()

    def test_report_covers_1989_through_1995(self) -> None:
        report = json.loads(self.reporter.report_json())

        years = [lead["year"] for lead in report["leads"]]

        self.assertEqual([1989, 1990, 1991, 1992, 1993, 1994, 1995], years)
        self.assertEqual(
            "GOVPUB-T22-c1f3e2322722ef17ff04ef9a47a3b6da",
            report["leads"][-1]["govinfo"]["package_id"],
        )
        self.assertIn("GovInfo records are official", report["notes"][0])
        self.assertEqual(3, len(report["comparison_sources"]))

    def test_1989_records_tax_notes_archival_lead(self) -> None:
        report = json.loads(self.reporter.report_json())
        lead_1989 = next(lead for lead in report["leads"] if lead["year"] == 1989)

        self.assertIn("tax_notes", lead_1989)
        self.assertEqual(
            "Tax Notes Archival Document for Rev. Rul. 89-111",
            lead_1989["tax_notes"][0]["title"],
        )
        self.assertEqual(["1989-10"], lead_1989["tax_notes"][0]["periods"])

    def test_1993_records_archive_gap(self) -> None:
        report = json.loads(self.reporter.report_json())
        lead_1993 = next(lead for lead in report["leads"] if lead["year"] == 1993)

        self.assertNotIn("index_identifier", lead_1993["internet_archive"])
        self.assertIn(
            "No matching Archive.org index identifier",
            lead_1993["internet_archive"]["notes"][0],
        )

    def test_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "leads.json"

            self.reporter.write_report(output_path)

            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "historical-irs-revenue-ruling-source-leads",
                report["report_id"],
            )

    def test_writes_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "leads.md"

            self.reporter.write_markdown_report(output_path)

            text = output_path.read_text(encoding="utf-8")
            self.assertIn("Tax Notes Archival Document for Rev. Rul. 89-111", text)
            self.assertIn("Comparison Sources", text)


if __name__ == "__main__":
    unittest.main()
