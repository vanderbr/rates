# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import import_2015_vbt


class Import2015VbtTests(unittest.TestCase):
    def test_normalize_relative_risk_table_preserves_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (
                "2015_vbt_relative_risk_tables__2015_male_non_smoker_rr100_anb.json"
            )
            self.write_table(
                path,
                {
                    "source": "2015 VBT Relative Risk Tables",
                    "table": "2015 VBT Male Non Smoker RR100 ANB",
                    "table_identity": 3252,
                    "select_rates": {"18": {"1": 69, "2": 72}},
                    "ultimate_rates": {"18": 69, "19": 64},
                },
            )

            table = import_2015_vbt.normalize_table(path)

        self.assertEqual("relative_risk", table.get("structure"))
        self.assertEqual("male", table.get("sex"))
        self.assertEqual("non_smoker", table.get("smoker"))
        self.assertEqual("anb", table.get("age_basis"))
        self.assertEqual(100, table.get("relative_risk_percent"))
        self.assertEqual(
            {"18": {"1": 69, "2": 72}},
            table.get("select_rates"),
        )

    def test_normalize_rejects_boolean_rate_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2015_vbt_smoker_distinct_tables__2015_fns_alb.json"
            self.write_table(
                path,
                {
                    "source": "2015 VBT Smoker-Distinct Tables",
                    "table": "2015 VBT FNS ALB",
                    "table_identity": 3270,
                    "select_rates": {"18": {"1": True}},
                    "ultimate_rates": {"18": 69},
                },
            )

            with self.assertRaises(import_2015_vbt.Import2015VbtError):
                import_2015_vbt.normalize_table(path)

    def write_table(self, path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
