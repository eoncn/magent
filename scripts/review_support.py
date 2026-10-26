"""Shared read-only Git snapshot for synchronous and supervised reviews."""
import hashlib
import os
import stat
import subprocess
import sys


def fingerprint(directory):
    """Hash HEAD, index and tracked/non-ignored untracked contents, including submodules."""
    digest = hashlib.sha256()

    def record(value):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    def git(root, *args, optional=False):
        result = subprocess.run(
            ["git", *args], cwd=root, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if result.returncode != 0 and not (optional and result.returncode == 1):
            raise RuntimeError(result.stderr.decode(errors="replace").strip()
                               or "Git fingerprint command failed")
        record(str(result.returncode).encode())
        return result.stdout

    def snapshot_path(path):
        record(path)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            record(b"missing")
            return
        record(str(stat.S_IFMT(info.st_mode)).encode())
        record(str(stat.S_IMODE(info.st_mode)).encode())
        if stat.S_ISLNK(info.st_mode):
            record(os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            content = hashlib.sha256()
            with open(path, "rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    content.update(block)
            record(content.digest())
        elif stat.S_ISDIR(info.st_mode):
            if os.path.lexists(os.path.join(path, b".git")):
                snapshot_repo(path)
            else:
                for name in sorted(os.listdir(path)):
                    snapshot_path(os.path.join(path, name))
        else:
            raise RuntimeError("unsupported file type: " + os.fsdecode(path))

    def snapshot_repo(root):
        record(root)
        record(git(root, "rev-parse", "--verify", "--quiet", "HEAD", optional=True))
        record(git(root, "symbolic-ref", "-q", "HEAD", optional=True))
        record(git(root, "status", "--porcelain=v1", "-z", "-uall", "--ignore-submodules=none"))
        record(git(root, "ls-files", "--stage", "-z"))
        paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for path in sorted(set(paths.split(b"\0")) - {b""}):
            snapshot_path(os.path.join(root, path))

    snapshot_repo(os.fsencode(os.path.realpath(directory)))
    return digest.hexdigest()


if __name__ == "__main__":
    try:
        print(fingerprint(os.getcwd()))
    except (OSError, RuntimeError) as error:
        print("ERROR: cannot fingerprint repository: " + str(error), file=sys.stderr)
        sys.exit(1)
