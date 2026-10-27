from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from returns_workflow.generator import generate_dataset, verify_reproducibility
from returns_workflow.policy import calculate_refund_cents, disposition_for, is_eligible
from returns_workflow.validation import validate_dataset


class PolicyTests(unittest.TestCase):
    def test_return_window_boundaries_and_exclusions(self) -> None:
        ordered = date(2026, 1, 1)
        self.assertTrue(is_eligible("apparel", ordered, ordered + timedelta(days=45), False))
        self.assertFalse(is_eligible("apparel", ordered, ordered + timedelta(days=46), False))
        self.assertTrue(is_eligible("apparel", ordered, ordered + timedelta(days=46), True))
        self.assertFalse(is_eligible("final_sale", ordered, ordered + timedelta(days=1), True))
        self.assertFalse(is_eligible("home", ordered, ordered - timedelta(days=1), True))

    def test_refund_is_prorated_and_fees_are_applied(self) -> None:
        self.assertEqual(calculate_refund_cents(10_000, 1, 2, "damaged"), 5_000)
        self.assertEqual(calculate_refund_cents(10_000, 1, 2, "no_longer_needed"), 4_501)
        self.assertEqual(calculate_refund_cents(10_000, 0, 2, "damaged"), 0)

    def test_condition_drives_disposition(self) -> None:
        self.assertEqual(disposition_for("sellable"), "restock")
        self.assertEqual(disposition_for("minor_damage"), "refurbish")
        self.assertEqual(disposition_for("missing_parts"), "quarantine")
        self.assertEqual(disposition_for("unsafe"), "scrap")


class DatasetTests(unittest.TestCase):
    def test_starter_dataset_integrity_and_required_edge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dataset"
            summary = generate_dataset(
                output,
                order_count=300,
                request_count=50,
                return_line_count=80,
                seed=20261022,
            )
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["row_counts"]["orders"], 300)
            self.assertEqual(summary["row_counts"]["return_requests"], 50)
            self.assertEqual(summary["row_counts"]["return_lines"], 80)
            self.assertEqual(summary["checks_passed"], summary["checks_total"])
            self.assertGreater(summary["metrics"]["shipment_outcomes"]["missing"], 0)
            self.assertGreater(summary["metrics"]["request_reason_counts"]["damaged"], 0)
            self.assertGreater(summary["metrics"]["request_reason_counts"]["exchange"], 0)
            self.assertGreater(summary["metrics"]["partial_authorization_count"], 0)

            truth = json.loads((output / "ground_truth.json").read_text(encoding="utf-8"))
            self.assertEqual(len(truth), 5)
            with (output / "ground_truth.csv").open(newline="", encoding="utf-8") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 5)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["seed"], 20261022)
            self.assertIn("seed.sql", manifest["checksums_sha256"])
            self.assertTrue((output / "source_manifest.json").exists())
            self.assertEqual(validate_dataset(output, write_summary=False)["status"], "passed")

    def test_same_seed_produces_identical_exports(self) -> None:
        result = verify_reproducibility(
            order_count=60,
            request_count=10,
            return_line_count=16,
            seed=20261022,
        )
        self.assertTrue(result["reproducible"])
        self.assertGreaterEqual(result["files_compared"], 10)

    def test_invalid_scale_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                generate_dataset(
                    Path(temporary) / "dataset",
                    order_count=5,
                    request_count=5,
                    return_line_count=21,
                )


if __name__ == "__main__":
    unittest.main()
