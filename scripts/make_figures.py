#!/usr/bin/env python
"""Regenerate figures/*.png from a demo run (SYNTHETIC data).

    python scripts/make_figures.py                 # runs the demo into a temp dir first
    python scripts/make_figures.py --demo outputs/demo --out figures

Figure text is English (font availability) and every figure says SYNTHETIC.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from motion_io_lab import analysis as an
from motion_io_lab import calibration
from motion_io_lab.demo import (MOTION_NAMES, RESUME_GAP_MS, RESUME_UNPLUG_MS, analyze_motion,
                                run_demo, synthetic_calibration)
from motion_io_lab.motions import MOTIONS
from motion_io_lab.triallog import load_trial

C = {"commanded": "#6b6b6b", "applied": "#1b6ca8", "observed": "#d95f02", "recording": "#111111",
     "good": "#2a9d8f", "bad": "#c1121f"}
TAG_VIDEO = "SYNTHETIC: virtual device + synthetic servo + rendered video. Not a hardware result."
TAG_DEVICE = "SYNTHETIC: virtual device + synthetic servo (no video). Not a hardware result."
TAG_PACING = "SYNTHETIC: simulated host wake-up delays only (no device, servo or video). Not a hardware result."


def stamp(fig, tag=TAG_VIDEO):
    fig.text(0.01, 0.005, tag, fontsize=7.5, color="#8a1c1c", ha="left", va="bottom")


def ppm(x):
    t = f"{x:.1f}"
    return "0.0" if t == "-0.0" else t


def style(ax):
    ax.grid(alpha=0.25, lw=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def fig1(curves, demo_dir, out):
    c = curves["look_wait_nod"]
    g = c["grid"] / 1000.0
    r = c["replays"][0]
    tr = load_trial(demo_dir / "look_wait_nod-rep01")
    ts = an.extract(tr)
    scale = ts.angle_scale_deg
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.step(g, r["commanded_deg"], where="post", color=C["commanded"], lw=1.2, label="commanded (host sent)")
    ax.plot(g, r["applied_deg"], color=C["applied"], lw=1.6, label="applied (firmware wrote to PWM chip)")
    # the frames the detector really returned, not the interpolated grid
    ax.plot(ts.observed[0] / 1000.0, ts.observed[1] * scale, ".", color=C["observed"], ms=4,
            label="observed: valid frames (marker in rendered video)")
    bad = [r_["media_pos_ms"] / 1000.0 for r_ in tr.stream("observation").of("marker") if not r_["valid"]]
    lo = ax.get_ylim()[0]
    ax.plot(bad, np.full(len(bad), lo), "|", color=C["observed"], ms=9, mew=1.6,
            label=f"observed: no valid frame ({len(bad)}; dropout / second blob)")
    ax.set_ylim(lo - 0.15, None)
    ax.set_xlabel("time since first command (s)")
    ax.set_ylabel("angle (deg)")
    ax.set_title("Three separate series of one replay: look - wait - nod\n(commanded/applied: pulse width x assumed 0.1 deg/us; observed: marker angle in the video)", fontsize=10)
    ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2)
    style(ax)
    stamp(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "fig1_three_series.png", dpi=150)
    plt.close(fig)


def fig2(curves, out):
    fig, axes = plt.subplots(2, 3, figsize=(11, 5.6), sharex="col", gridspec_kw={"height_ratios": [3, 2]})
    for j, name in enumerate(MOTION_NAMES):
        c = curves[name]
        g = c["grid"] / 1000.0
        top, bot = axes[0, j], axes[1, j]
        for k, rep in enumerate(c["replays"]):
            top.plot(g, rep["observed_deg"], lw=0.8, color=C["observed"], alpha=0.55,
                     label="replays (observed)" if k == 0 else None)
        top.plot(g, c["recording"]["observed_deg"], color=C["recording"], lw=1.4, label="recording (observed)")
        top.set_title(name)
        for key, col in (("commanded_deg", C["commanded"]), ("applied_deg", C["applied"]),
                         ("observed_deg", C["observed"])):
            bot.plot(g, c[f"sd_{key}"], color=col, lw=1.2, label=key.split("_")[0])
        bot.set_xlabel("time (s)")
        if j == 0:
            top.set_ylabel("observed angle (deg)")
            bot.set_ylabel("SD across 5 replays (deg)")
            top.legend(fontsize=7)
            bot.legend(fontsize=7)
        style(top)
        style(bot)
    fig.suptitle("Repeatability: 5 replays of one recording, per series", fontsize=11)
    stamp(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(out / "fig2_repeatability.png", dpi=150)
    plt.close(fig)


def fig3(summary, out):
    """Points, not bars: five replays per stage and motion. Also shown: the floor set by detector
    noise, and the replay-vs-replay difference (which does not involve the recording)."""
    ms = list(MOTION_NAMES)
    floor = summary["observation_noise_floor"]["pooled_sd_deg"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [3, 2]})
    w = 0.24
    rng = np.random.default_rng(0)
    for i, (key, lab, col) in enumerate((("rms_deg_replay_vs_recording_commanded", "commanded", C["commanded"]),
                                         ("rms_deg_replay_vs_recording_applied", "applied", C["applied"]),
                                         ("rms_deg_replay_vs_recording_observed", "observed", C["observed"]))):
        for j, m in enumerate(ms):
            vals = summary["motions"][m][key]["values"]
            x = j + (i - 1) * w
            a.plot(x + rng.uniform(-0.04, 0.04, len(vals)), vals, "o", color=col, ms=5, alpha=0.8,
                   label=lab if j == 0 else None)
            a.hlines(np.mean(vals), x - 0.09, x + 0.09, color=col, lw=2)
    for j, m in enumerate(ms):
        v = summary["motions"][m]["rms_deg_replay_vs_replay_observed"]["mean"]
        a.plot(j + 1.5 * w + 0.12, v, "D", color="white", mec=C["observed"], ms=6,
               label="observed, replay vs replay (no recording)" if j == 0 else None)
    a.axhline(np.sqrt(2) * floor, color=C["bad"], lw=1, ls="--",
              label=f"detector noise floor: two readings of a still marker differ by {np.sqrt(2) * floor:.3f}")
    a.set_xticks(range(3), ms)
    a.set_ylim(0, None)
    a.set_ylabel("RMS difference (deg)")
    a.set_title("Replay vs recording at three stages (5 replays: dots, bar = mean)\n"
                "commanded: depends on the 10 ms analysis grid; observed: at the noise floor",
                fontsize=9.5)
    a.legend(title="series", fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2)
    style(a)
    for i, (key, lab, col) in enumerate((("lag_ms_commanded_to_observed", "commanded -> observed", C["commanded"]),
                                         ("lag_ms_applied_to_observed", "applied -> observed", C["applied"]))):
        for j, m in enumerate(ms):
            vals = summary["motions"][m][key]["values"]
            x = j + (i - 0.5) * 0.3
            b.plot(x + rng.uniform(-0.04, 0.04, len(vals)), vals, "o", color=col, ms=5, alpha=0.8,
                   label=lab if j == 0 else None)
            exp = summary["motions"][m][key + "_expected"]["mean"]
            b.plot(x, exp, "x", color="black", ms=8, mew=1.6,
                   label="expected from the noiseless servo + 70 ms camera" if (i == 0 and j == 0) else None)
    b.set_xticks(range(3), ms, fontsize=8)
    b.set_ylabel("estimated total lag (ms)")
    b.set_title("Cross-correlation lag (total delay, not camera latency)", fontsize=9.5)
    b.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=1)
    style(b)
    stamp(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "fig3_error_ladder.png", dpi=150)
    plt.close(fig)


def fig4(demo_dir, cal, curves, out):
    g = curves["sweep"]["grid"]
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    ax.axvspan(RESUME_UNPLUG_MS / 1000, (RESUME_UNPLUG_MS + RESUME_GAP_MS) / 1000, color="#dddddd",
               label="cable out (600 ms)")
    nominal = curves["sweep"]["mean_applied_deg"]
    ax.plot(g / 1000, nominal, color=C["recording"], lw=1.2, ls="--", label="uninterrupted (mean of 5 replays)")
    for policy, col in (("restart", C["observed"]), ("continue", C["good"]), ("none", C["bad"])):
        ts = an.extract(load_trial(demo_dir / f"sweep-resume-{policy}"))
        ax.step(ts.applied[0] / 1000, cal.pulse_to_deg(ts.applied[1]), where="post", color=col,
                lw=2.4 if policy == "none" else 1.5, zorder=5 if policy == "none" else 3,
                label={"none": "no resume: stops, device latched", "continue": "resume: continue (skip ahead)",
                       "restart": "resume: restart from the beginning"}[policy])
    ax.set_xlabel("time since first command (s)")
    ax.set_ylabel("firmware-applied angle (deg)")
    ax.set_title("Cable out mid-motion (flat segments = last report held; no reports arrive while the cable is out)", fontsize=9.5)
    ax.legend(fontsize=7.5, loc="lower right")
    style(ax)
    stamp(fig, TAG_DEVICE)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "fig4_disconnect_resume.png", dpi=150)
    plt.close(fig)


def fig5(demo_dir, summary, out):
    z = np.load(demo_dir / "timing_series.npz")
    fig, ax = plt.subplots(figsize=(8, 4))
    for k, col in (("relative", C["bad"]), ("deadline", C["good"])):
        t = summary["timing"][k]
        ax.plot(z[f"{k}_t"], z[f"{k}_late"], lw=0.9, color=col,
                label=f"{k} pacing: drift {ppm(t['drift_ppm'])} ppm, p95 {t['lateness_ms_p95']:.2f} ms")
    ax.set_yscale("symlog", linthresh=1.0)
    ax.set_xlabel("schedule time (s)")
    ax.set_ylabel("lateness of each send vs its ideal deadline (ms)")
    ax.set_title("Deadline pacing vs sleep-after-send pacing (same wake-up model)")
    ax.legend(fontsize=8)
    style(ax)
    stamp(fig, TAG_PACING)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "fig5_pacing.png", dpi=150)
    plt.close(fig)


def fig6(demo_dir, cal, out):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, (label, title) in zip(axes, (("host_slew_400", "host slew limit 400 us/s"),
                                         ("device_slew_only", "no host limit: the device's 500 us/s limit"))):
        ts = an.extract(load_trial(demo_dir / f"hardstep-{label}"))
        ax.step(ts.commanded[0] / 1000, ts.commanded[1], where="post", color=C["commanded"], label="commanded")
        ax.step(ts.applied[0] / 1000, ts.applied[1], where="post", color=C["applied"], lw=1.6, label="applied")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("time (s)")
        ax.set_xlim(0, 1.5)
        style(ax)
    axes[0].set_ylabel("pulse width (us)")
    axes[0].legend(fontsize=8)
    fig.suptitle("A 50 us step: what was asked for vs what the firmware wrote", fontsize=11)
    stamp(fig, TAG_DEVICE)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(out / "fig6_hard_step.png", dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", default=None, help="existing demo output directory")
    ap.add_argument("--out", default="figures")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = None
    if a.demo is None:
        tmp = tempfile.TemporaryDirectory()
        demo_dir = Path(tmp.name) / "demo"
        run_demo(demo_dir)
    else:
        demo_dir = Path(a.demo)
    summary = json.loads((demo_dir / "summary.json").read_text())
    cal = calibration.load(demo_dir / "calibration_synthetic.json")
    curves = {m: analyze_motion(demo_dir, m, summary["repeats"], cal)[1] for m in MOTION_NAMES}
    fig1(curves, demo_dir, out)
    fig2(curves, out)
    fig3(summary, out)
    fig4(demo_dir, cal, curves, out)
    fig5(demo_dir, summary, out)
    fig6(demo_dir, cal, out)
    shown = {
        "source": "synthetic", "demo_seed": summary["seed"],
        "fig3_replay_vs_recording_rms_deg": {m: {k: summary["motions"][m][f"rms_deg_replay_vs_recording_{k}"]["values"]
                                                 for k in ("commanded", "applied", "observed")} for m in MOTION_NAMES},
        "fig3_replay_vs_replay_observed_rms_deg_mean": {m: summary["motions"][m]["rms_deg_replay_vs_replay_observed"]["mean"]
                                                        for m in MOTION_NAMES},
        "fig3_detector_noise_floor_sd_deg": summary["observation_noise_floor"]["pooled_sd_deg"],
        "fig5_drift_ppm": {k: summary["timing"][k]["drift_ppm"] for k in summary["timing"]},
    }
    (out / "figures_numbers.json").write_text(json.dumps(shown, indent=2) + "\n")
    print("wrote", ", ".join(sorted(p.name for p in out.glob("*.png"))))
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
