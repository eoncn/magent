#!/usr/bin/env python3
"""Offline end-to-end lifecycle tests. CCB and codex queue are local fixtures."""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

if os.name == "nt":
    from review_windows import identity as windows_identity, group_alive as windows_group_alive

ENTRY = Path(__file__).resolve().with_name("claude-review.sh")
BASH = shutil.which("bash")
VALID = {"verdict": "ship", "summary": "Fixture reviewed", "findings": []}
FINDING = {"severity": "high", "file": "tracked.txt", "line": 1,
           "issue": "Fixture defect", "recommendation": "Repair it"}

CCB = r'''
import json, os, pathlib, signal, subprocess, sys, time
args = sys.argv[1:]
packet = sys.stdin.read()
control = json.loads(pathlib.Path(os.environ['FIXTURE_MODE']).read_text())
with open(os.environ['FIXTURE_ARGS'], 'a') as out:
    out.write(json.dumps({'args': args, 'packet': packet, 'pid': os.getpid(),
                          'has_codex_thread': 'CODEX_THREAD_ID' in os.environ,
                          'has_shell_startup': any(key in os.environ for key in ('BASH_ENV', 'ENV'))}) + '\n')
print('fixture progress: review started', file=sys.stderr, flush=True)
mode = control.get('mode', 'success')
if mode in ('mutation', 'mutation_timeout'):
    pathlib.Path('untracked.txt').write_text('reviewer mutation\n')
if mode == 'ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode in ('slow', 'mutation_timeout', 'ignore_term'):
    time.sleep(30)
if mode == 'overdue':
    time.sleep(0.8)
if mode == 'exit':
    sys.exit(7)
if mode == 'invalid_json':
    print('not json')
    sys.exit(0)
result = {'verdict': 'ship', 'summary': 'Fixture reviewed', 'findings': []}
if mode in ('fix-first', 'rethink'):
    result.update(verdict=mode, findings=[{'severity':'high','file':'tracked.txt','line':1,
                  'issue':'Fixture defect','recommendation':'Repair it'}])
envelope = {'type':'result','subtype':'success','is_error':False,'structured_output':result}
if mode == 'error_envelope': envelope['is_error'] = True
if mode == 'bad_schema': del result['summary']
print(json.dumps(envelope))
'''

CODEX = r'''
import json, os, pathlib, sys
args = sys.argv[1:]
if args == ['queue', '--help']:
    if os.environ.get('FIXTURE_NO_QUEUE'):
        print('unsupported queue')
    else:
        print('queue --thread THREAD --message TEXT')
    sys.exit(0)
assert args[0] == 'queue'
message = args[args.index('--message') + 1]
prefix, payload = message.split('\n', 1)
assert prefix == '$magent internal callback'
item = {'thread': args[args.index('--thread') + 1], 'message': json.loads(payload)}
with open(os.environ['FIXTURE_QUEUE'], 'a') as out:
    out.write(json.dumps(item) + '\n')
if pathlib.Path(os.environ['FIXTURE_QUEUE_MODE']).read_text() == 'fail':
    sys.exit(9)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="magent-runner-test-")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "tracked.txt").write_text("original tracked\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        (self.repo / "untracked.txt").write_text("original untracked\n")
        binary = self.root / "bin"
        binary.mkdir()
        for name, code in (("ccb", CCB), ("codex", CODEX)):
            path = binary / name
            if os.name == "nt":
                source = binary / (name + ".py")
                source.write_bytes(code.encode("utf-8"))
                launcher = '#!/usr/bin/env bash\nexec "' + Path(sys.executable).as_posix() + '" "' + source.as_posix() + '" "$@"\n'
                path.write_bytes(launcher.encode("utf-8"))
            else:
                path.write_text("#!" + sys.executable + "\n" + code)
            path.chmod(0o755)
        self.mode = self.root / "mode.json"
        self.queue_mode = self.root / "queue-mode"
        self.mode.write_text('{"mode":"success"}')
        self.queue_mode.write_text("success")
        self.args_log = self.root / "args.jsonl"
        self.queue_log = self.root / "queue.jsonl"
        self.runs = self.root / "runs"
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                        CLAUDE_CCB_BIN=str(binary / "ccb"), CLAUDE_CODEX_BIN=str(binary / "codex"),
                        MAGENT_REVIEW_RUNS_DIR=str(self.runs), FIXTURE_MODE=str(self.mode),
                        FIXTURE_ARGS=str(self.args_log), FIXTURE_QUEUE=str(self.queue_log),
                        FIXTURE_QUEUE_MODE=str(self.queue_mode), CODEX_THREAD_ID="fixture-parent-thread")
        for key in ("MAGENT_REVIEW_SESSION_ID", "MAGENT_REVIEW_RESUME", "CLAUDE_REVIEW_MODEL", "CLAUDE_REVIEW_EFFORT"):
            self.env.pop(key, None)
        self.children = []

    def tearDown(self):
        # Only terminate workers created by this test, never arbitrary state PIDs.
        for pid in self.children:
            if self.alive(pid):
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while self.alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.03)
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    @staticmethod
    def alive(pid):
        if os.name == "nt":
            return windows_identity(pid) is not None
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
        return result.returncode == 0 and bool(result.stdout.strip()) and not result.stdout.lstrip().startswith("Z")

    def call(self, *args, code=0, packet="Review fixture\n", env=None):
        result = subprocess.run([BASH, "--noprofile", "--norc", ENTRY.as_posix(), "--claude-review",
                                 *map(str, args)], cwd=self.repo,
                                env=env or self.env, input=packet, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, code, (args, result.stdout, result.stderr))
        return json.loads(result.stdout) if code == 0 else result

    def start(self, mode="success", eta=5, timeout=10, explicit_thread=True, action="final"):
        self.mode.write_text(json.dumps({"mode": mode}))
        args = [action, "--detach", "--eta-seconds", eta, "--timeout-seconds", timeout]
        if explicit_thread:
            args += ["--thread-id", "fixture-explicit-thread"]
        info = self.call(*args)
        self.children.append(info["worker_pid"])
        self.assertEqual(info["status"], "running")
        return Path(info["run_dir"])

    def state(self, run):
        deadline = time.monotonic() + 2
        while True:
            try:
                return json.loads((run / "state.json").read_text())
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def record(self, run):
        return self.state(run)["attempts"][-1]

    def wait(self, run, status="completed"):
        deadline = time.monotonic() + (30 if os.name == "nt" else 12)
        while time.monotonic() < deadline:
            record = self.record(run)
            callback = record.get("callbacks", {}).get(status, {}).get("status")
            if record["status"] == status and callback in ("sent", "failed") and not self.alive(record["worker_pid"]):
                return record
            time.sleep(0.03)
        self.fail(("review did not settle", self.state(run), (run / "attempt-1/supervisor.log").read_text()))

    def wait_for_ccb(self, run):
        deadline = time.monotonic() + (20 if os.name == "nt" else 8)
        while time.monotonic() < deadline:
            if self.args_log.exists() and self.record(run).get("cli_identity"):
                return
            time.sleep(0.03)
        self.fail("CCB did not start")

    def log(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_success_callback_and_private_artifacts(self):
        run = self.start()
        record = self.wait(run)
        self.assertEqual(json.loads((run / "attempt-1/result.json").read_text()), VALID)
        item = self.log(self.queue_log)[-1]
        self.assertEqual(item["thread"], "fixture-explicit-thread")
        self.assertEqual(item["message"]["event"], "completed")
        self.assertEqual(item["message"]["run_id"], run.name)
        self.assertEqual(item["message"]["run_dir"], str(run))
        self.assertEqual(item["message"]["attempt"], 1)
        self.assertEqual(record["before_fingerprint"], record["after_fingerprint"])
        if os.name != "nt":
            self.assertEqual(run.stat().st_mode & 0o777, 0o700)
        for path in run.rglob("*"):
            if path.is_file() and os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600, str(path))
        self.assertIn("fixture progress", Path(record["progress_log"]).read_text())

    def test_request_parameters_and_scrubbed_ccb_environment(self):
        run = self.start()
        self.wait(run)
        entry = self.log(self.args_log)[0]
        args = entry["args"]
        self.assertFalse(entry["has_codex_thread"])
        self.assertNotIn("--no-session-persistence", args)
        self.assertEqual(args[args.index("--session-id") + 1], self.record(run)["session_id"])
        for flag in ("--progress", "--disable-slash-commands", "--no-chrome", "--strict-mcp-config"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(args[args.index("--setting-sources") + 1], "user")
        self.assertTrue(json.loads(args[args.index("--settings") + 1])["sandbox"]["failIfUnavailable"])
        self.assertIn("=== STAGED DIFF ===", entry["packet"])

    def test_commitment_and_environment_thread(self):
        run = self.start(action="commitment", explicit_thread=False)
        self.wait(run)
        self.assertEqual(self.state(run)["mode"], "commitment")
        self.assertEqual(self.log(self.queue_log)[-1]["thread"], "fixture-parent-thread")

    def test_material_verdicts_are_preserved(self):
        for verdict in ("fix-first", "rethink"):
            with self.subTest(verdict=verdict):
                run = self.start(mode=verdict)
                self.wait(run)
                self.assertEqual(json.loads((run / "attempt-1/result.json").read_text())["verdict"], verdict)
                self.call("cleanup", "--run-dir", run, code=1)

    def test_failures_callback_without_acceptance_result(self):
        for mode, exit_code in (("exit", 7), ("invalid_json", 31), ("error_envelope", 31), ("bad_schema", 33)):
            with self.subTest(mode=mode):
                run = self.start(mode=mode)
                record = self.wait(run, "blocked")
                self.assertEqual(record["exit_code"], exit_code)
                self.assertFalse((run / "attempt-1/result.json").exists())
                self.call("cleanup", "--run-dir", run, code=1)

    def test_mutation_is_blocked(self):
        run = self.start(mode="mutation")
        record = self.wait(run, "blocked")
        self.assertEqual(record["exit_code"], 30)
        self.assertNotEqual(record["before_fingerprint"], record["after_fingerprint"])

    def test_timeout_callback_and_post_kill_fingerprint(self):
        run = self.start(mode="slow", timeout=0.8)
        record = self.wait(run, "blocked")
        self.assertEqual(record["exit_code"], 124)
        self.assertIn("after_fingerprint", record)
        self.assertFalse(self.alive(record["cli_pid"]))

    def test_timeout_does_not_hide_a_mutation(self):
        run = self.start(mode="mutation_timeout", timeout=0.8)
        record = self.wait(run, "blocked")
        self.assertEqual(record["exit_code"], 30)

    def test_overdue_notification_and_live_progress(self):
        run = self.start(mode="overdue", eta=0.3)
        self.wait_for_ccb(run)
        self.assertIn("fixture progress", Path(self.record(run)["progress_log"]).read_text())
        self.wait(run)
        events = [item["message"]["event"] for item in self.log(self.queue_log)]
        self.assertIn("overdue", events)
        self.assertIn("completed", events)

    def test_stop_and_resume_reuses_persistent_session(self):
        run = self.start(mode="slow")
        self.wait_for_ccb(run)
        old_session = self.record(run)["session_id"]
        self.call("stop", "--run-dir", run)
        self.wait(run, "stopped")
        self.mode.write_text('{"mode":"success"}')
        info = self.call("resume", "--run-dir", run, "--detach")
        self.children.append(info["worker_pid"])
        record = self.wait(run)
        self.assertEqual(record["attempt"], 2)
        self.assertEqual(record["session_id"], old_session)
        self.assertEqual(record["resumed_from_attempt"], 1)
        args = self.log(self.args_log)[-1]["args"]
        self.assertEqual(args[args.index("--resume") + 1], old_session)
        self.assertNotIn("--session-id", args)

    def test_stop_escalates_for_sigterm_ignoring_ccb(self):
        run = self.start(mode="ignore_term")
        self.wait_for_ccb(run)
        ccb_pid = self.log(self.args_log)[-1]["pid"]
        self.call("stop", "--run-dir", run)
        self.wait(run, "stopped")
        self.assertFalse(self.alive(ccb_pid))

    @unittest.skipIf(os.name == "nt", "Windows job ownership has a dedicated recovery test")
    def test_orphaned_worker_recovery(self):
        run = self.start(mode="slow")
        self.wait_for_ccb(run)
        record = self.record(run)
        os.kill(record["worker_pid"], signal.SIGKILL)
        deadline = time.monotonic() + 3
        while self.alive(record["worker_pid"]) and time.monotonic() < deadline:
            time.sleep(0.03)
        self.call("stop", "--run-dir", run)
        stopped = self.record(run)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["before_fingerprint"], stopped["after_fingerprint"])
        self.assertFalse(self.alive(record["cli_pid"]))

    def test_callback_thread_cannot_be_redirected(self):
        run = self.start()
        self.wait(run)
        self.call("retry-callback", "--run-dir", run, "--thread-id", "other-thread", code=22)
        self.assertEqual(len(self.log(self.queue_log)), 1)

    def test_unicode_packet_and_opaque_callback_parameters(self):
        thread = 'fixture-"quote"\\path & $(printf marker)'
        packet = "验证外部审核，保留中文和 emoji 🌍\n"
        env = dict(self.env, CODEX_THREAD_ID=thread)
        info = self.call("final", "--detach", "--eta-seconds", 5, env=env, packet=packet)
        self.children.append(info["worker_pid"])
        run = Path(info["run_dir"])
        self.wait(run)
        self.assertEqual(self.log(self.queue_log)[-1]["thread"], thread)
        self.assertTrue(self.log(self.args_log)[-1]["packet"].startswith(packet))

    @unittest.skipIf(os.name == "nt", "POSIX process-group fixture")
    def test_dead_worker_and_cli_are_audited_before_recovery(self):
        for mutated in (False, True):
            with self.subTest(mutated=mutated):
                run = self.start(mode="slow")
                self.wait_for_ccb(run)
                record = self.record(run)
                os.kill(record["worker_pid"], signal.SIGKILL)
                os.killpg(record["cli_pid"], signal.SIGKILL)
                deadline = time.monotonic() + 3
                while any(self.alive(record[key]) for key in ("worker_pid", "cli_pid")):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.03)
                if mutated:
                    (self.repo / "untracked.txt").write_text("orphaned change\n")
                self.call("stop", "--run-dir", run)
                recovered = self.record(run)
                self.assertEqual(recovered["status"], "blocked" if mutated else "stopped")
                self.assertEqual(recovered["exit_code"], 30 if mutated else 130)
                self.assertIn("after_fingerprint", recovered)

    def test_blocked_resume_keeps_history(self):
        run = self.start(mode="exit")
        self.wait(run, "blocked")
        self.mode.write_text('{"mode":"success"}')
        info = self.call("resume", "--run-dir", run, "--detach")
        self.children.append(info["worker_pid"])
        self.wait(run)
        self.assertEqual(self.state(run)["attempts"][0]["exit_code"], 7)

    def test_cannot_resume_active_attempt(self):
        run = self.start(mode="slow")
        self.wait_for_ccb(run)
        self.call("resume", "--run-dir", run, "--detach", code=1)
        self.assertEqual(len(self.state(run)["attempts"]), 1)

    def test_callback_retry_does_not_rerun_ccb(self):
        self.queue_mode.write_text("fail")
        run = self.start()
        record = self.wait(run)
        self.assertEqual(record["callbacks"]["completed"]["status"], "failed")
        self.assertEqual(len(self.log(self.queue_log)), 3)
        self.call("cleanup", "--run-dir", run, code=1)
        self.queue_mode.write_text("success")
        self.call("retry-callback", "--run-dir", run)
        self.assertEqual(self.record(run)["callbacks"]["completed"]["status"], "sent")
        self.assertEqual(len(self.log(self.args_log)), 1)
        messages = self.log(self.queue_log)
        self.assertEqual(messages[0]["message"]["delivery_id"], messages[-1]["message"]["delivery_id"])
        self.call("retry-callback", "--run-dir", run)
        self.assertEqual(len(self.log(self.queue_log)), 4)

    def test_cleanup_and_inspect(self):
        run = self.start()
        self.wait(run)
        self.assertEqual(self.call("inspect", "--run-dir", run)["run_id"], run.name)
        self.assertEqual(self.call("cleanup", "--run-dir", run)["status"], "cleaned")
        self.assertFalse(run.exists())

    def test_reject_external_or_symlink_run(self):
        self.call("inspect", "--run-dir", self.root, code=1)
        run = self.start()
        self.wait(run)
        alias = self.runs / "alias"
        try:
            alias.symlink_to(run, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows user lacks symbolic-link creation privilege")
            raise
        self.call("inspect", "--run-dir", alias, code=1)

    def test_stop_refuses_a_mismatched_process(self):
        run = self.start(mode="slow")
        self.wait_for_ccb(run)
        state = self.state(run)
        original = state["attempts"][-1]["worker_identity"]
        state["attempts"][-1]["worker_identity"] = "wrong identity"
        (run / "state.json").write_text(json.dumps(state))
        try:
            self.call("stop", "--run-dir", run, code=1)
            self.assertTrue(self.alive(state["attempts"][-1]["worker_pid"]))
        finally:
            state["attempts"][-1]["worker_identity"] = original
            (run / "state.json").write_text(json.dumps(state))

    def test_invalid_run_identity_is_rejected(self):
        run = self.start()
        self.wait(run)
        state = self.state(run)
        state["run_id"] = str(uuid.uuid4())
        (run / "state.json").write_text(json.dumps(state))
        self.call("inspect", "--run-dir", run, code=1)

    def test_legacy_attempt_without_session_cannot_resume(self):
        run = self.start(mode="exit")
        self.wait(run, "blocked")
        state = self.state(run)
        del state["attempts"][-1]["session_id"]
        (run / "state.json").write_text(json.dumps(state))
        self.call("resume", "--run-dir", run, "--detach", code=1)

    def test_missing_dependencies_fail_before_launch(self):
        for key in ("CLAUDE_CCB_BIN", "CLAUDE_CODEX_BIN"):
            env = dict(self.env, **{key: str(self.root / "missing")})
            self.call("final", "--detach", "--eta-seconds", 5, code=20, env=env)
        self.assertFalse(self.args_log.exists())

    def test_unsupported_codex_queue_fails_before_launch(self):
        env = dict(self.env, FIXTURE_NO_QUEUE="1")
        self.call("final", "--detach", "--eta-seconds", 5, code=20, env=env)
        self.assertFalse(self.args_log.exists())

    def test_detach_requires_thread_and_eta(self):
        self.call("final", "--detach", code=22)
        env = dict(self.env)
        env.pop("CODEX_THREAD_ID")
        self.call("final", "--detach", "--eta-seconds", 5, code=22, env=env)

    def test_artifacts_inside_repository_are_rejected(self):
        env = dict(self.env, MAGENT_REVIEW_RUNS_DIR=str(self.repo / "runs"))
        self.call("final", "--detach", "--eta-seconds", 5, code=22, env=env)
        self.assertFalse((self.repo / "runs").exists())

    def test_empty_packet_rejected(self):
        self.call("final", "--detach", "--eta-seconds", 5, packet=" \n", code=22)

    def test_invalid_deadlines_and_model_are_rejected(self):
        for value in ("0", "-1", "nan", "inf"):
            self.call("final", "--detach", "--eta-seconds", value, code=2)
        for key, value in (("CLAUDE_REVIEW_MODEL", "-invalid"), ("CLAUDE_REVIEW_EFFORT", "invalid")):
            self.call("final", "--detach", "--eta-seconds", 5, code=22,
                      env=dict(self.env, **{key: value}))

    def test_supervised_synchronous_call_has_no_implicit_callback(self):
        env = dict(self.env, CLAUDE_CODEX_BIN=str(self.root / "missing"))
        result = self.call("final", "--repo", self.repo, env=env)
        self.assertEqual(result, VALID)
        self.assertFalse(self.queue_log.exists())

    def test_internal_shells_do_not_run_host_startup_hooks(self):
        hook = self.root / "startup.sh"
        marker = self.root / "startup.log"
        hook.write_bytes(b'printf "startup\\n" >> "$FIXTURE_STARTUP_MARKER"\n')
        env = dict(self.env, BASH_ENV=hook.as_posix(), ENV=hook.as_posix(), FIXTURE_STARTUP_MARKER=str(marker))
        result = self.call("final", "--repo", self.repo, env=env)
        self.assertEqual(result, VALID)
        # Only the caller's outer Bash reads its own BASH_ENV. Worker-owned
        # shells must not run host startup commands or leak hooks into CCB.
        self.assertEqual(marker.read_text().splitlines(), ["startup"])
        self.assertFalse(self.log(self.args_log)[0]["has_shell_startup"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle")
    def test_windows_job_cleans_up_after_worker_crash(self):
        run = self.start(mode="slow")
        self.wait_for_ccb(run)
        record = self.record(run)
        os.kill(record["worker_pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while self.alive(record["worker_pid"]) or windows_group_alive(record["cli_pid"], record["nonce"]):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.03)
        (self.repo / "untracked.txt").write_text("orphaned change\n")
        self.call("stop", "--run-dir", run)
        self.assertEqual(self.record(run)["status"], "blocked")
        self.assertEqual(self.record(run)["exit_code"], 30)

    def test_explicit_opt_in_required_before_any_side_effects(self):
        for args in ([], ["final"], ["final", "--claude-review"], ["--unknown"]):
            env = dict(self.env, CLAUDE_CCB_BIN="missing-ccb", CLAUDE_CODEX_BIN="missing-codex")
            result = subprocess.run([BASH, "--noprofile", "--norc", ENTRY.as_posix(), *args],
                                    cwd=self.repo, env=env, input="", text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 18, result.stderr)
            self.assertFalse(result.stdout)
        self.assertFalse(self.runs.exists())
        self.assertFalse(self.args_log.exists())
        self.assertFalse(self.queue_log.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
