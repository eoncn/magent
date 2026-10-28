#!/usr/bin/env python3
"""Smoke tests for the Stage 3 paired benchmark summarizer."""
import importlib.util
from pathlib import Path
import csv
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stage3", ROOT / "analyze_stage3.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AnalyzerTests(unittest.TestCase):
    def sample(self, path, change=False):
        common = dict(case_id="S01", difficulty="S", project="fixture",
                      base_sha="abc123", route="routine", verdict="pass",
                      acceptance_pass="1", elapsed_sec="10",
                      input_tokens="100", output_tokens="20",
                      worker_calls="1", verifier_calls="1", retries="0",
                      checkpoint_recovered="0", notes="")
        old = dict(common, variant="old")
        new = dict(common, variant="new", route="standard", elapsed_sec="8")
        if change:
            new["verdict"] = "fail"
            new["acceptance_pass"] = "0"
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=mod.COLUMNS)
            writer.writeheader()
            writer.writerows([old, new])

    def test_pair_and_render(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "results.csv"
            self.sample(p)
            report = mod.render(mod.load_pairs(p))
            self.assertIn("Valid comparisons: 1", report)
            self.assertIn("old=120, new=120", report)

    def test_regression(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "results.csv"
            self.sample(p, change=True)
            self.assertIn("REGRESSION", mod.render(mod.load_pairs(p)))

    def test_missing_terra_coverage_warning_for_medium_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "results.csv"
            self.sample(p)
            rows = list(csv.DictReader(p.open(encoding="utf-8")))
            for row in rows:
                row["case_id"] = "M01"
                row["difficulty"] = "M"
                row["route"] = "routine"
            with p.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=mod.COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            report = mod.render(mod.load_pairs(p))
            self.assertIn("New M-class initial standard route: 0/1", report)
            self.assertIn("no new M-class case declared standard", report)

    def test_missing_tokens_are_explicit_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "results.csv"
            self.sample(p)
            rows = list(csv.DictReader(p.open(encoding="utf-8")))
            rows[1]["input_tokens"] = ""
            rows[1]["output_tokens"] = ""
            with p.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=mod.COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            report = mod.render(mod.load_pairs(p))
            self.assertIn("Missing complete token pairs: 1/1", report)
            self.assertIn("not present it as total experiment usage", report)

    def test_incomplete_pair_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "results.csv"
            self.sample(p)
            rows = p.read_text().splitlines()
            p.write_text("\n".join(rows[:2]) + "\n")
            with self.assertRaises(ValueError):
                mod.load_pairs(p)


if __name__ == "__main__":
    unittest.main()
