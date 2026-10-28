#!/usr/bin/env python3
"""Link both local Codex installations to this checkout, preserving old files."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[1]


def linked_to(path, target):
    junction = getattr(path, "is_junction", lambda: False)()
    return (path.is_symlink() or junction) and path.resolve() == target.resolve()


def link_directory(target, link):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_entry(path):
    if getattr(path, "is_junction", lambda: False)():
        path.rmdir()
    elif path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def install(homes, include_roles=False, dry_run=False):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    entries = []
    homes = list(dict.fromkeys(p.expanduser().absolute() for p in homes))
    for home in homes:
        target = home / "skills/magent"
        if SOURCE.is_relative_to(target.resolve()):
            if linked_to(target, SOURCE):
                continue_skill = True
            else:
                raise ValueError("Source checkout must be outside the installation: " + str(target))
        else:
            continue_skill = False
        if not continue_skill:
            entries.append((target, SOURCE, "directory", home))
        if include_roles:
            for role in sorted((SOURCE / "roles").glob("*.toml")):
                target = home / "agents" / role.name
                if target.is_file() and target.read_bytes() == role.read_bytes():
                    continue
                entries.append((target, role, "file", home))
    report = [{"target": str(t), "source": str(s), "kind": kind} for t, s, kind, _ in entries]
    if dry_run:
        return {"dry_run": True, "changes": report}
    completed = []
    pending = None
    try:
        for target, source, kind, home in entries:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
            pending = temporary
            if kind == "directory":
                link_directory(source, temporary)
            else:
                shutil.copyfile(source, temporary)
            backup = None
            if os.path.lexists(target):
                backup = home / "magent-install-backups" / stamp / target.relative_to(home)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            completed.append((target, backup))
            os.replace(temporary, target)
            pending = None
        return {"dry_run": False, "changes": report,
                "backups": [str(b) for _, b in completed if b is not None]}
    except BaseException:
        if pending is not None and os.path.lexists(pending):
            remove_entry(pending)
        for target, backup in reversed(completed):
            if os.path.lexists(target):
                remove_entry(target)
            if backup is not None:
                os.replace(backup, target)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", action="append", type=Path,
                        help="Codex home; repeatable, defaults to ~/.codex and ~/.codex-us")
    parser.add_argument("--roles", action="store_true", help="also install the five role templates; leaves config.toml untouched")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (SOURCE / "SKILL.md").is_file():
        parser.error("source skill is missing")
    homes = args.home or [Path.home() / ".codex", Path.home() / ".codex-us"]
    try:
        print(json.dumps(install(homes, args.roles, args.dry_run), ensure_ascii=True, indent=2))
    except (OSError, ValueError) as error:
        print("Installation failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
