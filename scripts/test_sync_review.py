#!/usr/bin/env python3
"""Offline integration tests: real Git/Python, mocked CCB; no external calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REAL_GIT = shutil.which("git")
BASH = shutil.which("bash")
WEIRD_NAME = 'line\nname-文件-β'.encode() if sys.platform == 'darwin' else b'line\nname-\xff'
PACKET = "USER OBJECTIVE: verify fixture\nPARENT VERIFICATION EVIDENCE: passed\n"
VALID = {"verdict": "ship", "summary": "Fixture reviewed", "findings": []}
FINDING = {"severity": "high", "file": "tracked.txt", "line": 1,
           "issue": "Fixture issue", "recommendation": "Fix fixture"}

FAKE_CCB = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
packet = sys.stdin.read()
assert packet.startswith(os.environ['AUDIT_PACKET']), repr(packet)
assert '=== STAGED DIFF ===' in packet and '=== UNSTAGED DIFF ===' in packet
args = sys.argv[1:]
assert args[args.index('--permission-mode') + 1] == 'dontAsk'
assert args[args.index('--model') + 1] == 'audit-model'
assert args[args.index('--effort') + 1] == 'high'
assert args[args.index('--output-format') + 1] == 'json'
assert '--no-session-persistence' not in args
assert '--session-id' in args or '--resume' in args
for flag in ('--progress', '--disable-slash-commands', '--no-chrome', '--strict-mcp-config'):
    assert flag in args
assert args[args.index('--setting-sources') + 1] == 'user'
assert args[args.index('--tools') + 1] == 'Read,Glob,Grep'
assert json.loads(args[args.index('--mcp-config') + 1]) == {'mcpServers': {}}
assert json.loads(args[args.index('--settings') + 1])['sandbox']['failIfUnavailable'] is True
assert json.loads(args[args.index('--json-schema') + 1])['additionalProperties'] is False
case = os.environ['AUDIT_CASE']
marker = pathlib.Path(os.environ['AUDIT_CALLED'])
marker.touch()
def git(*args, **kwargs):
    return subprocess.run([os.environ['AUDIT_REAL_GIT'], *args],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
if case in ('untracked_mutation', 'failure_after_mutation'):
    pathlib.Path('untracked.txt').write_text('changed untracked\n')
if case in ('tracked_mutation', 'assume_unchanged_mutation'):
    pathlib.Path('tracked.txt').write_text('changed tracked\n')
if case == 'staged_mutation':
    oid = git('hash-object', '-w', '--stdin', input=b'new staged value\n').stdout.strip().decode()
    git('update-index', '--cacheinfo', '100644', oid, 'tracked.txt')
if case == 'head_mutation':
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'reviewer mutation')
if case == 'branch_mutation':
    git('checkout', '-qb', 'reviewer-branch')
if case == 'symlink_mutation':
    link = pathlib.Path('link')
    link.unlink()
    link.symlink_to('different-missing-target')
if case == 'mode_mutation':
    pathlib.Path('tracked.txt').chmod(0o755)
if case == 'untracked_deleted':
    pathlib.Path('untracked.txt').unlink()
if case == 'untracked_created':
    pathlib.Path('new-untracked.txt').write_text('new\n')
if case == 'weird_name_mutation':
    name = 'line\nname-文件-β'.encode() if sys.platform == 'darwin' else b'line\nname-\xff'
    with open(name, 'wb') as out:
        out.write(b'changed\x00\xff')
if case == 'ignored_mutation':
    pathlib.Path('ignored-output').write_text('new build output\n')
if case in ('nested_mutation', 'submodule_mutation'):
    pathlib.Path('nested/untracked.txt').write_text('changed nested\n')
if case == 'after_special_file':
    pathlib.Path('tracked.txt').unlink()
    os.mkfifo('tracked.txt')
if case in ('cli_failure', 'failure_after_mutation', 'fingerprint_and_cli_failure'):
    print(os.environ['AUDIT_RAW'])
    sys.exit(7)
sys.stdout.write(os.environ['AUDIT_RAW'])
'''

FAKE_GIT = r'''#!/usr/bin/env python3
import os, pathlib, sys
case = os.environ.get('AUDIT_CASE')
if sys.argv[1:2] == ['ls-files']:
    called = pathlib.Path(os.environ['AUDIT_CALLED']).exists()
    if case == 'fingerprint_before_failure' or (
        case in ('fingerprint_after_failure', 'fingerprint_and_cli_failure') and called
    ):
        print('simulated Git fingerprint failure', file=sys.stderr)
        sys.exit(71)
os.execv(os.environ['AUDIT_REAL_GIT'], [os.environ['AUDIT_REAL_GIT'], *sys.argv[1:]])
'''


def envelope(output=None):
    return {"type": "result", "subtype": "success", "is_error": False,
            "structured_output": copy.deepcopy(VALID if output is None else output)}


def git(repo, *args):
    return subprocess.run([REAL_GIT, *args], cwd=repo, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def initialize(repo, committed=True):
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Audit")
    git(repo, "config", "user.email", "audit@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "/dev/null")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "tracked.txt").write_text("original tracked\n")
    (repo / ".gitignore").write_text("ignored-output\n")
    git(repo, "add", ".")
    if committed:
        git(repo, "commit", "-qm", "baseline")
    (repo / "untracked.txt").write_text("original untracked\n")


def run(script, root, number, name, expected=0, output=None, raw=None, mode="final"):
    work = root / str(number)
    work.mkdir()
    repo = work / "repo"
    initialize(repo, committed=name != "unborn")
    if name == "detached":
        git(repo, "checkout", "--detach", "-q")
    if name in ("symlink_mutation", "symlink_stable"):
        (repo / "link").symlink_to("missing-target")
    if name in ("weird_name_mutation", "weird_name_stable"):
        with open(os.fsencode(repo) + b'/' + WEIRD_NAME, "wb") as target:
            target.write(b"original\x00\xff")
    if name == "assume_unchanged_mutation":
        git(repo, "update-index", "--assume-unchanged", "tracked.txt")
    if name == "staged_mutation":
        (repo / "tracked.txt").write_text("staged value\n")
        git(repo, "add", "tracked.txt")
        (repo / "tracked.txt").write_text("worktree value\n")
    if name.startswith("nested_") or name.startswith("submodule_"):
        initialize(repo / "nested")
        if name.startswith("submodule_"):
            git(repo, "config", "-f", ".gitmodules", "submodule.nested.path", "nested")
            git(repo, "config", "-f", ".gitmodules", "submodule.nested.url", "./nested")
            git(repo, "add", ".gitmodules", "nested")
            git(repo, "commit", "-qm", "add fixture submodule")
    if name == "before_special_file":
        (repo / "tracked.txt").unlink()
        os.mkfifo(repo / "tracked.txt")
    cwd = repo
    if name in ("subdirectory", "relative_entry"):
        cwd = repo / "subdir"
        cwd.mkdir()
    if name == "outside_repo":
        cwd = work
    if name == "bare_repo":
        cwd = work / "bare.git"
        git(work, "init", "--bare", "-q", str(cwd))
    env = dict(os.environ, AUDIT_CASE=name, AUDIT_RAW=raw if raw is not None else json.dumps(envelope(output)),
               AUDIT_CALLED=str(work / "called"), AUDIT_PACKET=PACKET,
               AUDIT_REAL_GIT=REAL_GIT, CLAUDE_REVIEW_MODEL="audit-model",
               CLAUDE_REVIEW_EFFORT="high", PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
    env.pop("CLAUDE_CCB_BIN", None)
    if name == "custom_ccb_binary":
        custom = work / "custom ccb"
        custom.write_text(FAKE_CCB)
        custom.chmod(0o755)
        env["CLAUDE_CCB_BIN"] = str(custom)
    if name == "missing_custom_ccb_binary":
        env["CLAUDE_CCB_BIN"] = str(work / "missing ccb")
    if name.startswith("missing_command_"):
        missing = name.removeprefix("missing_command_")
        limited_bin = work / "limited-bin"
        limited_bin.mkdir()
        for command in ("ccb", "claude", "git", "python3", "cat"):
            if command != missing:
                target = root / "bin" / command if command in ("ccb", "claude", "git") else Path(shutil.which(command))
                (limited_bin / command).symlink_to(target)
        env["PATH"] = str(limited_bin)
    packet = " \n\t" if name == "empty_packet" else PACKET
    entry = os.path.relpath(script, cwd.resolve()) if name == "relative_entry" else str(script)
    completed = subprocess.run([BASH, "--noprofile", "--norc", entry, "--claude-review", mode], cwd=cwd, env=env,
                               input=packet, text=True, capture_output=True, timeout=30)
    assert completed.returncode == expected, (name, expected, completed.returncode,
                                              completed.stdout, completed.stderr)
    if expected:
        assert not completed.stdout, (name, "failure emitted stdout", completed.stdout)
        assert "ERROR:" in completed.stderr, (name, completed.stderr)
    else:
        assert json.loads(completed.stdout) == (VALID if output is None else output), (name, completed.stdout)
        assert not completed.stderr, (name, completed.stderr)
    if name == "failure_after_mutation":
        assert "exit status 7" in completed.stderr and "state changed" in completed.stderr
    if name == "fingerprint_and_cli_failure":
        assert "exit status 7" in completed.stderr and "after review" in completed.stderr
    if name in ("empty_packet", "fingerprint_before_failure", "before_special_file", "outside_repo", "bare_repo", "invalid_mode"):
        assert not (work / "called").exists(), (name, "CCB unexpectedly invoked")


def cases():
    yield "baseline", 0, {}
    yield "commitment", 0, {"mode": "commitment"}
    for verdict in ("ship", "fix-first", "rethink"):
        output = dict(VALID, verdict=verdict, findings=[] if verdict == 'ship' else [FINDING])
        yield "verdict_" + verdict, 0, {"output": output}
        yield "array_" + verdict, 0, {"output": output, "raw": json.dumps([
            {"type": "system", "subtype": "init"}, {"type": "assistant"}, envelope(output)])}
    for line in (None, 0, 1, -1):
        output = dict(VALID, verdict="fix-first", findings=[dict(FINDING, line=line)])
        yield "valid_line_" + str(line), 0, {"output": output}
    for severity in ("critical", "high", "medium", "low"):
        yield "severity_" + severity, 0, {"output": dict(VALID, verdict="fix-first", findings=[dict(FINDING, severity=severity)])}
    yield 'ship_material_findings', 34, {'output': dict(VALID, findings=[FINDING])}
    yield 'non_ship_without_findings', 34, {'output': dict(VALID, verdict='fix-first')}
    for field in VALID:
        output = copy.deepcopy(VALID)
        del output[field]
        yield "missing_" + field, 32 if field == "verdict" else 33, {"output": output}
    for patch in ({"summary": None}, {"summary": 7}, {"findings": None},
                  {"findings": {}}, {"extra": True}, {"verdict": "approve"},
                  {"verdict": True}, {"verdict": ["ship"]}):
        yield "invalid_output_" + repr(patch), 32 if "verdict" in patch else 33, {"output": dict(VALID, **patch)}
    for field in FINDING:
        finding = copy.deepcopy(FINDING)
        del finding[field]
        yield "missing_finding_" + field, 33, {"output": dict(VALID, findings=[finding])}
    for patch in ({"severity": "urgent"}, {"severity": None}, {"file": 1},
                  {"line": "1"}, {"line": 1.5}, {"line": True},
                  {"issue": []}, {"recommendation": None}, {"extra": "value"}):
        yield "invalid_finding_" + repr(patch), 33, {"output": dict(VALID, findings=[dict(FINDING, **patch)])}
    for finding in (None, "issue", 1, []):
        yield "non_object_finding_" + repr(finding), 33, {"output": dict(VALID, findings=[finding])}
    for patch in ({"is_error": True}, {"is_error": "false"}, {"subtype": "error_during_execution"},
                  {"subtype": "error_max_structured_output_retries"}, {"type": "assistant"},
                  {"structured_output": None}, {"structured_output": []}):
        yield "invalid_envelope_" + repr(patch), 31, {"raw": json.dumps(dict(envelope(), **patch))}
    for field in ("type", "subtype", "is_error", "structured_output"):
        result = envelope()
        del result[field]
        yield "missing_envelope_" + field, 31, {"raw": json.dumps(result)}
    for value in ("not json", "", "null", "[]", "7", '"text"',
                  json.dumps(envelope()) + '\n' + json.dumps(envelope()),
                  json.dumps([envelope(), dict(envelope(), is_error=True, subtype="error_during_execution")]),
                  json.dumps([envelope(), {"type": "assistant"}]),
                  json.dumps([envelope(), "malformed event"])):
        yield "invalid_raw_" + repr(value), 31, {"raw": value}
    for name in ("untracked_mutation", "tracked_mutation", "assume_unchanged_mutation", "staged_mutation",
                 "head_mutation", "branch_mutation", "symlink_mutation", "mode_mutation",
                 "untracked_deleted", "untracked_created", "weird_name_mutation", "nested_mutation",
                 "submodule_mutation", "failure_after_mutation"):
        yield name, 30, {}
    for name in ("symlink_stable", "weird_name_stable", "unborn", "detached", "ignored_mutation",
                 "subdirectory", "relative_entry", "nested_stable", "submodule_stable", "custom_ccb_binary"):
        yield name, 0, {}
    for name, code in (("cli_failure", 7), ("fingerprint_before_failure", 28),
                       ("fingerprint_after_failure", 29), ("fingerprint_and_cli_failure", 29),
                       ("before_special_file", 28), ("after_special_file", 29),
                       ("empty_packet", 22), ("outside_repo", 21), ("bare_repo", 21)):
        yield name, code, {}
    yield "missing_custom_ccb_binary", 20, {}
    for command in ("ccb", "git", "python3"):
        yield "missing_command_" + command, 20, {}
    yield "invalid_mode", 19, {"mode": "unknown"}


def main():
    for script_arg in sys.argv[1:]:
        script = Path(script_arg).resolve()
        with tempfile.TemporaryDirectory(prefix="magent-integration-") as td:
            root = Path(td)
            (root / "bin").mkdir()
            for name, content in (("ccb", FAKE_CCB), ("git", FAKE_GIT),
                                  ("claude", "#!/bin/sh\necho 'ERROR: wrong CLI invoked' >&2\nexit 99\n")):
                path = root / "bin" / name
                path.write_text(content)
                path.chmod(0o755)
            total = 0
            for number, (name, expected, kwargs) in enumerate(cases()):
                run(script, root, number, name, expected=expected, **kwargs)
                total += 1
            print(f"PASS: {script}: {total} offline integration cases", flush=True)


if __name__ == "__main__":
    main()
