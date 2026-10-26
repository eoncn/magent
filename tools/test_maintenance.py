#!/usr/bin/env python3
"""Offline installation, rollback, sync and shared-checkout state regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import install
import sync
sys.path.insert(0, str(install.SOURCE / "scripts"))
import review_runner


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="magent-maintenance-")
        self.root = Path(self.temp.name)
        self.homes = [self.root / "one", self.root / "two"]

    def tearDown(self):
        for home in self.homes:
            entry = home / "skills/magent"
            if install.linked_to(entry, install.SOURCE):
                install.remove_entry(entry)
        self.temp.cleanup()

    def old_installations(self):
        for home in self.homes:
            target = home / "skills/magent"
            target.mkdir(parents=True)
            (target / "old.txt").write_text("old", encoding="utf-8")

    def test_dry_run_never_creates_directories(self):
        result = install.install(self.homes, dry_run=True)
        self.assertEqual(len(result["changes"]), 2)
        self.assertTrue(all(not p.exists() for p in self.homes))

    def test_backup_shared_links_and_idempotence(self):
        self.old_installations()
        result = install.install(self.homes)
        self.assertEqual(len(result["backups"]), 2)
        for path in result["backups"]:
            self.assertEqual((Path(path) / "old.txt").read_text(), "old")
        for home in self.homes:
            self.assertTrue(install.linked_to(home / "skills/magent", install.SOURCE))
            self.assertEqual((home / "skills/magent/SKILL.md").read_bytes(),
                             (install.SOURCE / "SKILL.md").read_bytes())
        self.assertEqual(install.install(self.homes)["changes"], [])

    def test_second_home_failure_restores_both_originals(self):
        self.old_installations()
        original = install.link_directory
        calls = []
        def fail_second(target, link):
            calls.append(link)
            if len(calls) == 2:
                raise OSError("simulated link failure")
            original(target, link)
        with patch.object(install, "link_directory", fail_second):
            with self.assertRaises(OSError):
                install.install(self.homes)
        for home in self.homes:
            target = home / "skills/magent"
            self.assertFalse(install.linked_to(target, install.SOURCE))
            self.assertEqual((target / "old.txt").read_text(), "old")

    def test_roles_backup_without_touching_host_config(self):
        home = self.homes[0]
        (home / "agents").mkdir(parents=True)
        config = home / "config.toml"
        config.write_text("# keep all host settings\n")
        (home / "agents/sol-verifier-routine.toml").write_text("old role")
        result = install.install([home], include_roles=True)
        self.assertEqual(config.read_text(), "# keep all host settings\n")
        for source in (install.SOURCE / "roles").glob("*.toml"):
            self.assertEqual((home / "agents" / source.name).read_bytes(), source.read_bytes())
        self.assertTrue(any(Path(p).read_text() == "old role" for p in result["backups"]))

    def test_shared_checkout_state_stays_outside_source(self):
        home_env = {"HOME": str(Path.home()), "USERPROFILE": str(Path.home())}
        with patch.dict(os.environ, home_env, clear=True):
            self.assertEqual(review_runner.runs_root(), (Path.home() / ".codex/magent-runs").resolve())
        with patch.dict(os.environ, dict(home_env, CODEX_HOME=str(self.homes[1])), clear=True):
            self.assertEqual(review_runner.runs_root(), (self.homes[1] / "magent-runs").resolve())
        with patch.dict(os.environ, dict(home_env, MAGENT_REVIEW_RUNS_DIR=str(self.root / "runs")), clear=True):
            self.assertEqual(review_runner.runs_root(), (self.root / "runs").resolve())
        direct = self.root / "profile/skills/magent/scripts/claude-review.sh"
        with patch.object(review_runner, "SCRIPT", direct), patch.dict(os.environ, home_env, clear=True):
            self.assertEqual(review_runner.runs_root(), (self.root / "profile/magent-runs").resolve())


@unittest.skipUnless(shutil.which("git"), "Git is required")
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="magent-sync-test-")
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.run_git(self.root, "init", "--bare", str(self.origin))
        self.run_git(self.root, "clone", str(self.origin), str(self.source))
        self.run_git(self.source, "checkout", "-b", "main")
        self.configure(self.source)
        self.commit("one")
        self.run_git(self.source, "push", "-u", "origin", "main")
        self.run_git(self.root, "clone", "--branch", "main", str(self.origin), str(self.target))
        self.configure(self.target)
        self.before = self.run_git(self.target, "rev-parse", "HEAD")
        self.commit("two")
        self.expected = self.run_git(self.source, "rev-parse", "HEAD")

    def tearDown(self):
        def writable(function, path, info):
            os.chmod(path, 0o700)
            function(path)
        shutil.rmtree(self.root, onerror=writable)
        self.temp.cleanup()

    def run_git(self, cwd, *args):
        return subprocess.check_output(["git", "-C", str(cwd), *args],
                                       stderr=subprocess.PIPE, text=True).strip()

    def configure(self, repo):
        self.run_git(repo, "config", "user.name", "Fixture")
        self.run_git(repo, "config", "user.email", "fixture@example.invalid")
        self.run_git(repo, "config", "commit.gpgsign", "false")
        self.run_git(repo, "config", "core.hooksPath", "/dev/null")

    def commit(self, text):
        (self.source / "file.txt").write_text(text)
        self.run_git(self.source, "add", ".")
        self.run_git(self.source, "commit", "-m", text)

    def sync(self, dry_run=False):
        options = json.dumps({"path": str(self.target), "commit": self.expected,
                              "install": False, "dry_run": dry_run})
        program = sync.REMOTE.replace("OPTIONS", repr(options), 1)
        return subprocess.run([sys.executable, "-B", "-"], input=program, text=True, capture_output=True)

    def test_dry_run_does_not_fetch_or_merge(self):
        self.run_git(self.source, "push")
        result = self.sync(dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), self.before)
        self.assertEqual(self.run_git(self.target, "rev-parse", "origin/main"), self.before)

    def test_fast_forward_to_published_source_version(self):
        self.run_git(self.source, "push")
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), self.expected)

    def test_unpublished_source_stops_before_worktree_change(self):
        result = self.sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), self.before)

    def test_dirty_target_is_preserved(self):
        self.run_git(self.source, "push")
        (self.target / "local.txt").write_text("keep me")
        result = self.sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.target / "local.txt").read_text(), "keep me")
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), self.before)

    def test_divergent_target_is_preserved(self):
        self.run_git(self.source, "push")
        (self.target / "local.txt").write_text("local commit")
        self.run_git(self.target, "add", ".")
        self.run_git(self.target, "commit", "-m", "local")
        local_head = self.run_git(self.target, "rev-parse", "HEAD")
        result = self.sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), local_head)

    def test_target_ahead_of_published_version_is_preserved(self):
        self.run_git(self.source, "push")
        self.assertEqual(self.sync().returncode, 0)
        (self.target / "ahead.txt").write_text("extra local commit")
        self.run_git(self.target, "add", ".")
        self.run_git(self.target, "commit", "-m", "ahead")
        local_head = self.run_git(self.target, "rev-parse", "HEAD")
        result = self.sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.run_git(self.target, "rev-parse", "HEAD"), local_head)


if __name__ == "__main__":
    unittest.main(verbosity=2)
