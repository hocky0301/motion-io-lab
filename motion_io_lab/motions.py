"""Three short test motions, and the motion-file format the player reads.

All motions are functions of time returning a normalised axis value in [-1, 1].
``look_wait_nod`` is turn, wait, nod: a look to one side, a pause, a short nod.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MOTION_SCHEMA = "motion-io-lab/motion-v1"


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (6 * u - 15) + 10)       # minimum-jerk blend 0 -> 1


def _blend(t: float, t0: float, t1: float, a: float, b: float) -> float:
    return a + (b - a) * _smooth((t - t0) / (t1 - t0))


def sweep(t: float) -> float:
    """0 -> +0.8 -> -0.8 -> 0, smooth. 2.4 s."""
    if t < 0.6:
        return _blend(t, 0.0, 0.6, 0.0, 0.8)
    if t < 1.4:
        return _blend(t, 0.6, 1.4, 0.8, -0.8)
    return _blend(t, 1.4, 2.0, -0.8, 0.0)


def step_hold(t: float) -> float:
    """Fast edge to +0.6, hold, fast edge back. The 50 ms edges exceed the host slew limit."""
    if t < 0.3:
        return 0.0
    if t < 0.35:
        return _blend(t, 0.3, 0.35, 0.0, 0.6)
    if t < 1.35:
        return 0.6
    return _blend(t, 1.35, 1.40, 0.6, 0.0)


def look_wait_nod(t: float) -> float:
    """Turn to +0.7 (0.5 s), wait (0.5 s), nod twice (+-0.2, 0.6 s), return (0.5 s)."""
    if t < 0.5:
        return _blend(t, 0.0, 0.5, 0.0, 0.7)
    if t < 1.0:
        return 0.7
    if t < 1.6:
        return 0.7 - 0.2 * math.sin(2 * math.pi * (t - 1.0) / 0.3)
    return _blend(t, 1.6, 2.1, 0.7, 0.0)


MOTIONS = {
    "sweep": (sweep, 2.4),
    "step_hold": (step_hold, 1.6),
    "look_wait_nod": (look_wait_nod, 2.4),
}


def sample_motion(name: str, rate_hz: float = 50.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (t_ms, x_norm) sampled at ``rate_hz``, ending with the motion at rest."""
    fn, dur = MOTIONS[name]
    n = int(round(dur * rate_hz)) + 1
    t = np.arange(n) / rate_hz
    return t * 1000.0, np.array([fn(float(u)) for u in t])


@dataclass
class Motion:
    name: str
    t_ms: np.ndarray            # strictly increasing, starts at 0
    pulse_us: np.ndarray
    origin: str                 # where it came from (file name, "trial", ...)

    def __post_init__(self) -> None:
        if len(self.t_ms) != len(self.pulse_us) or len(self.t_ms) < 2:
            raise ValueError("a motion needs at least two samples")
        if not (np.isfinite(self.t_ms).all() and np.isfinite(self.pulse_us).all()):
            raise ValueError("motion contains non-finite values")
        if (np.diff(self.t_ms) <= 0).any():
            raise ValueError("motion time must be strictly increasing")

    @property
    def duration_ms(self) -> float:
        return float(self.t_ms[-1] - self.t_ms[0])

    def resample(self, period_ms: float, start_ms: float = 0.0, end_ms: float | None = None,
                 speed: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        """Linear resampling on a fixed period. Returns (t_out_ms, pulse). t_out starts at 0."""
        if not speed > 0:
            raise ValueError("speed must be positive")
        t0 = self.t_ms[0] + start_ms
        t1 = self.t_ms[-1] if end_ms is None else self.t_ms[0] + end_ms
        if not (self.t_ms[0] <= t0 < t1 <= self.t_ms[-1] + 1e-9):
            raise ValueError(f"segment {start_ms}..{end_ms} ms is outside the motion "
                             f"(0..{self.duration_ms:.0f} ms)")
        n = int(math.floor((t1 - t0) / speed / period_ms + 1e-9)) + 1
        t_out = np.arange(n) * period_ms
        src = t0 + t_out * speed
        return t_out, np.interp(src, self.t_ms, self.pulse_us)


def write_motion_file(path: str | Path, t_ms, pulse_us, name: str, origin: str) -> None:
    p = Path(path)
    if p.exists():
        raise FileExistsError(p)
    with p.open("x", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": MOTION_SCHEMA, "record_type": "header",
                            "name": name, "units": "pulse_us", "origin": origin}) + "\n")
        for t, u in zip(t_ms, pulse_us):
            f.write(json.dumps({"t_ms": round(float(t), 3), "pulse_us": round(float(u), 3)}) + "\n")


def load_motion_file(path: str | Path) -> Motion:
    p = Path(path)
    ts, us = [], []
    name = p.stem
    for i, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p.name}:{i}: not JSON ({exc})") from exc
        if rec.get("record_type") == "header":
            if rec.get("schema_version") != MOTION_SCHEMA:
                raise ValueError(f"{p.name}:{i}: unsupported schema {rec.get('schema_version')!r}")
            if rec.get("units") != "pulse_us":
                raise ValueError(f"{p.name}:{i}: units must be pulse_us, got {rec.get('units')!r}")
            name = rec.get("name", name)
            continue
        if "t_ms" not in rec or "pulse_us" not in rec:
            raise ValueError(f"{p.name}:{i}: needs t_ms and pulse_us")
        ts.append(float(rec["t_ms"]))
        us.append(float(rec["pulse_us"]))
    if not ts:
        raise ValueError(f"{p.name}: no samples")
    t = np.array(ts)
    return Motion(name, t - t[0], np.array(us), p.name)


def motion_from_trial(trial) -> Motion:
    """The commanded trajectory of a recorded trial (what was sent, after host shaping)."""
    sets = [r for r in trial.stream("command").of("set")
            if r["valid"] and r["commanded_pulse_us"] is not None]
    if len(sets) < 2:
        raise ValueError(f"trial {trial.trial_id} has fewer than two commands")
    ref = "sent_monotonic_ns" if all(r["sent_monotonic_ns"] is not None for r in sets) \
        else "scheduled_monotonic_ns"
    t = np.array([r[ref] for r in sets], dtype=np.float64)
    # Use the scheduled time when available: it is the intended spacing, free of send jitter.
    if all(r["scheduled_monotonic_ns"] is not None for r in sets):
        t = np.array([r["scheduled_monotonic_ns"] for r in sets], dtype=np.float64)
    t_ms = (t - t[0]) / 1e6
    u = np.array([r["commanded_pulse_us"] for r in sets], dtype=np.float64)
    return Motion(trial.trial_id, t_ms, u, f"trial:{trial.trial_id}")


def load_motion(path: str | Path) -> Motion:
    """A motion file (.jsonl with a motion-v1 header) or a recorded trial directory."""
    from .triallog import load_trial

    p = Path(path)
    if p.is_dir():
        return motion_from_trial(load_trial(p))
    return load_motion_file(p)
