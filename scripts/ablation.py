#!/usr/bin/env python
"""Which stage of the replay-vs-recording comparison does each assumed noise source move?

The demo compares a recording with its replays at three stages (commanded / applied /
observed). Whether the stage a difference shows up in says anything about where it came from
can only be checked by switching the assumed sources on and off one at a time, on the same
virtual device, and watching which numbers move. This script does that.

    python scripts/ablation.py                      # seeds 3 and 7 -> reference_results/ablation.json
    python scripts/ablation.py --seeds 7 --out outputs/ablation.json

SYNTHETIC: every number here comes from the demo's virtual device, synthetic servo and rendered
video. It tests the reading of the metrics, not any real servo.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from motion_io_lab import analysis as an
from motion_io_lab.demo import Rig, run_motions, synthetic_calibration
from motion_io_lab.motions import MOTIONS
from motion_io_lab.triallog import load_trial

ROOT = Path(__file__).resolve().parent.parent

# name -> (what is changed, Rig keyword arguments)
VARIANTS = {
    "baseline": ("the demo's assumptions (every trial starts from a homed horn)", {}),
    "carry_over_start": ("the horn keeps the previous trial's end position at the start",
                         {"start_state": "carry_over"}),
    "gain_jitter_off": ("no trial-to-trial gain change (1 % -> 0)", {"gain_sigma": 0.0}),
    "gain_jitter_5pct": ("trial-to-trial gain change 5 % instead of 1 %", {"gain_sigma": 0.05}),
    "deadband_off": ("no stiction dead band (0.12 deg -> 0)", {"deadband_deg": 0.0, "deadband_spread": 0.0}),
    "deadband_0p5deg": ("dead band 0.5 deg (about 5 us) instead of 0.12 deg", {"deadband_deg": 0.5}),
    "timing_jitter_off": ("no host wake-up jitter and no link delay jitter", {"timing_jitter": False}),
    "camera_dropout_off": ("no marker dropouts and no second blob", {"dropout_p": 0.0, "distractor_p": 0.0}),
    "marker_noise_1px": ("marker position noise 1 px (the demo uses 0)", {"marker_noise_px": 1.0}),
}
KEYS = ("rms_deg_replay_vs_recording_commanded", "rms_deg_replay_vs_recording_applied",
        "rms_deg_replay_vs_recording_observed", "rms_deg_replay_vs_replay_observed",
        "rms_deg_replay_vs_recording_observed_after_500ms")


GRIDS_MS = (2.0, 5.0, 10.0, 20.0, 40.0)


def command_stage_by_grid(root: Path, cal, motion: str, repeats: int = 5) -> dict:
    """The commanded stage (replay vs recording, RMS) when the comparison grid is 2 .. 40 ms wide.
    The replay sends the same values at times that wobble by about 1 ms; on a coarse grid a value
    that changed 1 ms earlier or later shows up as a whole step, on a fine grid it hardly does."""
    out = {}
    dur = MOTIONS[motion][1] * 1000.0
    rec = an.extract(load_trial(root / f"{motion}-rec"))
    for g in GRIDS_MS:
        grid = np.arange(0.0, dur + 600.0, g)
        r0 = cal.pulse_to_deg(an.on_grid(*rec.commanded, grid, hold=True))
        vals = []
        for r in range(1, repeats + 1):
            ts = an.extract(load_trial(root / f"{motion}-rep{r:02d}"))
            vals.append(an.rms(cal.pulse_to_deg(an.on_grid(*ts.commanded, grid, hold=True)) - r0))
        out[f"{g:g}"] = float(np.mean(vals))
    return out


def one(args) -> dict:
    seed, name = args
    desc, kw = VARIANTS[name]
    tmp = Path(tempfile.mkdtemp(prefix=f"abl-{name}-"))
    try:
        rig = Rig(tmp, seed, **kw)
        cal = synthetic_calibration()
        floor = rig.noise_floor(cal)
        motions, _ = run_motions(rig, 5, cal, floor["pooled_sd_deg"])
        res = {m: {k: r[k]["mean"] for k in KEYS} for m, r in motions.items()}
        for m, r in motions.items():
            res[m]["observed_repeat_sd"] = r["repeat_sd_deg"]["observed_deg"]["pooled_sd"]
        row = {"seed": seed, "variant": name, "what": desc, "settings": rig.settings(),
               "noise_floor_sd_deg": floor["pooled_sd_deg"], "motions": res}
        if name == "baseline":
            row["command_stage_by_grid_ms"] = {m: command_stage_by_grid(tmp, cal, m) for m in motions}
        return row
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default="3,7")
    ap.add_argument("--out", default=str(ROOT / "reference_results" / "ablation.json"))
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    jobs = [(s, n) for s in seeds for n in VARIANTS]
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        rows = list(ex.map(one, jobs))
    out = {"source": "synthetic", "seeds": seeds, "repeats": 5,
           "note": "RMS values in degrees, mean over 5 replays; see docs/DESIGN.md section 5",
           "rows": rows}
    p = Path(a.out)
    if p.exists():
        sys.exit(f"{p} exists; remove it to regenerate")
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", p)
    # a compact table, mean over the seeds
    print(f"{'variant':20s} {'motion':14s}  cmd    app    obs   obs(rep-rep)  obs(after .5s)  obs SD")
    for name in VARIANTS:
        for m in rows[0]["motions"]:
            sel = [r["motions"][m] for r in rows if r["variant"] == name]
            avg = lambda k: sum(x[k] for x in sel) / len(sel)  # noqa: E731
            print(f"{name:20s} {m:14s}  {avg(KEYS[0]):.3f}  {avg(KEYS[1]):.3f}  {avg(KEYS[2]):.3f}"
                  f"  {avg(KEYS[3]):.3f}        {avg(KEYS[4]):.3f}          {avg('observed_repeat_sd'):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
