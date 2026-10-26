#!/usr/bin/env python3
"""Persistent CCB review supervisor, adapted from the macos-server stage runner.

The Bash entry point owns synchronous review validation. This supervisor adds
private run state, persistent sessions, bounded lifecycle and Codex callbacks.
Only the review stage is implemented; it never dispatches implementation work.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

if os.name == "nt":
    import msvcrt
else:
    import fcntl

sys.dont_write_bytecode = True
from review_support import fingerprint

SCRIPT = Path(__file__).resolve().with_name("claude-review.sh")
ACTIVE = {"queued", "running", "stopping"}
NO_CONSOLE = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class ReviewError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat()


def runs_root():
    # A shared source checkout is not a Codex home. Keep runtime state outside it.
    owner = SCRIPT.parents[3] if SCRIPT.parents[2].name == "skills" else Path.home() / ".codex"
    default = Path(os.environ.get("CODEX_HOME", str(owner))) / "magent-runs"
    return Path(os.environ.get("MAGENT_REVIEW_RUNS_DIR", str(default))).expanduser().resolve()


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=True, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


def safe_open(path, flags, mode=0o600):
    if path.is_symlink():
        raise ReviewError("State paths must not be symbolic links")
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)


def read_json(path):
    deadline = time.monotonic() + 2
    while True:
        try:
            with os.fdopen(safe_open(path, os.O_RDONLY), "r", encoding="utf-8") as source:
                return json.load(source)
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


@contextmanager
def lock(run, name="state.lock"):
    fd = safe_open(run / name, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def run_path(value):
    candidate = Path(value).expanduser().absolute()
    if candidate.is_symlink():
        raise ReviewError("Run directory must not be a symlink")
    run = candidate.resolve(strict=True)
    if run.parent != runs_root() or str(uuid.UUID(run.name)) != run.name:
        raise ReviewError("Run directory is outside the configured review runs root")
    return run


def load_state(run):
    state = read_json(run / "state.json")
    if (state.get("version") != 1 or state.get("run_id") != run.name
            or state.get("run_dir") != str(run) or state.get("mode") not in ("final", "commitment")
            or not isinstance(state.get("attempts"), list) or not state["attempts"]
            or state.get("current_attempt") != len(state["attempts"])):
        raise ReviewError("Invalid or mismatched run state")
    for number, record in enumerate(state["attempts"], 1):
        if record.get("attempt") != number or str(uuid.UUID(record["nonce"])) != record["nonce"]:
            raise ReviewError("Invalid attempt identity")
    return state


def update(run, number, change):
    with lock(run):
        state = load_state(run)
        if state["current_attempt"] != number:
            raise ReviewError("Stale attempt cannot update the current run")
        change(state, state["attempts"][number - 1])
        state["updated_at"] = now()
        write_json(run / "state.json", state)
        return state


def paths(run, number):
    directory = run / ("attempt-" + str(number))
    return {"directory": directory, "packet": directory / "packet.txt",
            "progress": directory / "progress.log", "output": directory / "stdout.json",
            "result": directory / "result.json", "supervisor": directory / "supervisor.log"}


def executable(value):
    resolved = shutil.which(value)
    if not resolved and os.name == "nt" and Path(value).is_absolute() and Path(value).is_file():
        resolved = value  # Git Bash can execute extensionless shell launchers.
    if not resolved:
        raise ReviewError("Required command not found: " + value, 20)
    # Keep the launcher path: resolving symlinks can change executable semantics.
    return os.path.abspath(resolved)


def bash_path(value):
    return str(value).replace("\\", "/") if os.name == "nt" else str(value)


def shell_launcher(value):
    path = Path(value)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".bat"):
        path = path.with_suffix("")
        if not path.is_file():
            raise ReviewError("Missing Git Bash launcher beside " + value, 20)
    return bash_path(path)


def tool_args(value):
    if os.name == "nt" and Path(value).suffix.lower() != ".exe":
        return [executable("bash"), "--noprofile", "--norc", shell_launcher(value)]
    return [value]


def shell_env():
    env = dict(os.environ)
    for key in ("BASH_ENV", "ENV"):
        env.pop(key, None)
    return env


def send_queue(launcher, thread, message, cwd):
    env = shell_env()
    if os.name == "nt" and Path(launcher).suffix.lower() != ".exe":
        # Windows/MSYS command-line parsing can consume JSON backslashes before
        # Bash receives argv. Pass opaque payloads through the environment and
        # expand them only inside Bash; never interpolate them into shell code.
        env.update(MAGENT_QUEUE_LAUNCHER=shell_launcher(launcher),
                   MAGENT_QUEUE_THREAD=thread, MAGENT_QUEUE_MESSAGE=message,
                   MSYS2_ARG_CONV_EXCL="*")
        command = [executable("bash"), "--noprofile", "--norc", "-c",
                   'exec "$MAGENT_QUEUE_LAUNCHER" queue --thread "$MAGENT_QUEUE_THREAD" --message "$MAGENT_QUEUE_MESSAGE"']
    else:
        command = [launcher, "queue", "--thread", thread, "--message", message]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=10, **NO_CONSOLE)


def preflight(thread):
    config = {"ccb": executable(os.environ.get("CLAUDE_CCB_BIN", "ccb")),
              "bash": executable("bash"), "model": os.environ.get("CLAUDE_REVIEW_MODEL", "opus"),
              "effort": os.environ.get("CLAUDE_REVIEW_EFFORT", "high")}
    for command in ("git", "python3"):
        executable(command)
    if (not config["model"].strip() or config["model"].startswith("-")
            or config["effort"] not in ("low", "medium", "high", "xhigh", "max")):
        raise ReviewError("Invalid CCB model or effort configuration", 22)
    if thread:
        config["codex"] = executable(os.environ.get("CLAUDE_CODEX_BIN", "codex"))
        result = subprocess.run([*tool_args(config["codex"]), "queue", "--help"],
                                env=shell_env(), capture_output=True, timeout=10, **NO_CONSOLE)
        if result.returncode or b"--thread" not in result.stdout or b"--message" not in result.stdout:
            raise ReviewError("Configured Codex does not support queue --thread --message", 20)
    return config


def repo_root(value):
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=value,
                            capture_output=True, text=True, **NO_CONSOLE)
    if result.returncode:
        raise ReviewError("Review requires a Git working tree", 21)
    return str(Path(result.stdout.rstrip("\n")).resolve())


def identity(pid):
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "stat=", "-o", "lstart=", "-o", "command="],
                            capture_output=True, text=True)
    # Save the complete start-time/command tuple to reject reused PIDs.
    value = result.stdout.strip().split(maxsplit=1)
    # Process state changes between R/S; only start time and command identify it.
    return value[1] if result.returncode == 0 and len(value) == 2 and not value[0].startswith("Z") else None


def owned(record, prefix):
    pid = record.get(prefix + "_pid")
    expected = record.get(prefix + "_identity")
    actual = identity(pid) if isinstance(pid, int) and pid > 1 else None
    if not actual:
        return None
    if not expected or actual != expected or record["nonce"] not in actual:
        raise ReviewError("Refusing to signal a process with mismatched identity")
    return pid


def group_alive(pgid, nonce=None):
    if not isinstance(pgid, int) or pgid <= 1:
        return False
    result = subprocess.run(["ps", "-axo", "pgid=,stat="], capture_output=True, text=True)
    if result.returncode:
        raise ReviewError("Cannot verify review process group")
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == str(pgid) and not fields[1].startswith("Z"):
            return True
    return False


def terminate_group(pgid, nonce=None):
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def event_message(state, record, event):
    return "$magent internal callback\n" + json.dumps({
        "run_id": state["run_id"], "run_dir": state["run_dir"], "mode": state["mode"],
        "attempt": record["attempt"], "event": event,
        "delivery_id": f'{state["run_id"]}:{record["attempt"]}:{event}',
    })


def queue_callback(run, number, event):
    """At-least-once delivery. A stable delivery_id lets the receiver deduplicate."""
    with lock(run, "callback.lock"):
        state = load_state(run)
        record = state["attempts"][number - 1]
        if state["current_attempt"] != number or not state["thread_id"]:
            return True
        if record.get("callbacks", {}).get(event, {}).get("status") == "sent":
            return True
        if event == "overdue" and record["status"] != "running":
            return True
        def sending(_, item):
            item.setdefault("callbacks", {})[event] = {"status": "sending", "updated_at": now()}
        update(run, number, sending)
        last_error = ""
        for attempt in range(3):
            if event == "overdue" and load_state(run)["attempts"][number - 1]["status"] != "running":
                update(run, number, lambda _, item: item["callbacks"].update({event: {"status": "superseded"}}))
                return True
            try:
                result = send_queue(record["config"]["codex"], state["thread_id"],
                                    event_message(state, record, event), state["repo_root"])
                if result.returncode == 0:
                    update(run, number, lambda _, item: item["callbacks"].update({
                        event: {"status": "sent", "updated_at": now(), "tries": attempt + 1}}))
                    return True
                last_error = "codex queue exited with status " + str(result.returncode)
            except (OSError, subprocess.TimeoutExpired) as error:
                last_error = type(error).__name__
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
        update(run, number, lambda _, item: item["callbacks"].update({event: {
            "status": "failed", "updated_at": now(), "tries": 3, "error": last_error}}))
        return False


def new_attempt(run, state, packet, config, eta, timeout, resume=None):
    number = len(state["attempts"]) + 1
    files = paths(run, number)
    files["directory"].mkdir(mode=0o700)
    for key in ("packet", "progress", "output", "supervisor"):
        with os.fdopen(os.open(files[key], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
            if key == "packet":
                out.write(packet)
    record = {"attempt": number, "nonce": str(uuid.uuid4()), "status": "queued", "created_at": now(),
              "session_id": resume["session_id"] if resume else str(uuid.uuid4()),
              "resumed_from_attempt": resume["attempt"] if resume else None,
              "eta_seconds": eta, "timeout_seconds": timeout, "config": config,
              "before_fingerprint": fingerprint(state["repo_root"]), "callbacks": {},
              "progress_log": str(files["progress"]), "result_path": str(files["result"])}
    state["attempts"].append(record)
    state["current_attempt"] = number
    state["updated_at"] = now()
    write_json(run / "state.json", state)
    return record


def launch(run, record, detach):
    files = paths(run, record["attempt"])
    args = [sys.executable, "-B", str(Path(__file__).resolve()), "__worker", "--run-dir", str(run),
            "--attempt", str(record["attempt"]), "--nonce", record["nonce"]]
    with files["supervisor"].open("ab") as log:
        if os.name == "nt":
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    def register(_, item):
        item.setdefault("worker_pid", proc.pid)
    update(run, record["attempt"], register)
    if detach:
        print(json.dumps({"run_id": run.name, "run_dir": str(run), "attempt": record["attempt"],
                          "session_id": record["session_id"], "status": "running",
                          "eta_seconds": record["eta_seconds"], "progress_log": str(files["progress"]),
                          "worker_pid": proc.pid}))
        return 0
    try:
        proc.wait()
    except KeyboardInterrupt:
        try:
            if os.name == "nt":
                update(run, record["attempt"], lambda _, item: item.update(status="stopping", stop_requested_at=now()))
            else:
                os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)
        raise ReviewError("Review interrupted; retained run: " + str(run), 130)
    current = load_state(run)["attempts"][record["attempt"] - 1]
    if current["status"] == "completed":
        print(json.dumps(read_json(files["result"])))
        return 0
    raise ReviewError((current.get("error") or "Review did not complete") + "; run: " + str(run), current.get("exit_code", 1) or 1)


def terminate(proc):
    if proc.poll() is None:
        # Keep the leader unreaped until the whole group has terminated; a CCB
        # child may ignore SIGTERM even when its Bash parent exits immediately.
        terminate_group(proc.pid)
        proc.wait(timeout=2)


def worker(run, number, nonce):
    state = load_state(run)
    record = state["attempts"][number - 1]
    if (state["current_attempt"] != number or record["nonce"] != nonce
            or record["status"] not in ("queued", "stopping")):
        raise ReviewError("Worker identity or attempt state mismatch")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    def started(_, item):
        item.update(worker_pid=os.getpid(), worker_identity=identity(os.getpid()), started_at=now())
        if item["status"] == "stopping":
            stopping.set()
        else:
            item["status"] = "running"
    update(run, number, started)
    files = paths(run, number)
    proc = None
    notification = None
    result = None
    status, code, error = "blocked", 1, None
    env = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
                "BASH_ENV", "ENV"):
        env.pop(key, None)
    env.update(CLAUDE_CCB_BIN=shell_launcher(record["config"]["ccb"]), CLAUDE_REVIEW_MODEL=record["config"]["model"],
               CLAUDE_REVIEW_EFFORT=record["config"]["effort"], MAGENT_REVIEW_SESSION_ID=record["session_id"],
               MAGENT_REVIEW_RESUME="1" if record["resumed_from_attempt"] else "0")
    try:
        if fingerprint(state["repo_root"]) != record["before_fingerprint"]:
            raise ReviewError("Repository changed between submission and review", 30)
        if stopping.is_set():
            status, code = "stopped", 130
        else:
            # The group leader carries the unique attempt nonce, so stop can
            # validate both its PID/start time and its command before signalling.
            command = [record["config"]["bash"], "--noprofile", "--norc", "-c",
                       '"$3" --noprofile --norc "$1" --claude-review "$2"\nstatus=$?\nexit "$status"',
                       "magent-review-" + nonce, bash_path(SCRIPT), state["mode"], bash_path(record["config"]["bash"])]
            with files["packet"].open("rb") as packet, files["output"].open("wb") as output, files["progress"].open("ab") as progress:
                if os.name == "nt":
                    proc = windows_spawn(command, nonce, cwd=state["repo_root"], env=env,
                                         stdin=packet, stdout=output, stderr=progress)
                else:
                    proc = subprocess.Popen(command, cwd=state["repo_root"], env=env, stdin=packet,
                                            stdout=output, stderr=progress, start_new_session=True)
                update(run, number, lambda _, item: item.update(cli_pid=proc.pid, cli_identity=identity(proc.pid)))
                start = time.monotonic()
                overdue = False
                while proc.poll() is None:
                    if os.name == "nt" and load_state(run)["attempts"][number - 1]["status"] == "stopping":
                        stopping.set()
                    elapsed = time.monotonic() - start
                    if stopping.is_set():
                        status, code = "stopped", 130
                        terminate(proc)
                        break
                    if elapsed >= record["timeout_seconds"]:
                        code, error = 124, "Review exceeded hard timeout"
                        terminate(proc)
                        break
                    if not overdue and elapsed >= record["eta_seconds"]:
                        overdue = True
                        update(run, number, lambda _, item: item.update(overdue_at=now()))
                        notification = threading.Thread(target=queue_callback, args=(run, number, "overdue"))
                        notification.start()
                    time.sleep(0.05)
                else:
                    code = proc.returncode
                if stopping.is_set():
                    status, code = "stopped", 130
                elif not error and code == 0:
                    if files["output"].stat().st_size > 16 * 1024 * 1024:
                        raise ReviewError("Review result exceeds 16 MiB", 31)
                    result = read_json(files["output"])
                    if not isinstance(result, dict) or result.get("verdict") not in ("ship", "fix-first", "rethink"):
                        raise ReviewError("Missing validated review result", 31)
                    status = "completed"
                elif not error:
                    code = code if code > 0 else 128 - code
                    error = "CCB review command failed with exit status " + str(code)
    except (OSError, ValueError, RuntimeError, ReviewError) as failure:
        code = getattr(failure, "code", 1)
        error = str(failure)
    finally:
        if proc is not None:
            try:
                terminate(proc)
            except (OSError, subprocess.TimeoutExpired) as failure:
                status, code, error, result = "blocked", 1, "Process termination failed: " + str(failure), None
        # Independent post-run audit also runs when the shell was killed and
        # therefore could not execute its own post-review fingerprint.
        try:
            after = fingerprint(state["repo_root"])
            update(run, number, lambda _, item: item.update(after_fingerprint=after))
            if after != record["before_fingerprint"]:
                status, code, error, result = "blocked", 30, "Repository changed during read-only review", None
        except (OSError, RuntimeError) as failure:
            status, code, error, result = "blocked", 29, "Post-review fingerprint failed: " + str(failure), None
    if status == "completed" and result is not None:
        write_json(files["result"], result)
    update(run, number, lambda _, item: item.update(status=status, exit_code=code,
                                                   completed_at=now(), event=status, error=error))
    queue_callback(run, number, status)
    if notification:
        notification.join()
    return 0 if status == "completed" else (code or 1)


def create(args):
    thread = args.thread_id or (os.environ.get("CODEX_THREAD_ID") if args.detach else None)
    if args.detach and (not thread or args.eta_seconds is None):
        raise ReviewError("Detached review requires --thread-id (or CODEX_THREAD_ID) and --eta-seconds", 22)
    config = preflight(thread)
    repo = repo_root(args.repo)
    root = runs_root()
    if root == Path(repo) or Path(repo) in root.parents:
        raise ReviewError("Review artifacts must be stored outside the repository", 22)
    packet = sys.stdin.read()
    if not packet.strip():
        raise ReviewError("Empty review packet", 22)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = root / str(uuid.uuid4())
    run.mkdir(mode=0o700)
    state = {"version": 1, "run_id": run.name, "run_dir": str(run), "mode": args.action,
             "repo_root": repo, "thread_id": thread, "created_at": now(), "attempts": []}
    eta = args.eta_seconds or 900
    with lock(run):
        record = new_attempt(run, state, packet, config, eta, args.timeout_seconds or 2 * eta)
    return launch(run, record, args.detach)


def control(args):
    run = run_path(args.run_dir)
    state = load_state(run)
    record = state["attempts"][-1]
    number = record["attempt"]
    if args.thread_id and args.thread_id != state["thread_id"]:
        raise ReviewError("A run's callback thread cannot be changed", 22)
    if args.action == "inspect":
        print(json.dumps(state))
        return 0
    if args.action == "retry-callback":
        if record["status"] in ACTIVE:
            raise ReviewError("Cannot retry a terminal callback while a review is active")
        if not state["thread_id"]:
            raise ReviewError("This run has no callback thread")
        ok = queue_callback(run, number, record["event"])
        print(json.dumps(load_state(run)))
        return 0 if ok else 1
    if args.action == "stop":
        if record["status"] not in ACTIVE:
            raise ReviewError("Only an active review can be stopped")
        if not record.get("worker_identity") and (
                not record.get("worker_pid") or identity(record["worker_pid"])):
            # Registration can race a very early stop. The worker observes this
            # state before starting CCB; do not signal an unverified PID.
            update(run, number, lambda _, item: item.update(status="stopping", stop_requested_at=now()))
            print(json.dumps(load_state(run)))
            return 0
        worker_pid = owned(record, "worker")
        cli_pid = owned(record, "cli")
        update(run, number, lambda _, item: item.update(status="stopping", stop_requested_at=now()))
        if worker_pid:
            if os.name != "nt":
                os.kill(worker_pid, signal.SIGTERM)
        else:
            if cli_pid:
                terminate_group(cli_pid, record["nonce"])
            elif group_alive(record.get("cli_pid"), record["nonce"]):
                raise ReviewError("An orphaned process group still exists; refusing unsafe recovery")
            status, code, error, after = "stopped", 130, None, None
            try:
                after = fingerprint(state["repo_root"])
                if after != record["before_fingerprint"]:
                    status, code, error = "blocked", 30, "Repository changed during orphaned review"
            except (OSError, RuntimeError) as failure:
                status, code, error = "blocked", 29, "Post-review fingerprint failed: " + str(failure)
            update(run, number, lambda _, item: item.update(status=status, event=status,
                   after_fingerprint=after, completed_at=now(), exit_code=code, error=error))
            queue_callback(run, number, status)
        print(json.dumps(load_state(run)))
        return 0
    if args.action == "resume":
        with lock(run):
            state = load_state(run)
            record = state["attempts"][-1]
            if record["status"] not in ("stopped", "blocked"):
                raise ReviewError("Only stopped or blocked reviews can be resumed")
            if owned(record, "worker") or owned(record, "cli") or group_alive(record.get("cli_pid"), record["nonce"]):
                raise ReviewError("Previous attempt still has a live process")
            if not record.get("session_id"):
                raise ReviewError("Legacy attempt has no persistent CCB session ID")
            config = preflight(state["thread_id"])
            packet = paths(run, record["attempt"])["packet"].read_text(encoding="utf-8")
            eta = args.eta_seconds or record["eta_seconds"]
            timeout = args.timeout_seconds or (2 * eta if args.eta_seconds else record["timeout_seconds"])
            record = new_attempt(run, state, packet, config, eta, timeout, resume=record)
        return launch(run, record, args.detach)
    if args.action == "cleanup":
        if record["status"] != "completed" or read_json(paths(run, number)["result"])["verdict"] != "ship":
            raise ReviewError("Cleanup requires a completed ship review; retain failed or stopped artifacts")
        if any(item.get("status") in ("sending", "failed") for item in record["callbacks"].values()):
            raise ReviewError("Resolve callback delivery before cleanup")
        if owned(record, "worker") or owned(record, "cli") or group_alive(record.get("cli_pid"), record["nonce"]):
            raise ReviewError("Review process is still running")
        shutil.rmtree(run)
        print(json.dumps({"run_id": state["run_id"], "status": "cleaned"}))
        return 0
    raise ReviewError("Unsupported lifecycle action")


def positive(value):
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="final", choices=(
        "final", "commitment", "inspect", "resume", "stop", "retry-callback", "cleanup", "__worker"))
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--thread-id")
    parser.add_argument("--eta-seconds", type=positive)
    parser.add_argument("--timeout-seconds", type=positive)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--run-dir")
    parser.add_argument("--attempt", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.action in ("final", "commitment"):
            return create(args)
        if not args.run_dir:
            raise ReviewError("--run-dir is required", 22)
        if args.action == "__worker":
            return worker(run_path(args.run_dir), args.attempt, args.nonce)
        return control(args)
    except (ReviewError, OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return getattr(error, "code", 1)


if __name__ == "__main__":
    if os.name == "nt":
        from review_windows import identity, group_alive, terminate_group, terminate, spawn as windows_spawn
    sys.exit(main())
