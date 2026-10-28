#!/usr/bin/env python3
"""Offline tests for isolated standard-worker A/B preparation and statistics."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prep = load_module("prepare_terra_trial")
analyzer = load_module("analyze_terra_trial")


class TerraTargetedTests(unittest.TestCase):
    def test_isolated_arms_and_no_source_changes(self):
        with tempfile.TemporaryDirectory(prefix="magent-terra-test-") as tmp:
            root = Path(tmp)
            src = root / "fake-source"
            src.mkdir()
            (src / "SKILL.md").write_text("# fake skill\n", encoding="utf-8")
            roles = src / "roles"
            roles.mkdir()
            for name in prep.ROLES:
                (roles / (name + ".toml")).write_text(
                    'name = "{}"\nmodel = "gpt-6.1-terra"\nmodel_reasoning_effort = "high"\n'.format(
                        name.replace("-", "_")), encoding="utf-8")
            out = root / "trial"
            result = prep.prepare(src, out)
            self.assertEqual(result["skill_files"] >= 1, True)
            self.assertEqual(prep.verify(out)["role_models"]["control"]["model"], "gpt-6-luna")
            self.assertIn('model = "gpt-6.1-terra"',
                          (src / "roles" / "sol-advisor-standard.toml").read_text())
            self.assertIn('model = "gpt-6-luna"',
                          (out / "control" / "agents" / "sol-advisor-standard.toml").read_text())
            self.assertIn('model = "gpt-6.1-terra"',
                          (out / "terra" / "agents" / "sol-advisor-standard.toml").read_text())
            with self.assertRaises(FileExistsError):
                prep.prepare(src, out)
            (out / "control" / "agents" / "sol-verifier-routine.toml").write_text("bad")
            with self.assertRaises(ValueError):
                prep.verify(out)

    def row(self, arm):
        return dict(
            case_id="T01", arm=arm, project="example", base_sha="abc123",
            prompt_sha256="deadbeef", initial_route="standard", standard_calls="1",
            observed_model=analyzer.ARMS[arm][0], observed_effort=analyzer.ARMS[arm][1],
            verifier_model="gpt-6-luna", verifier_effort="xhigh",
            verdict="pass", acceptance_pass="1", elapsed_sec="10",
            input_tokens="100", output_tokens="25", retries="0",
            worker_calls="1", verifier_calls="1", notes="")

    def make_csv(self, path, rows):
        with path.open("w", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=analyzer.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_valid_pair_and_model_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "result.csv"
            self.make_csv(csv_path, [self.row("control"), self.row("terra")])
            pair = analyzer.load(csv_path)
            yes, why = analyzer.valid_pair(pair["T01"])
            self.assertTrue(yes, why)
            self.assertIn("comparable: 1", analyzer.report(pair))

    def test_no_terra_route_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "result.csv"
            terra = self.row("terra")
            terra["initial_route"] = "routine"
            self.make_csv(csv_path, [self.row("control"), terra])
            report = analyzer.report(analyzer.load(csv_path))
            self.assertIn("comparable: 0", report)
            self.assertIn("did not naturally select standard", report)

    def test_wrong_model_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.csv"
            new = self.row("terra")
            new["observed_model"] = "gpt-6-luna"
            self.make_csv(path, [self.row("control"), new])
            self.assertIn("observed model/effort differs", analyzer.report(analyzer.load(path)))

    def test_mismatch_and_unpaired_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.csv"
            c = self.row("control")
            t = self.row("terra")
            t["base_sha"] = "different"
            self.make_csv(path, [c, t])
            with self.assertRaises(ValueError):
                analyzer.load(path)
            self.make_csv(path, [c])
            with self.assertRaises(ValueError):
                analyzer.load(path)

    def test_missing_tokens_not_treated_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.csv"
            t = self.row("terra")
            t["input_tokens"] = ""
            t["output_tokens"] = ""
            self.make_csv(path, [self.row("control"), t])
            report = analyzer.report(analyzer.load(path))
            self.assertIn("Only 0 pairs have complete", report)
            self.assertIn("comparable: 1", report)


if __name__ == "__main__":
    unittest.main()
