#!/usr/bin/env python3
"""Update existing SSH checkouts to this committed version, with optional installation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]

# All user data travels through stdin, never interpolated into the SSH command.
REMOTE = r'''
import json, pathlib, subprocess, sys
options = json.loads(OPTIONS)
root = pathlib.Path(options['path']).expanduser()
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
try:
    if pathlib.Path(git('rev-parse', '--show-toplevel')).resolve() != root.resolve():
        raise RuntimeError('Remote path must be the root of a magent Git checkout')
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise RuntimeError('Remote checkout has local changes; sync stopped')
    upstream = git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
    remote = git('config', 'branch.' + git('branch', '--show-current') + '.remote')
    if options['dry_run']:
        print(json.dumps({'checkout': str(root), 'current': git('rev-parse', 'HEAD'),
                          'expected': options['commit'], 'upstream': upstream,
                          'install': options['install'], 'dry_run': True}))
    else:
        subprocess.run(['git', '-C', str(root), 'fetch', '--', remote], check=True)
        if git('rev-parse', upstream) != options['commit']:
            raise RuntimeError('Upstream differs from source HEAD; push the intended version first')
        subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor',
                        'HEAD', options['commit']], check=True)
        subprocess.run(['git', '-C', str(root), 'merge', '--ff-only', options['commit']], check=True)
        if options['install']:
            subprocess.run([sys.executable, str(root / 'tools/install.py')], check=True)
        print(json.dumps({'checkout': str(root), 'commit': git('rev-parse', 'HEAD')}))
except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
    print('Sync failed: ' + str(error), file=sys.stderr)
    sys.exit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", required=True, help="SSH alias; repeat to update multiple hosts")
    parser.add_argument("--path", default="~/otherWorks/magent", help="checkout path on every target")
    parser.add_argument("--install", action="store_true", help="also link both Codex installations on each target")
    parser.add_argument("--dry-run", action="store_true", help="read-only checks, without fetch, merge or installation")
    args = parser.parse_args()
    try:
        def git(*items):
            return subprocess.check_output(["git", "-C", str(SOURCE), *items], text=True).strip()
        if Path(git("rev-parse", "--show-toplevel")).resolve() != SOURCE:
            raise RuntimeError("Initialize this source directory as its own Git repository first")
        if git("status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError("Commit local changes before syncing")
        commit = git("rev-parse", "HEAD")
        options = json.dumps({"path": args.path, "commit": commit,
                              "install": args.install, "dry_run": args.dry_run})
        program = REMOTE.replace("OPTIONS", repr(options), 1)
        for host in dict.fromkeys(args.host):
            if host.startswith("-"):
                raise ValueError("SSH host must not start with a dash")
            print("Sync: " + host, flush=True)
            subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            host, "python3 -"], input=program, text=True, check=True)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
