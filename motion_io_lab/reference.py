"""Comparison of a fresh demo summary with ``reference_results/demo_summary.json``.

Two levels, tried in this order:

1. **identical (strict)**: every number matches to 1e-6 relative (1e-9 absolute), counts and
   strings exactly. On the same machine and library versions this is what happens: the demo
   is deterministic given the seed, and the video path gives the same bytes.
2. **within cross-build tolerance**: only numbers computed from the *decoded video* (the
   detected marker angle and everything derived from it) may differ, by a fixed amount. They
   can move between OpenCV / ffmpeg builds because the mp4 coder differs. Everything that does
   not pass through the video (commanded and applied values, timing, failure cases) stays strict.

The cross-build amounts below are twice the largest difference measured between the reference
build (OpenCV 5.0.0.93) and an old one (OpenCV 4.9.0.80, numpy 1.26.4) on the same demo seed
(measured 2026-10-09; OpenCV 4.14.0.94 gave byte-identical results to 5.0.0.93):
lags 3.7 ms, peak / return error 0.35 deg, applied-vs-observed RMS 0.10 deg, other
angle RMS / SD values 0.03 deg. They are wide on purpose: they let a different build pass, they
are not a regression test (use ``--require-identical`` for that).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

STRICT_REL, STRICT_ABS = 1e-6, 1e-9

# (substring of the key path, absolute tolerance); the first rule that matches wins.
# Paths that contain "_expected" never go through the video and are always strict.
VIDEO_RULES: tuple[tuple[str, float], ...] = (
    ("observation_frames", 0.0),                 # a count: exact
    ("invalid", 3.0),                            # counts of frames the detector rejected
    ("rms_deg_applied_vs_observed", 0.2),
    ("peak_deg", 0.7),
    ("return_error", 0.7),
    ("lag_", 8.0),                               # ms
    ("bridged", 0.05),
    ("observ", 0.06),                            # angle RMS / SD derived from the detected angle
    ("noise_floor", 0.06),
    ("pooled_sd_minus_floor", 0.06),
)


def _video_tol(path: str) -> float | None:
    if "_expected" in path:
        return None
    for key, tol in VIDEO_RULES:
        if key in path:
            return tol
    return None


def _tol(path: str, ref: float, strict: bool) -> float:
    base = max(STRICT_ABS, STRICT_REL * abs(ref))
    if strict:
        return base
    v = _video_tol(path)
    return base if v is None else max(base, v)


def compare(new, ref, path: str = "", strict: bool = False) -> list[str]:
    """Differences between ``new`` and ``ref`` (empty list = they match). ``strict`` = level 1."""
    diffs: list[str] = []
    if isinstance(ref, dict):
        if not isinstance(new, dict):
            return [f"{path}: expected an object"]
        for k in ref:
            if k not in new:
                diffs.append(f"{path}/{k}: missing in the new result")
            else:
                diffs += compare(new[k], ref[k], f"{path}/{k}", strict)
        for k in new:
            if k not in ref:
                diffs.append(f"{path}/{k}: not in the reference (extra key)")
    elif isinstance(ref, list):
        if not isinstance(new, list) or len(new) != len(ref):
            diffs.append(f"{path}: list length {None if not isinstance(new, list) else len(new)} != {len(ref)}")
        else:
            for i, (a, b) in enumerate(zip(new, ref)):
                diffs += compare(a, b, f"{path}[{i}]", strict)
    elif isinstance(ref, bool) or isinstance(ref, str) or ref is None:
        if new != ref:
            diffs.append(f"{path}: {new!r} != {ref!r}")
    elif isinstance(ref, (int, float)):
        if not isinstance(new, (int, float)) or isinstance(new, bool):
            diffs.append(f"{path}: {new!r} is not a number")
        elif isinstance(ref, int) and isinstance(new, int) and (strict or _video_tol(path) is None):
            if new != ref:
                diffs.append(f"{path}: {new} != {ref} (exact)")
        elif not (math.isfinite(new) and abs(new - ref) <= _tol(path, ref, strict)):
            diffs.append(f"{path}: {new} vs reference {ref} (tolerance {_tol(path, ref, strict):.3g})")
    return diffs


def match_level(new, ref) -> tuple[str, list[str]]:
    """("identical" | "within_cross_build_tolerance" | "different", the differences found)."""
    strict = compare(new, ref, strict=True)
    if not strict:
        return "identical", []
    loose = compare(new, ref, strict=False)
    if loose:
        return "different", loose
    return "within_cross_build_tolerance", strict


def compare_files(new_path: str | Path, ref_path: str | Path) -> list[str]:
    """Differences outside the cross-build tolerance (empty = acceptable)."""
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    ref = json.loads(Path(ref_path).read_text(encoding="utf-8"))
    return compare(new, ref)


def level_of_files(new_path: str | Path, ref_path: str | Path) -> tuple[str, list[str]]:
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    ref = json.loads(Path(ref_path).read_text(encoding="utf-8"))
    return match_level(new, ref)
