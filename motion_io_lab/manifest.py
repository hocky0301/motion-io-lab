"""run_manifest.json: what ran, with which versions, and the hash of every output."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

MANIFEST_NAME = "run_manifest.json"


def _is_manifest(name: str) -> bool:
    return name == MANIFEST_NAME or (name.startswith("run_manifest.") and name.endswith(".json"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> str | None:
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(["git", "-C", str(here), *args], capture_output=True, text=True,
                             timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _own_repo() -> bool:
    """True when this package sits directly in the top level of a git checkout (our own repo).
    A copy installed inside some other project's repo must not report that project's commit."""
    top = _git("rev-parse", "--show-toplevel")
    if not top:
        return False
    here = Path(__file__).resolve().parent
    return Path(top.strip()).resolve() / "motion_io_lab" == here


def git_commit() -> str | None:
    if not _own_repo():
        return None
    sha = (_git("rev-parse", "HEAD") or "").strip()
    return sha if len(sha) == 40 else None


def git_dirty() -> bool | None:
    """True when the working tree differs from the commit (uncommitted edits). None = unknown."""
    if not _own_repo():
        return None
    status = _git("status", "--porcelain", "--untracked-files=no")
    return None if status is None else bool(status.strip())


def environment() -> dict:
    import numpy as np

    env = {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "motion_io_lab": __version__,
    }
    try:
        import cv2

        env["opencv"] = cv2.__version__
        try:
            from importlib.metadata import version

            env["opencv_python_package"] = version("opencv-python")     # e.g. 5.0.0.93
        except Exception:  # noqa: BLE001 - another distribution name (headless) or not installed as a package
            env["opencv_python_package"] = None
    except ImportError:  # pragma: no cover - optional at import time
        env["opencv"] = None
    try:
        import serial

        env["pyserial"] = serial.__version__
    except ImportError:  # pragma: no cover
        env["pyserial"] = None
    return env


def write_run_manifest(out_dir: Path, command: str, args: dict, seed: int | None = None,
                       source: str | None = None, extra: dict | None = None) -> dict:
    """Write run_manifest.json into out_dir; hashes every other file below out_dir."""
    out_dir = Path(out_dir)
    files = {}
    for p in sorted(out_dir.rglob("*")):
        if p.is_file() and not _is_manifest(p.name) and not p.name.endswith(".part"):
            files[p.relative_to(out_dir).as_posix()] = sha256_file(p)
    manifest = {
        "schema_version": "motion-io-lab/run-manifest-v1",
        "command": command,
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in args.items()
                 if not callable(v)},
        "seed": seed,
        "source": source,
        "git_commit": git_commit(),
        "git_dirty": git_dirty(),
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": environment(),
        "output_sha256": files,
    }
    if extra:
        manifest.update(extra)
    # Several commands can share one trial directory (record + observe): the first one takes
    # run_manifest.json, later ones run_manifest.<command>.json. Nothing is overwritten.
    target = out_dir / MANIFEST_NAME
    if target.exists():
        target = out_dir / f"run_manifest.{command}.json"
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
