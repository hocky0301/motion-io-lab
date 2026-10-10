"""Align and compare three separate series of one motion:

    commanded   what the host sent                (pulse_us, from command.jsonl 'set')
    applied     what the firmware says it wrote   (pulse_us, from 'reply' EXECUTED lines)
    observed    what the camera saw               (norm_angle, from observation.jsonl)

They are never merged into one column. "Applied" is the value written to the PWM chip,
not a measured position; "observed" is a marker's angle in the image, delayed by an
unknown camera pipeline. A lag estimated by cross-correlation is the *total* delay
between two series (servo response + camera pipeline + clock offset assumptions), not
the camera latency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .calibration import Calibration
from .triallog import Trial

GRID_MS = 10.0


@dataclass
class TrialSeries:
    trial_id: str
    t0_ns: int
    commanded: tuple[np.ndarray, np.ndarray]     # (t_ms, pulse_us)
    applied: tuple[np.ndarray, np.ndarray]       # (t_ms, pulse_us)
    observed: tuple[np.ndarray, np.ndarray]      # (t_ms, norm_angle), valid frames only
    obs_total: int
    obs_invalid: dict[str, int]
    angle_scale_deg: float | None
    notes: list[str]


def extract(trial: Trial, media_offset_ms: float = 0.0) -> TrialSeries:
    """Pull the three series out of a trial. Time zero = the first command handed to the link
    (or its scheduled time in a dry run).

    For a video *file*, ``media_offset_ms`` says where the file's time zero sits on that
    timeline; 0 means "the video started with the first command", which is an assumption.
    """
    cmd = trial.stream("command")
    sets = [r for r in cmd.of("set") if r["valid"] and r["commanded_pulse_us"] is not None]
    if not sets:
        raise ValueError(f"{trial.trial_id}: no commands")
    ref = "sent_monotonic_ns" if sets[0]["sent_monotonic_ns"] is not None else "scheduled_monotonic_ns"
    t0 = int(sets[0][ref])
    c_t = np.array([(r[ref] - t0) / 1e6 for r in sets])
    c_v = np.array([r["commanded_pulse_us"] for r in sets], dtype=float)
    ex = [r for r in cmd.of("reply") if r["reply_status"] == "EXECUTED" and r["reply_cmd"] == "SET"
          and r["device_applied_pulse_us"] is not None]
    a_t = np.array([(r["received_monotonic_ns"] - t0) / 1e6 for r in ex])
    a_v = np.array([r["device_applied_pulse_us"] for r in ex], dtype=float)
    notes: list[str] = []
    o_t = np.array([]), np.array([])
    obs_total, inv, scale_deg = 0, {}, None
    if "observation" in trial.streams:
        ob = trial.stream("observation")
        rows = ob.of("marker")
        obs_total = len(rows)
        cam = ob.header.get("config", {}).get("camera", {})
        if cam.get("angle_scale_rad"):
            scale_deg = math.degrees(cam["angle_scale_rad"])
        ts, vs = [], []
        for r in rows:
            if not r["valid"]:
                inv[r["invalid_reason"]] = inv.get(r["invalid_reason"], 0) + 1
                continue
            if r["observed_value"] is None:
                continue
            if r["source_kind"] == "file":
                t = r["media_pos_ms"] + media_offset_ms
            else:
                t = (r["received_monotonic_ns"] - t0) / 1e6
            ts.append(t)
            vs.append(r["observed_value"])
        o_t = np.array(ts), np.array(vs)
        if rows and rows[0]["source_kind"] == "file":
            notes.append(f"video file: time axis = media_pos_ms + {media_offset_ms} ms "
                         "(assumed alignment, not measured)")
    return TrialSeries(trial.trial_id, t0, (c_t, c_v), (a_t, a_v), o_t, obs_total, inv, scale_deg,
                       notes)


def on_grid(t: np.ndarray, v: np.ndarray, grid: np.ndarray, hold: bool = False,
            max_gap_ms: float = 120.0) -> np.ndarray:
    """Resample onto ``grid``. ``hold`` = zero-order hold (for commands). Points farther than
    ``max_gap_ms`` from any sample become NaN, so a dropout is a gap, not a straight line."""
    if len(t) == 0:
        return np.full(len(grid), np.nan)
    if hold:
        idx = np.searchsorted(t, grid, side="right") - 1
        out = np.where(idx >= 0, v[np.clip(idx, 0, len(v) - 1)], np.nan)
        return out
    out = np.interp(grid, t, v)
    j = np.searchsorted(t, grid)
    left = t[np.clip(j - 1, 0, len(t) - 1)]
    right = t[np.clip(j, 0, len(t) - 1)]
    gap = right - left
    near = np.minimum(np.abs(grid - left), np.abs(right - grid))
    # never extrapolate past the first/last sample, and never bridge a hole wider than max_gap_ms
    bad = ((gap > max_gap_ms) & (near > max_gap_ms / 2)) | (grid < t[0] - 1) | (grid > t[-1] + 1)
    out[bad] = np.nan
    return out


def to_deg(ts: TrialSeries, cal: Calibration | None, grid: np.ndarray) -> dict[str, np.ndarray]:
    """The three series on one grid, in degrees (needs a calibration and the camera scale)."""
    out: dict[str, np.ndarray] = {}
    c = on_grid(*ts.commanded, grid, hold=True)
    a = on_grid(*ts.applied, grid, hold=True)
    out["commanded_us"], out["applied_us"] = c, a
    if cal is not None:
        out["commanded_deg"], out["applied_deg"] = cal.pulse_to_deg(c), cal.pulse_to_deg(a)
    o = on_grid(*ts.observed, grid)
    out["observed_norm"] = o
    if ts.angle_scale_deg is not None:
        out["observed_deg"] = o * ts.angle_scale_deg
    return out


def bridged_fraction(t_valid: np.ndarray, grid: np.ndarray, gap_factor: float = 1.5) -> float:
    """Share of the grid points (inside the data) that were filled by interpolating across a gap
    wider than ``gap_factor`` frame periods, i.e. across one or more invalid frames.

    A metric computed on the grid treats those points like measurements; this says how many of
    them are really straight lines between two valid frames."""
    t = np.asarray(t_valid, float)
    if len(t) < 3:
        return float("nan")
    inside = (grid >= t[0]) & (grid <= t[-1])
    if not inside.any():
        return float("nan")
    j = np.clip(np.searchsorted(t, grid[inside]), 1, len(t) - 1)
    gap = t[j] - t[j - 1]
    return float(np.mean(gap > gap_factor * float(np.median(np.diff(t)))))


def rms(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def estimate_lag_ms(ref: np.ndarray, other: np.ndarray, dt_ms: float = GRID_MS,
                    max_lag_ms: float = 400.0) -> dict:
    """Lag L such that other(t) ~ ref(t - L). Normalised cross-correlation on the common valid
    samples, sub-sample by a parabola. Returns lag_ms (None if not estimable) and the peak value.

    If the best match sits on the edge of the search window (``max_lag_ms``), the true lag is
    probably outside it and the number would be a confident-looking guess, so ``lag_ms`` is None
    and ``at_search_limit`` is True."""
    r = np.asarray(ref, float)
    o = np.asarray(other, float)
    m = int(max_lag_ms / dt_ms)
    best, scores = None, {}
    for k in range(-m, m + 1):
        if k >= 0:
            a, b = r[: len(r) - k] if k else r, o[k:]
        else:
            a, b = r[-k:], o[: len(o) + k]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < 20:
            continue
        a, b = a[ok] - a[ok].mean(), b[ok] - b[ok].mean()
        den = math.sqrt(float((a * a).sum() * (b * b).sum()))
        if den == 0:
            continue
        scores[k] = float((a * b).sum() / den)
    if not scores:
        return {"lag_ms": None, "peak_corr": None, "note": "not enough valid samples"}
    k0 = max(scores, key=scores.get)
    if abs(k0) >= m:
        return {"lag_ms": None, "peak_corr": scores[k0], "at_search_limit": True,
                "note": f"best match at the edge of the +-{max_lag_ms:g} ms window; lag not estimable"}
    lag = float(k0)
    if k0 - 1 in scores and k0 + 1 in scores:
        y0, y1, y2 = scores[k0 - 1], scores[k0], scores[k0 + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            lag += 0.5 * (y0 - y2) / den
    return {"lag_ms": lag * dt_ms, "peak_corr": scores[k0]}


def repeat_stats(curves: list[np.ndarray]) -> dict:
    """Spread across repeats on a common grid: pooled SD (ddof=1) per time point -> RMS over time."""
    arr = np.vstack(curves)
    n = np.isfinite(arr).sum(axis=0)
    ok = n >= 2
    mean = np.full(arr.shape[1], np.nan)
    sd_full = np.full(arr.shape[1], np.nan)
    have = n >= 1
    mean[have] = np.nansum(arr[:, have], axis=0) / n[have]
    if not ok.any():
        return {"n_repeats": len(curves), "pooled_sd": None, "max_sd": None, "mean_curve": mean,
                "sd_curve": sd_full}
    dev = arr[:, ok] - mean[ok]
    sd_full[ok] = np.sqrt(np.nansum(dev ** 2, axis=0) / (n[ok] - 1))
    return {"n_repeats": len(curves), "pooled_sd": float(np.sqrt(np.mean(sd_full[ok] ** 2))),
            "max_sd": float(np.max(sd_full[ok])), "mean_curve": mean, "sd_curve": sd_full}


def summarize_values(vals: list[float]) -> dict:
    v = np.array([x for x in vals if x is not None and np.isfinite(x)])
    if not len(v):
        return {"n": 0, "mean": None, "sd": None}
    return {"n": int(len(v)), "mean": float(v.mean()),
            "sd": float(v.std(ddof=1)) if len(v) > 1 else None,
            "min": float(v.min()), "max": float(v.max()),
            "values": [float(x) for x in v]}      # every replay's value, so plots can show points


def window_mean(curve: np.ndarray, grid: np.ndarray, lo: float, hi: float) -> float:
    m = (grid >= lo) & (grid <= hi) & np.isfinite(curve)
    return float(curve[m].mean()) if m.any() else float("nan")
