#!/usr/bin/env python3
"""Safely prepare Codex agent registrations without rewriting unrelated settings.

Use a real TOML parser (stdlib tomllib on Python 3.11+, or optional tomli on 3.9/3.10).
Caller installs the returned bytes transactionally with Skill and role files.
"""
from pathlib import Path
import os

try:
    import tomllib as _toml
except ImportError:
    try:
        import tomli as _toml
    except ImportError:
        _toml = None


def parse_toml(text, context):
    if _toml is None:
        raise ValueError(
            "Automatic role registration requires Python 3.11+ or 'python3 -m pip install tomli' "
            "(Python 3.9/3.10). Use --no-register to keep legacy manual setup.")
    try:
        return _toml.loads(text)
    except Exception as error:
        raise ValueError(f"Invalid TOML in {context}; not changing it: {error}") from error


def registration_update(home, roles):
    """Return (new_bytes_or_None, added_roles).

    - Existing correct registration stays byte-for-byte unchanged.
    - Conflicts fail closed rather than replacing user-authored custom roles.
    - All new tables are appended; other TOML settings, comments and order stay intact.
    """
    home = Path(home)
    path = home / "config.toml"
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlinked {path}; use --no-register or edit its target explicitly")
    if path.exists() and not path.is_file():
        raise ValueError(f"Config must be a regular file: {path}")
    old = path.read_bytes() if path.exists() else b""
    if b"\x00" in old:
        raise ValueError(f"Config contains NUL bytes: {path}")
    try:
        text = old.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError(f"Config must be UTF-8: {path}") from error
    current = parse_toml(text, str(path))
    agents = current.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError(f"[agents] must be a TOML table in {path}")
    if agents.get("enabled") is False:
        raise ValueError(
            f"Multi-agent was explicitly disabled in {path}; enable it manually "
            "before installing magent roles (no automatic override).")
    missing = []
    for name, filename in roles.items():
        desired = (home / "agents" / filename).resolve()
        if name in agents:
            section = agents[name]
            if not isinstance(section, dict) or not isinstance(section.get("config_file"), str):
                raise ValueError(f"[agents.{name}] has no valid config_file in {path}; refusing overwrite")
            specified = Path(section["config_file"]).expanduser()
            resolved = (specified if specified.is_absolute() else home / specified).resolve()
            if resolved != desired:
                raise ValueError(
                    f"[agents.{name}] points to {resolved}, expected {desired}; "
                    "edit that conflicting registration manually or use --no-register.")
        else:
            missing.append((name, filename))
    if not missing:
        return None, []
    newline = "\r\n" if b"\r\n" in old else "\n"
    # Keep BOM, original comments and all unrelated configuration exactly as they were.
    bom = b"\xef\xbb\xbf" if old.startswith(b"\xef\xbb\xbf") else b""
    body = text
    if body and not body.endswith(("\n", "\r")):
        body += newline
    if body and not body.endswith(newline * 2):
        body += newline
    for name, filename in missing:
        body += f'[agents.{name}]{newline}config_file = "agents/{filename}"{newline}{newline}'
    new_bytes = bom + body.encode("utf-8")
    updated = parse_toml(body, str(path) + " (generated)")
    for name in roles:
        if not isinstance(updated.get("agents", {}).get(name, {}), dict):
            raise ValueError(f"Invalid generated table [agents.{name}], no changes made")
    return new_bytes, [name for name, _ in missing]
