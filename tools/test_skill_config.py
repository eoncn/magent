#!/usr/bin/env python3
"""Offline checks for magent model routing and role registration."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rolecheck", ROOT / "tools/check_role_registration.py")
rolecheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rolecheck)


class RoutingTests(unittest.TestCase):
    def test_role_templates_match_expected_models(self):
        for name, (filename, model, effort) in rolecheck.EXPECTED.items():
            body = (ROOT / "roles" / filename).read_text(encoding="utf-8")
            self.assertEqual(rolecheck.assignment(body, "name"), name)
            self.assertEqual(rolecheck.assignment(body, "model"), model)
            self.assertEqual(rolecheck.assignment(body, "model_reasoning_effort"), effort)

    def test_skill_routes_and_external_opt_in(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for name in rolecheck.EXPECTED:
            self.assertIn(name, text)
        self.assertIn("first argument", text)
        self.assertIn("--claude-review", text)
        self.assertIn("independent", text.lower())

    def test_registration_check(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "agents").mkdir()
            config = []
            for name, (filename, _, _) in rolecheck.EXPECTED.items():
                (home / "agents" / filename).write_bytes((ROOT / "roles" / filename).read_bytes())
                config.append(f'[agents.{name}]\nconfig_file = "agents/{filename}"\n')
            (home / "config.toml").write_text("\n".join(config), encoding="utf-8")
            self.assertEqual(rolecheck.validate(home), [])
            (home / "config.toml").write_text("[agents.other]\n", encoding="utf-8")
            self.assertTrue(any("missing" in message for message in rolecheck.validate(home)))

    def test_bad_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "agents").mkdir()
            lines = []
            for name, (filename, _, _) in rolecheck.EXPECTED.items():
                content = (ROOT / "roles" / filename).read_text(encoding="utf-8")
                if name == "sol_advisor_standard":
                    content = content.replace("gpt-6.1-terra", "wrong-model")
                (home / "agents" / filename).write_text(content, encoding="utf-8")
                lines.append(f'[agents.{name}]\nconfig_file = "agents/{filename}"\n')
            (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
            self.assertTrue(any("wrong-model" in message for message in rolecheck.validate(home)))


if __name__ == "__main__":
    unittest.main()
