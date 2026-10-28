#!/usr/bin/env python3
"""Offline, no-model tests for automatic Codex role registration and rollback."""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import install
import register_agents
from check_role_registration import EXPECTED, validate


@unittest.skipIf(register_agents._toml is None,
                 "Automatic registration needs Python 3.11+ or optional tomli")
class AutoRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="magent-auto-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "codex"

    def run_install(self, home=None):
        return install.install([home or self.home], include_roles=True, register_agents=True)

    def test_fresh_home_installs_skill_roles_and_config(self):
        result = self.run_install()
        self.assertFalse(result["dry_run"])
        self.assertTrue(install.linked_to(self.home / "skills/magent", install.SOURCE))
        self.assertEqual(validate(self.home), [])
        config = (self.home / "config.toml").read_text()
        self.assertEqual(config.count("[agents.sol_advisor_standard]"), 1)
        for name in EXPECTED:
            self.assertIn("[agents." + name + "]", config)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((self.home / "config.toml").stat().st_mode), 0o600)

    def test_preserves_host_settings_comments_bom_and_newlines(self):
        self.home.mkdir()
        path = self.home / "config.toml"
        original = b'\xef\xbb\xbf# my user settings\r\nmodel = "my-provider"\r\n[projects."/some"]\r\ntrust_level = "trusted"\r\n'
        path.write_bytes(original)
        self.run_install()
        result = path.read_bytes()
        self.assertTrue(result.startswith(original))
        self.assertIn(b'\r\n[agents.sol_advisor_routine]\r\n', result)
        self.assertEqual(validate(self.home), [])

    def test_repeat_is_byte_for_byte_noop(self):
        self.run_install()
        config = (self.home / "config.toml").read_bytes()
        second = self.run_install()
        self.assertEqual(second["changes"], [])
        self.assertEqual(config, (self.home / "config.toml").read_bytes())
        self.assertEqual(validate(self.home), [])

    def test_existing_correct_registration_kept(self):
        self.home.mkdir()
        path = self.home / "config.toml"
        path.write_text('# keep comments\n[agents.sol_advisor_routine]\n'
                        'config_file = "agents/sol-advisor-routine.toml"\n'
                        'description = "custom"\n', encoding="utf-8")
        self.run_install()
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count("[agents.sol_advisor_routine]"), 1)
        self.assertIn('description = "custom"', content)
        self.assertEqual(validate(self.home), [])

    def test_wrong_role_path_rejected_without_modification(self):
        self.home.mkdir()
        path = self.home / "config.toml"
        old = b'[agents.sol_advisor_routine]\nconfig_file = "custom/other.toml"\n'
        path.write_bytes(old)
        with self.assertRaisesRegex(ValueError, "conflicting registration|points to"):
            self.run_install()
        self.assertEqual(path.read_bytes(), old)
        self.assertFalse((self.home / "skills").exists())

    def test_invalid_toml_rejected_before_any_write(self):
        self.home.mkdir()
        path = self.home / "config.toml"
        path.write_text('model = "unterminated\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid TOML"):
            self.run_install()
        self.assertFalse((self.home / "skills").exists())

    def test_multi_agent_explicitly_disabled_rejected(self):
        self.home.mkdir()
        (self.home / "config.toml").write_text(
            "[agents]\nenabled = false\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "explicitly disabled"):
            self.run_install()
        self.assertFalse((self.home / "skills").exists())

    def test_dry_run_does_not_create_any_files(self):
        result = install.install([self.home], include_roles=True,
                                 register_agents=True, dry_run=True)
        self.assertTrue(any(item["kind"] == "config" for item in result["changes"]))
        self.assertFalse(self.home.exists())

    def test_roles_only_keeps_config_unmodified(self):
        self.home.mkdir()
        config = self.home / "config.toml"
        config.write_text("# original\n")
        install.install([self.home], include_roles=True, register_agents=False)
        self.assertEqual(config.read_text(), "# original\n")

    @unittest.skipIf(os.name == "nt", "Creating test symlinks may need Windows privileges")
    def test_config_symlink_refused(self):
        self.home.mkdir()
        target = self.root / "elsewhere.toml"
        target.write_text('model="keep"\n')
        (self.home / "config.toml").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.run_install()
        self.assertEqual(target.read_text(), 'model="keep"\n')

    def test_bad_second_home_preflight_keeps_first_unchanged(self):
        other = self.root / "other"
        other.mkdir()
        (other / "config.toml").write_text("invalid = [", encoding="utf-8")
        with self.assertRaises(ValueError):
            install.install([self.home, other], include_roles=True, register_agents=True)
        self.assertFalse(self.home.exists() or (self.home / "skills").exists())

    def test_second_home_failure_rolls_back_config_and_roles(self):
        other = self.root / "other"
        self.home.mkdir()
        original = b'approval_policy = "never"\n'
        (self.home / "config.toml").write_bytes(original)
        calls = 0
        real = install.link_directory

        def fail_second(source, temp):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-home failure")
            return real(source, temp)

        with patch.object(install, "link_directory", fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                install.install([self.home, other], include_roles=True, register_agents=True)
        self.assertEqual((self.home / "config.toml").read_bytes(), original)
        self.assertFalse((self.home / "skills/magent").exists())
        self.assertFalse((self.home / "agents").exists() and list((self.home / "agents").glob("*.toml")))
        self.assertFalse((other / "skills/magent").exists())

    def test_post_install_validator_failure_rolls_back(self):
        self.home.mkdir()
        old = b'# stay intact\n'
        (self.home / "config.toml").write_bytes(old)
        with patch.object(install, "validate", return_value=["injected invalid agent"]):
            with self.assertRaisesRegex(ValueError, "Post-install"):
                self.run_install()
        self.assertEqual((self.home / "config.toml").read_bytes(), old)
        self.assertFalse((self.home / "skills/magent").exists())

    def test_cli_default_auto_install_and_dry_run(self):
        cmd = [sys.executable, str(install.SOURCE / "tools/install.py"),
               "--home", str(self.home)]
        dry = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(self.home.exists())
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(validate(self.home), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
