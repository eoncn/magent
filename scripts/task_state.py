#!/usr/bin/env python3
"""magent long-task checkpoints. Single-writer, atomic, outside the business repository."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
VERDICTS = ("pending", "pass", "fail", "blocked")
ROUTES = ("solo", "routine", "standard", "high")
PHASES = ("planning", "implementation", "verification", "remediation", "blocked", "complete")


def root_dir():
    override = os.environ.get("MAGENT_TASKS_DIR")
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return home / "magent-tasks"


def ensure_root(root, create=False):
    root = Path(root).expanduser()
    if root.is_symlink():
        raise ValueError("Task state directory cannot be a symlink")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            root.chmod(0o700)
    elif not root.is_dir():
        raise FileNotFoundError(f"Task state directory not found: {root}")
    return root


def task_path(root, task_id):
    if not ID_PATTERN.fullmatch(task_id):
        raise ValueError("Invalid task id: use a 32-character hexadecimal identifier")
    path = Path(root) / (task_id + ".json")
    if path.is_symlink():
        raise ValueError("Task state file cannot be a symlink")
    return path


def read_task(root, task_id):
    path = task_path(root, task_id)
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or state.get("task_id") != task_id or not isinstance(state.get("revision"), int)):
        raise ValueError("Invalid task state schema")
    return state


def atomic_write(path, state):
    path = Path(path)
    payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix="." + path.stem + "-", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(tmp)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def init_task(root, repo, objective, scope, baseline="", phase="planning"):
    root = ensure_root(root, create=True)
    if not objective.strip() or not scope.strip():
        raise ValueError("Objective and scope must be nonempty")
    if phase not in PHASES or phase in ("complete", "blocked"):
        raise ValueError("Invalid initial phase")
    task_id = uuid.uuid4().hex
    path = task_path(root, task_id)
    if path.exists():
        raise FileExistsError("Task id collision")
    state = {
        "schema_version": 1,
        "task_id": task_id,
        "revision": 0,
        "status": "active",
        "repo": str(Path(repo).expanduser().resolve()),
        "objective": objective.strip(),
        "scope": scope.strip(),
        "baseline_sha": baseline.strip(),
        "phase": phase,
        "completed": [],
        "next_steps": [],
        "blocker": "",
        "last_verdict": "pending",
        "route": "",
        "retry_count": 0,
        "milestone": 1,
    }
    atomic_write(path, state)
    return state


def checkpoint(root, task_id, expected_revision, phase=None, completed=None,
               next_steps=None, blocker=None, verdict=None, route=None, retry_count=None,
               new_milestone=False):
    root = ensure_root(root)
    state = read_task(root, task_id)
    if state["status"] != "active":
        raise ValueError("Closed tasks cannot be checkpointed")
    if expected_revision != state["revision"]:
        raise ValueError(f"Revision mismatch: expected {expected_revision}, actual {state['revision']}")
    if new_milestone:
        if state["last_verdict"] != "pass" or state["blocker"]:
            raise ValueError("A new milestone requires previous independent PASS")
        state["milestone"] += 1
        state["retry_count"] = 0
        state["last_verdict"] = "pending"
    if phase is not None:
        if phase not in PHASES or phase == "complete":
            raise ValueError("Invalid checkpoint phase")
        state["phase"] = phase
    if completed:
        for item in completed:
            item = item.strip()
            if item and item not in state["completed"]:
                state["completed"].append(item)
    if next_steps is not None:
        state["next_steps"] = [x.strip() for x in next_steps if x.strip()]
    if blocker is not None:
        state["blocker"] = blocker.strip()
    if verdict is not None:
        if verdict not in VERDICTS:
            raise ValueError("Invalid verdict")
        state["last_verdict"] = verdict
    if route is not None:
        if route not in ROUTES:
            raise ValueError("Invalid route")
        state["route"] = route
    if retry_count is not None:
        if not state["retry_count"] <= retry_count <= 6:
            raise ValueError("Retry counter must be monotonic and at most six per milestone")
        state["retry_count"] = retry_count
    if state["phase"] == "blocked" and not state["blocker"]:
        raise ValueError("Blocked phase requires a blocker description")
    state["revision"] += 1
    atomic_write(task_path(root, task_id), state)
    return state


def close_task(root, task_id, expected_revision):
    root = ensure_root(root)
    state = read_task(root, task_id)
    if state["status"] != "active":
        raise ValueError("Task already closed")
    if expected_revision != state["revision"]:
        raise ValueError("Revision mismatch")
    if state["last_verdict"] != "pass" or state["blocker"] or state["next_steps"]:
        raise ValueError("Only fully verified tasks without blockers or next steps can close")
    state["status"] = "closed"
    state["phase"] = "complete"
    state["revision"] += 1
    atomic_write(task_path(root, task_id), state)
    return state


def list_tasks(root, repo=None):
    root = ensure_root(root)
    repo_filter = str(Path(repo).expanduser().resolve()) if repo else None
    results = []
    for file in sorted(root.glob("*.json")):
        if file.is_symlink() or not ID_PATTERN.fullmatch(file.stem):
            continue
        state = read_task(root, file.stem)
        if repo_filter is None or state["repo"] == repo_filter:
            results.append({
                "task_id": state["task_id"], "revision": state["revision"],
                "status": state["status"], "phase": state["phase"],
                "route": state["route"], "repo": state["repo"],
                "next_steps": state["next_steps"],
            })
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="command", required=True)
    create = actions.add_parser("init", help="create a long task checkpoint")
    create.add_argument("--repo", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--scope", required=True)
    create.add_argument("--baseline", default="")
    create.add_argument("--phase", default="planning", choices=PHASES)
    show = actions.add_parser("show", help="view a task snapshot")
    show.add_argument("task_id")
    ls = actions.add_parser("list", help="list task snapshots")
    ls.add_argument("--repo")
    cp = actions.add_parser("checkpoint", help="write a milestone (one Lead writer)")
    cp.add_argument("task_id")
    cp.add_argument("--expected-revision", type=int, required=True)
    cp.add_argument("--phase", choices=PHASES)
    cp.add_argument("--completed", action="append")
    cp.add_argument("--next", dest="next_steps", action="append")
    cp.add_argument("--blocker")
    cp.add_argument("--verdict", choices=VERDICTS)
    cp.add_argument("--route", choices=ROUTES)
    cp.add_argument("--retry-count", type=int)
    cp.add_argument("--new-milestone", action="store_true", help="after verified PASS, reset retry budget for a user-authorized next milestone")
    end = actions.add_parser("close", help="close a verified, finished task")
    end.add_argument("task_id")
    end.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args(argv)
    root = root_dir()
    try:
        if args.command == "init":
            result = init_task(root, args.repo, args.objective, args.scope, args.baseline, args.phase)
        elif args.command == "show":
            result = read_task(ensure_root(root), args.task_id)
        elif args.command == "list":
            result = list_tasks(root, args.repo)
        elif args.command == "checkpoint":
            result = checkpoint(root, args.task_id, args.expected_revision, args.phase,
                                args.completed, args.next_steps, args.blocker, args.verdict,
                                args.route, args.retry_count, args.new_milestone)
        elif args.command == "close":
            result = close_task(root, args.task_id, args.expected_revision)
        else:
            parser.error("unsupported command")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
