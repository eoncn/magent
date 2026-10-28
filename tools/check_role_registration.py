#!/usr/bin/env python3
"""Read-only validation of magent Codex role files and explicit registration."""
import argparse
from pathlib import Path
import re
import sys

EXPECTED = {
    "sol_advisor_routine": ("sol-advisor-routine.toml", "gpt-6-luna", "xhigh"),
    "sol_advisor_standard": ("sol-advisor-standard.toml", "gpt-6.1-terra", "high"),
    "sol_advisor_high": ("sol-advisor-high.toml", "gpt-6.1-sol", "high"),
    "sol_verifier_routine": ("sol-verifier-routine.toml", "gpt-6-luna", "xhigh"),
    "sol_verifier_high": ("sol-verifier-high.toml", "gpt-6.1-sol", "high"),
}


def assignment(text, key):
    match = re.search(r'^\s*' + re.escape(key) + r'\s*=\s*["\x27]([^"\x27]+)["\x27]\s*(?:#.*)?$', text, re.M)
    return match.group(1) if match else None


def role_blocks(text):
    """Extract only exact TOML [agents.<role>] registration tables."""
    result = {}
    key = None
    for line in text.splitlines():
        heading = re.match(r'^\s*\[([^\]]+)\]\s*(?:#.*)?$', line)
        if heading:
            section = heading.group(1).strip()
            key = section[len("agents."):] if section.startswith("agents.") else None
            if key and key not in result:
                result[key] = []
        elif key in result:
            result[key].append(line)
    return {k: "\n".join(v) for k, v in result.items()}


def validate(home):
    home = Path(home).expanduser().resolve()
    cfg = home / "config.toml"
    errors = []
    if not cfg.is_file():
        return [f"Missing config: {cfg}"]
    blocks = role_blocks(cfg.read_text(encoding="utf-8-sig"))
    for role, (filename, model, effort) in EXPECTED.items():
        path = home / "agents" / filename
        if not path.is_file():
            errors.append(f"{role}: missing role file: {path}")
            continue
        body = path.read_text(encoding="utf-8-sig")
        for field, value in (("name", role), ("model", model), ("model_reasoning_effort", effort)):
            found = assignment(body, field)
            if found != value:
                errors.append(f"{role}: {field} expected {value}, got {found}")
        if role not in blocks:
            errors.append(f"{role}: missing [agents.{role}] registration in {cfg}")
        else:
            found = assignment(blocks[role], "config_file")
            if not found:
                errors.append(f"{role}: registration missing config_file")
            else:
                config_path = Path(found)
                resolved = (config_path if config_path.is_absolute() else cfg.parent / config_path).resolve()
                if resolved != path.resolve():
                    errors.append(f"{role}: config_file points to {resolved}, expected {path}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home() / ".codex")
    args = parser.parse_args(argv)
    errors = validate(args.home)
    for error in errors:
        print("ERROR: " + error, file=sys.stderr)
    if errors:
        print(f"FAIL: {len(errors)} role configuration issue(s)", file=sys.stderr)
        return 2
    print(f"PASS: {len(EXPECTED)} magent roles installed and registered in {args.home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
