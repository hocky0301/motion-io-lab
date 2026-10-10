#!/usr/bin/env python
"""How well does the marker detector read a rendered video back? (SYNTHETIC)

A marker is drawn at a known angle (4 deg sine, 2 s, 30 fps), encoded to mp4, read back and
detected. The angle error is reported for several random seeds and for several amounts of
marker-position jitter (the demo uses none, an ideal marker; a real marker wobbles).

    python scripts/observe_selftest.py                 # -> reference_results/observe_selftest.json

This checks the detector on drawn images. It is not the accuracy of any real camera.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from motion_io_lab import observe as obs
from motion_io_lab.manifest import environment
from motion_io_lab.synth import VideoSpec, render_video
from motion_io_lab.triallog import load_trial

ROOT = Path(__file__).resolve().parent.parent
SPEC = VideoSpec()


def run_one(tmp: Path, seed: int, sigma_px: float) -> dict:
    seconds = 2.0
    t = np.linspace(0, seconds, int(seconds * 1000) + 1)
    ang = 4.0 * np.sin(2 * np.pi * t / seconds)
    video = tmp / f"v-{seed}-{sigma_px}.mp4"
    truth = render_video(video, t, ang, seconds, SPEC, latency_s=0.0, seed=seed, dropout_p=0.0,
                         distractor_p=0.0, noise_px_sigma=sigma_px)
    cfg = obs.parse_config({**SPEC.camera_config(str(video))})
    w, d = obs.make_writer(tmp / "out", "s", f"o-{seed}-{sigma_px}", cfg, "DRY_RUN", "rendered_video")
    with w:
        obs.observe(cfg, w)
    rows = load_trial(d).stream("observation").of("marker")
    got = np.array([r["observed_value"] for r in rows])
    # the true angle at each frame is the one that was drawn: the marker position jitter is added
    # on top of it, so it is part of the error being measured
    want = np.radians(truth["true_angle_deg"]) / SPEC.angle_scale_rad
    err_deg = np.degrees((got - want) * SPEC.angle_scale_rad)
    return {"seed": seed, "marker_jitter_px": sigma_px, "frames": len(rows),
            "rms_deg": float(np.sqrt(np.mean(err_deg ** 2))),
            "rms_norm": float(np.sqrt(np.mean((got - want) ** 2))),
            "max_abs_deg": float(np.abs(err_deg).max())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "reference_results" / "observe_selftest.json"))
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--sigmas", default="0,0.3,1")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    sigmas = [float(s) for s in a.sigmas.split(",")]
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for sg in sigmas:
            for sd in seeds:
                rows.append(run_one(Path(tmp), sd, sg))
    summary = {}
    for sg in sigmas:
        sel = [r for r in rows if r["marker_jitter_px"] == sg]
        rms = [r["rms_deg"] for r in sel]
        summary[f"{sg:g}"] = {"rms_deg_min": min(rms), "rms_deg_max": max(rms),
                              "rms_deg_mean": float(np.mean(rms)),
                              "max_abs_deg_over_seeds": max(r["max_abs_deg"] for r in sel),
                              "frames": sel[0]["frames"]}
    out = {"source": "synthetic",
           "what": "rendered marker -> mp4 -> detector; angle error against the drawn angle",
           "degrees_per_pixel_note": f"marker radius {SPEC.radius_px:g} px, so 1 px of sideways "
                                     f"movement is about {math.degrees(1 / SPEC.radius_px):.2f} deg",
           "environment": environment(), "by_marker_jitter_px": summary, "runs": rows}
    p = Path(a.out)
    if p.exists():
        sys.exit(f"{p} exists; remove it to regenerate")
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for sg, s in summary.items():
        print(f"marker jitter {sg:>4s} px: RMS {s['rms_deg_min']:.3f}..{s['rms_deg_max']:.3f} deg "
              f"(mean {s['rms_deg_mean']:.3f}), worst frame {s['max_abs_deg_over_seeds']:.3f} deg, "
              f"{s['frames']} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
