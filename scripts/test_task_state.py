#!/usr/bin/env python3
"""Offline tests: long-task snapshots never touch a business repository."""
import importlib.util
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("task_state", ROOT / "task_state.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="magent-task-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "codex-home" / "magent-tasks"
        self.business_repo = Path(self.temp.name) / "business"
        self.business_repo.mkdir()

    def test_lifecycle_and_verification_gate(self):
        s = mod.init_task(self.root, self.business_repo, "Fix API", "src/api")
        self.assertEqual(s["revision"], 0)
        self.assertEqual(mod.read_task(self.root, s["task_id"])["objective"], "Fix API")
        with self.assertRaises(ValueError):
            mod.close_task(self.root, s["task_id"], 0)
        s = mod.checkpoint(self.root, s["task_id"], 0, "implementation",
                           ["API implemented"], ["run independent verifier"], verdict="pending", route="standard")
        self.assertEqual(s["revision"], 1)
        with self.assertRaises(ValueError):
            mod.checkpoint(self.root, s["task_id"], 0, "verification")
        s = mod.checkpoint(self.root, s["task_id"], 1, "verification", ["Tests passed"],
                           [], verdict="pass", retry_count=0)
        closed = mod.close_task(self.root, s["task_id"], 2)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(mod.list_tasks(self.root, self.business_repo)[0]["status"], "closed")
        self.assertEqual(list(self.business_repo.iterdir()), [])
        with self.assertRaises(ValueError):
            mod.checkpoint(self.root, s["task_id"], 3)

    def test_blocker_blocks_close_and_requires_reason(self):
        s = mod.init_task(self.root, self.business_repo, "Fix API", "src")
        with self.assertRaises(ValueError):
            mod.checkpoint(self.root, s["task_id"], 0, "blocked")
        s = mod.checkpoint(self.root, s["task_id"], 0, "blocked", blocker="credentials unavailable", verdict="blocked")
        with self.assertRaises(ValueError):
            mod.close_task(self.root, s["task_id"], s["revision"])

    def test_invalid_id_and_symlink(self):
        with self.assertRaises(ValueError):
            mod.read_task(self.root, "../escape")
        s = mod.init_task(self.root, self.business_repo, "Test", "src")
        path = self.root / (s["task_id"] + ".json")
        if hasattr(os, "symlink") and os.name != "nt":
            path.unlink()
            path.symlink_to(Path(self.temp.name) / "missing")
            with self.assertRaises(ValueError):
                mod.read_task(self.root, s["task_id"])

    def test_distinct_task_ids_and_permissions(self):
        a = mod.init_task(self.root, self.business_repo, "One", "a")
        b = mod.init_task(self.root, self.business_repo, "Two", "b")
        self.assertNotEqual(a["task_id"], b["task_id"])
        if os.name != "nt":
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / (a["task_id"] + ".json")).stat().st_mode & 0o777, 0o600)

    def test_resume_revision_and_list_filter(self):
        a = mod.init_task(self.root, self.business_repo, "One", "a")
        b = mod.init_task(self.root, Path(self.temp.name) / "other", "Two", "b")
        self.assertEqual([x["task_id"] for x in mod.list_tasks(self.root, self.business_repo)], [a["task_id"]])
        with self.assertRaises(ValueError):
            mod.checkpoint(self.root, b["task_id"], 22, "planning")
        self.assertEqual(mod.read_task(self.root, b["task_id"])["revision"], 0)

    def test_cli_smoke(self):
        with patch.dict(os.environ, {"MAGENT_TASKS_DIR": str(self.root)}):
            self.assertEqual(mod.main(["init", "--repo", str(self.business_repo),
                                       "--objective", "work", "--scope", "src"]), 0)
            self.assertEqual(mod.main(["list", "--repo", str(self.business_repo)]), 0)


if __name__ == "__main__":
    unittest.main()
