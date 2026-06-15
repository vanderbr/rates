# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from scripts import update_actuarial_tables as updater


class ActuarialTableUpdaterTests(unittest.TestCase):
    def test_parse_rate_basis_points_from_percent_text(self) -> None:
        self.assertEqual(20, updater.parse_rate_basis_points("0.2%"))
        self.assertEqual(2000, updater.parse_rate_basis_points("Interest Rate of 20.0 Percent"))
        self.assertIsNone(updater.parse_rate_basis_points("not a rate"))

    def test_parse_mortality_table_records_from_three_column_blocks(self) -> None:
        records = updater.parse_mortality_records(
            [
                [None, "Age", None, None, None, "Age", None, None, None, "Age", None],
                [None, 0, 100000, None, None, 37, 97193.66, None, None, 74, 71177.55],
                [None, 1, 99382.28, None, None, 38, 97058.84, None, None, 75, 69174.83],
            ]
        )

        self.assertEqual(
            [
                {"age": 0, "survivors_per_100000_scaled_1e6": 100000000000},
                {"age": 1, "survivors_per_100000_scaled_1e6": 99382280000},
                {"age": 37, "survivors_per_100000_scaled_1e6": 97193660000},
                {"age": 38, "survivors_per_100000_scaled_1e6": 97058840000},
                {"age": 74, "survivors_per_100000_scaled_1e6": 71177550000},
                {"age": 75, "survivors_per_100000_scaled_1e6": 69174830000},
            ],
            records,
        )

    def test_life_expectancy_records_are_computed_from_survivors(self) -> None:
        records = updater.parse_life_expectancy_records(
            [
                {"age": 0, "survivors_per_100000_scaled_1e6": 100000000},
                {"age": 1, "survivors_per_100000_scaled_1e6": 80000000},
                {"age": 2, "survivors_per_100000_scaled_1e6": 0},
            ]
        )

        self.assertEqual(
            {
                "age": 0,
                "curtate_life_expectancy_years_scaled_1e6": 800000,
                "complete_life_expectancy_years_scaled_1e6": 1300000,
            },
            records[0],
        )
        self.assertEqual(
            {
                "age": 2,
                "curtate_life_expectancy_years_scaled_1e6": 0,
                "complete_life_expectancy_years_scaled_1e6": 0,
            },
            records[2],
        )

    def test_table_d_generation_matches_irs_workbook_formula(self) -> None:
        records = updater.generate_table_d()
        one_percent_records = records[100]
        self.assertEqual(20, len(one_percent_records))
        self.assertEqual(
            {
                "adjusted_payout_rate_basis_points": 100,
                "term_years": 2,
                "unitrust_remainder_factor_scaled_1e6": 980100,
            },
            one_percent_records[1],
        )


if __name__ == "__main__":
    unittest.main()
