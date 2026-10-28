#!/usr/bin/env python3
"""Prepare two isolated Codex homes to compare Luna vs Terra in the SAME standard role.

No model calls, no authentication copying, no edits to the source checkout.
Python 3.9+; the destination must not exist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROLES = ("sol-advisor-routine", "sol-advisor-standard", "sol-advisor-high",
         "sol-verifier-routine", "sol-verifier-high")
EXPECTED = {
    "control": ("gpt-6-luna", "xhigh"),
    "terra": ("gpt-6.1-terra", "high"),
}
IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".DS_Store",
                                "*.pyc", "magent-runs", "magent-tasks", ".env", "auth.json")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def role_value(data, key):
    match = re.search(r"(?m)^" + re.escape(key) + r'\s*=\s*"([^"]+)"\s*(?:#.*)?$', data)
    return match.group(1) if match else None


def replace_role_value(data, key, value):
    pattern = r'(?m)^(' + re.escape(key) + r'\s*=\s*")[^"]+(".*)$'
    updated, count = re.subn(pattern, lambda m: m.group(1) + value + m.group(2), data)
    if count != 1:
        raise ValueError("Expected exactly one " + key + " in standard role")
    return updated


def role_dir(home):
    return home / "agents"


def prepare(source, destination):
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not (source / "SKILL.md").is_file() or not (source / "roles").is_dir():
        raise ValueError("Source is not a magent repository")
    if destination.exists():
        raise FileExistsError("Destination already exists; refusing overwrite: " + str(destination))
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Destination must be outside the magent source checkout")
    for role in ROLES:
        if not (source / "roles" / (role + ".toml")).is_file():
            raise ValueError("Missing source role " + role)
    for arm in ("control", "terra"):
        home = destination / arm
        skill = home / "skills" / "magent"
        skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, skill, ignore=IGNORE)
        agents = role_dir(home)
        agents.mkdir()
        for name in ROLES:
            shutil.copy2(source / "roles" / (name + ".toml"),
                         agents / (name + ".toml"))
        standard = agents / "sol-advisor-standard.toml"
        data = standard.read_text(encoding="utf-8")
        data = replace_role_value(data, "model", EXPECTED[arm][0])
        data = replace_role_value(data, "model_reasoning_effort", EXPECTED[arm][1])
        standard.write_text(data, encoding="utf-8")
        config = "".join(
            '[agents.' + name.replace("-", "_") + ']\n'
            'config_file = "agents/' + name + '.toml"\n\n'
            for name in ROLES)
        (home / "config.toml").write_text(config, encoding="utf-8")
    report = verify(destination)
    (destination / "trial-lock.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def verify(destination):
    destination = Path(destination).expanduser().resolve()
    control, terra = (destination / "control", destination / "terra")
    if not control.is_dir() or not terra.is_dir():
        raise ValueError("Missing trial arm")
    def skill_files(home):
        root = home / "skills" / "magent"
        return {str(p.relative_to(root)): file_hash(p) for p in root.rglob("*") if p.is_file()}
    control_skill = skill_files(control)
    terra_skill = skill_files(terra)
    if not control_skill or control_skill != terra_skill:
        raise ValueError("Skill source differs between arms")
    for arm, home in (("control", control), ("terra", terra)):
        standard = home / "agents" / "sol-advisor-standard.toml"
        data = standard.read_text(encoding="utf-8")
        if (role_value(data, "model"), role_value(data, "model_reasoning_effort")) != EXPECTED[arm]:
            raise ValueError(arm + " standard role model does not match the trial")
        for name in ROLES:
            role_file = home / "agents" / (name + ".toml")
            if name != "sol-advisor-standard":
                if file_hash(role_file) != file_hash((terra if arm == "control" else control) /
                                                     "agents" / (name + ".toml")):
                    raise ValueError("Other role differs: " + name)
        if (home / "config.toml").is_file():
            cfg = (home / "config.toml").read_text(encoding="utf-8")
            for name in ROLES:
                if ("[agents." + name.replace("-", "_") + "]") not in cfg:
                    raise ValueError("Missing registration: " + name)
    return {
        "skill_files": len(control_skill),
        "skill_fingerprint": hashlib.sha256(json.dumps(
            control_skill, sort_keys=True).encode()).hexdigest(),
        "role_models": {k: {"model": v[0], "effort": v[1]} for k, v in EXPECTED.items()},
        "isolation": "same copied skill and other roles; only installed standard-role model/effort differ",
        "warning": "This verifies static files, not runtime dispatch or local model entitlements.",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    prep.add_argument("--out", type=Path, required=True)
    test = sub.add_parser("verify")
    test.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        output = prepare(args.source, args.out) if args.command == "prepare" else verify(args.out)
        print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
