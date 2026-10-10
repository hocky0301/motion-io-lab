"""Per-motion comparison of one recording and its replays, from trial logs only.

Used by the demo (synthetic logs) and, unchanged, by ``summarize-session`` for a real session
recorded on hardware: the trial names are ``<motion>-rec`` and ``<motion>-repNN``.
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np

from . import analysis as an
from .motions import MOTIONS
from .triallog import load_trial


def _grid(motion_ms: float, tail_ms: float = 600.0) -> np.ndarray:
    return np.arange(0.0, motion_ms + tail_ms, an.GRID_MS)


def _series_deg(root: Path, tid: str, cal, grid) -> tuple[dict, an.TrialSeries]:
    ts = an.extract(load_trial(root / tid))
    return an.to_deg(ts, cal, grid), ts


def still_marker_sd_deg(root: Path, trial_id: str) -> float:
    """Angle SD (deg) of the valid frames of a trial in which the marker did not move.

    That spread is the detector's noise (camera, lighting, compression), not the servo's, so the
    spread of repeated replays cannot be read as servo repeatability below this level."""
    ob = load_trial(root / trial_id).stream("observation")
    scale = math.degrees(ob.header["config"]["camera"]["angle_scale_rad"])
    vals = np.array([r["observed_value"] for r in ob.of("marker")
                     if r["valid"] and r["observed_value"] is not None])
    if len(vals) < 10:
        raise ValueError(f"{trial_id}: fewer than 10 valid frames; not enough for a spread")
    return float(np.std(vals * scale, ddof=1))


def pooled_floor_sd_deg(sds: list[float]) -> float:
    return float(np.sqrt(np.mean(np.square(sds))))


SKIP_START_MS = 500.0     # the first part of a trial, where the start state still matters


def _commanded_by_index(root: Path, tid: str) -> np.ndarray:
    """Commanded pulses of the valid set rows, in the order they were planned (no time grid)."""
    rows = [r for r in load_trial(root / tid).stream("command").of("set")
            if r["valid"] and r["commanded_pulse_us"] is not None]
    return np.array([r["commanded_pulse_us"] for r in rows], dtype=float)


def _send_lateness_ms(root: Path, tid: str) -> np.ndarray:
    rows = [r for r in load_trial(root / tid).stream("command").of("set")
            if r["valid"] and r["sent_monotonic_ns"] is not None
            and r["scheduled_monotonic_ns"] is not None]
    return np.array([(r["sent_monotonic_ns"] - r["scheduled_monotonic_ns"]) / 1e6 for r in rows])


def _leave_one_out(curves: list[np.ndarray]) -> list[float]:
    """RMS of each replay against the mean of the others: replay-to-replay difference with the
    recording left out (so a recording that happens to start elsewhere does not count)."""
    out = []
    for i, c in enumerate(curves):
        others = np.vstack([x for j, x in enumerate(curves) if j != i])
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)        # all-NaN columns are expected
            out.append(an.rms(c - np.nanmean(others, axis=0)))
    return out


def analyze_motion(root: Path, name: str, repeats: int, cal, *, plant_truth: dict | None = None,
                   camera_latency_ms: float | None = None,
                   noise_floor_sd_deg: float | None = None) -> tuple[dict, dict]:
    """Per-motion comparison of one recording (``<name>-rec``) and its replays.

    ``plant_truth`` (synthetic runs only) maps a trial id to (t_ms, true plant angle in deg); it
    gives the lag a perfect detector would see, so the lag estimator can be checked against it.
    ``noise_floor_sd_deg`` is the angle SD of a marker that does not move (detector noise).
    """
    dur = MOTIONS[name][1] * 1000.0
    grid = _grid(dur)
    late = grid >= SKIP_START_MS
    rec, rec_ts = _series_deg(root, f"{name}-rec", cal, grid)
    reps = [_series_deg(root, f"{name}-rep{r:02d}", cal, grid) for r in range(1, repeats + 1)]
    curves = {"grid": grid, "recording": rec, "replays": [r[0] for r in reps]}
    lag_c, lag_a, c_vs_a, a_vs_o, rep_vs_rec_o, rep_vs_rec_a, rep_vs_rec_c = [], [], [], [], [], [], []
    late_o, late_a, late_c = [], [], []
    ret_err, peaks, inv, bridged = [], [], [], []
    exp_c, exp_a, err_c, err_a = [], [], [], []
    for r_i, (cv, ts) in enumerate(reps, start=1):
        lc = an.estimate_lag_ms(cv["commanded_deg"], cv["observed_deg"])
        la = an.estimate_lag_ms(cv["applied_deg"], cv["observed_deg"])
        lag_c.append(lc["lag_ms"])
        lag_a.append(la["lag_ms"])
        if plant_truth is not None:
            pt, pdeg = plant_truth[f"{name}-rep{r_i:02d}"]
            truth = np.interp(grid, pt, pdeg)
            tc = an.estimate_lag_ms(cv["commanded_deg"], truth)["lag_ms"]
            ta = an.estimate_lag_ms(cv["applied_deg"], truth)["lag_ms"]
            if tc is not None and ta is not None and camera_latency_ms is not None:
                exp_c.append(tc + camera_latency_ms)
                exp_a.append(ta + camera_latency_ms)
                if lc["lag_ms"] is not None and la["lag_ms"] is not None:
                    err_c.append(lc["lag_ms"] - exp_c[-1])
                    err_a.append(la["lag_ms"] - exp_a[-1])
        c_vs_a.append(an.rms(cv["commanded_deg"] - cv["applied_deg"]))
        if la["lag_ms"] is not None:
            shifted = an.on_grid(ts.observed[0] - la["lag_ms"], ts.observed[1] * ts.angle_scale_deg,
                                 grid)
            a_vs_o.append(an.rms(cv["applied_deg"] - shifted))
        rep_vs_rec_o.append(an.rms(cv["observed_deg"] - rec["observed_deg"]))
        rep_vs_rec_a.append(an.rms(cv["applied_deg"] - rec["applied_deg"]))
        rep_vs_rec_c.append(an.rms(cv["commanded_deg"] - rec["commanded_deg"]))
        late_o.append(an.rms((cv["observed_deg"] - rec["observed_deg"])[late]))
        late_a.append(an.rms((cv["applied_deg"] - rec["applied_deg"])[late]))
        late_c.append(an.rms((cv["commanded_deg"] - rec["commanded_deg"])[late]))
        ret_err.append(an.window_mean(cv["observed_deg"], grid, dur + 200, dur + 500)
                       - an.window_mean(cv["observed_deg"], grid, 0, 100))
        peaks.append(float(np.nanmax(np.abs(cv["observed_deg"]))))
        inv.append(ts.obs_invalid)
        ok_o = np.isfinite(cv["observed_deg"])
        bf = an.bridged_fraction(ts.observed[0], grid[ok_o])
        if np.isfinite(bf):
            bridged.append(bf)
    # the commanded stage without a time grid: the same planned samples, compared one to one
    rec_cmd = _commanded_by_index(root, f"{name}-rec")
    by_index = []
    for r in range(1, repeats + 1):
        c = _commanded_by_index(root, f"{name}-rep{r:02d}")
        n = min(len(c), len(rec_cmd))
        by_index.append(an.rms((c[:n] - rec_cmd[:n]) * cal.deg_per_us))
    lateness = [float(np.percentile(_send_lateness_ms(root, f"{name}-rep{r:02d}"), 95))
                for r in range(1, repeats + 1)]
    total = sum(t.obs_total for _, t in reps)
    n_inv = sum(sum(i.values()) for i in inv)
    reasons: dict[str, int] = {}
    for i in inv:
        for k, v in i.items():
            reasons[str(k)] = reasons.get(str(k), 0) + v
    rep = {
        "duration_ms": dur, "repeats": repeats,
        "lag_ms_commanded_to_observed": an.summarize_values(lag_c),
        "lag_ms_applied_to_observed": an.summarize_values(lag_a),
        "rms_deg_commanded_vs_applied": an.summarize_values(c_vs_a),
        "rms_deg_applied_vs_observed_lag_aligned": an.summarize_values(a_vs_o),
        "rms_deg_replay_vs_recording_observed": an.summarize_values(rep_vs_rec_o),
        "rms_deg_replay_vs_recording_applied": an.summarize_values(rep_vs_rec_a),
        "rms_deg_replay_vs_recording_commanded": an.summarize_values(rep_vs_rec_c),
        "rms_deg_replay_vs_recording_observed_after_500ms": an.summarize_values(late_o),
        "rms_deg_replay_vs_recording_applied_after_500ms": an.summarize_values(late_a),
        "rms_deg_replay_vs_recording_commanded_after_500ms": an.summarize_values(late_c),
        "rms_deg_replay_vs_replay_observed": an.summarize_values(_leave_one_out(
            [c["observed_deg"] for c in curves["replays"]])),
        "rms_deg_replay_vs_replay_applied": an.summarize_values(_leave_one_out(
            [c["applied_deg"] for c in curves["replays"]])),
        "rms_deg_replay_vs_replay_commanded": an.summarize_values(_leave_one_out(
            [c["commanded_deg"] for c in curves["replays"]])),
        "rms_deg_commanded_replay_vs_recording_by_sample": an.summarize_values(by_index),
        "send_lateness_ms_p95": an.summarize_values(lateness),
        "return_error_deg_observed": an.summarize_values(ret_err),
        "peak_deg_observed": an.summarize_values(peaks),
        "repeat_sd_deg": {},
        "observation_frames": total, "observation_invalid": n_inv,
        "observation_invalid_reasons": reasons,
        "observation_bridged_fraction": an.summarize_values(bridged),
    }
    if plant_truth is not None:
        rep["lag_ms_commanded_to_observed_expected"] = an.summarize_values(exp_c)
        rep["lag_ms_applied_to_observed_expected"] = an.summarize_values(exp_a)
        rep["lag_error_ms_commanded_to_observed"] = an.summarize_values(err_c)
        rep["lag_error_ms_applied_to_observed"] = an.summarize_values(err_a)
    for key in ("commanded_deg", "applied_deg", "observed_deg"):
        st = an.repeat_stats([c[key] for c in curves["replays"]])
        rep["repeat_sd_deg"][key] = {"pooled_sd": st["pooled_sd"], "max_sd": st["max_sd"]}
        curves[f"mean_{key}"] = st.get("mean_curve")
        curves[f"sd_{key}"] = st.get("sd_curve")
    if noise_floor_sd_deg is not None and rep["repeat_sd_deg"]["observed_deg"]["pooled_sd"] is not None:
        sd = rep["repeat_sd_deg"]["observed_deg"]["pooled_sd"]
        rep["repeat_sd_deg"]["observed_deg"]["noise_floor_sd"] = noise_floor_sd_deg
        rep["repeat_sd_deg"]["observed_deg"]["pooled_sd_minus_floor"] = float(
            np.sqrt(max(sd * sd - noise_floor_sd_deg ** 2, 0.0)))
    return rep, curves


def summarize_session(root: Path, cal, motions: list[str], repeats: int,
                      still_trials: list[str] | None = None) -> dict:
    """Summary of a session directory. ``source`` is read from the trial headers, never assumed.

    ``still_trials`` are trials in which the marker did not move (observation only); they give
    the detector's noise floor, which is then reported next to each motion's repeat SD."""
    root = Path(root)
    floor = None
    if still_trials:
        sds = [still_marker_sd_deg(root, t) for t in still_trials]
        floor = pooled_floor_sd_deg(sds)
    sources = set()
    for m in motions:
        for tid in [f"{m}-rec"] + [f"{m}-rep{r:02d}" for r in range(1, repeats + 1)]:
            tr = load_trial(root / tid)
            sources |= {s.header["source"] for s in tr.streams.values()}
            if "observation" not in tr.streams:
                raise ValueError(f"{tid}: no observation stream (run `observe` for this trial)")
    out = {"schema_version": "motion-io-lab/session-summary-v1", "sources_in_logs": sorted(sources),
           "source": sorted(sources)[0] if len(sources) == 1 else "mixed",
           "calibration_version": cal.version, "calibration_source": cal.source, "repeats": repeats,
           "observation_noise_floor": None if floor is None else {
               "trials": still_trials, "sd_deg_per_trial": sds, "pooled_sd_deg": floor},
           "motions": {}}
    for m in motions:
        if m not in MOTIONS:
            raise ValueError(f"unknown motion {m!r}")
        out["motions"][m] = analyze_motion(root, m, repeats, cal, noise_floor_sd_deg=floor)[0]
    return out
