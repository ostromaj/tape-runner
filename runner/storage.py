"""Saves dashboard.json to the `data` branch as a single force-pushed commit.

Uses compare-and-swap (--force-with-lease against the commit we last saw), so a Reset made
from the dashboard is never silently overwritten by the running bot."""
import json
import os
import shutil
import subprocess
import tempfile


def _remote():
    if os.environ.get("TR_REMOTE"):
        return os.environ["TR_REMOTE"]
    return f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"


def _git(args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def remote_sha():
    r = _git(["ls-remote", _remote(), "refs/heads/data"])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    line = r.stdout.strip()
    return line.split()[0] if line else ""


def load():
    """Returns (state or None, sha)."""
    sha = remote_sha()
    if not sha:
        return None, ""
    d = tempfile.mkdtemp()
    try:
        _git(["init", "-q"], d)
        r = _git(["fetch", "-q", "--depth=1", _remote(), "refs/heads/data"], d)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])
        r = _git(["show", "FETCH_HEAD:dashboard.json"], d)
        sha = _git(["rev-parse", "FETCH_HEAD"], d).stdout.strip()
        try:
            return json.loads(r.stdout), sha
        except json.JSONDecodeError:
            return None, sha
    finally:
        shutil.rmtree(d, ignore_errors=True)


def save(text, expected_sha):
    """Returns ('ok', new_sha) | ('conflict', None) | ('error', message)."""
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "dashboard.json"), "w") as f:
            f.write(text)
        _git(["init", "-q", "-b", "data"], d)
        _git(["config", "user.name", "tape-runner-bot"], d)
        _git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], d)
        _git(["add", "dashboard.json"], d)
        _git(["commit", "-q", "-m", "Tape Runner update"], d)
        r = _git(["push", "-q", f"--force-with-lease=refs/heads/data:{expected_sha}", _remote(), "HEAD:refs/heads/data"], d)
        if r.returncode == 0:
            return "ok", _git(["rev-parse", "HEAD"], d).stdout.strip()
        err = r.stderr.lower()
        if "stale info" in err or "rejected" in err or "fetch first" in err:
            return "conflict", None
        return "error", r.stderr.strip()[:200]
    finally:
        shutil.rmtree(d, ignore_errors=True)
