#!/usr/bin/env python
"""Run the demo for several seeds and tabulate the headline numbers (median and range).

One seed is one draw of the assumed noise; quoting a single draw's third digit says more than
the data support. This script shows how far each number moves from seed to seed.

    python scripts/seed_table.py                    # seeds 1..8 -> reference_results/seed_table.json

SYNTHETIC: virtual device, synthetic servo, rendered video.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from motion_io_lab.demo import MOTION_NAMES, run_demo

ROOT = Path(__file__).resolve().parent.parent
STAGES = ("commanded", "applied", "observed")


def one(seed: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"seed{seed}-") as tmp:
        s = run_demo(Path(tmp) / "demo", seed=seed)
    row: dict = {"seed": seed, "motions": {}}
    for m in MOTION_NAMES:
        r = s["motions"][m]
        g = lambda k: r[k]["mean"]  # noqa: E731
        row["motions"][m] = {
            **{f"replay_vs_recording_{st}": g(f"rms_deg_replay_vs_recording_{st}") for st in STAGES},
            **{f"replay_vs_recording_{st}_after_500ms": g(f"rms_deg_replay_vs_recording_{st}_after_500ms")
               for st in STAGES},
            "replay_vs_replay_observed": g("rms_deg_replay_vs_replay_observed"),
            "observed_repeat_sd": r["repeat_sd_deg"]["observed_deg"]["pooled_sd"],
            "observed_repeat_sd_minus_floor": r["repeat_sd_deg"]["observed_deg"]["pooled_sd_minus_floor"],
            "lag_commanded_to_observed_ms": g("lag_ms_commanded_to_observed"),
            "lag_applied_to_observed_ms": g("lag_ms_applied_to_observed"),
            "lag_applied_to_observed_expected_ms": g("lag_ms_applied_to_observed_expected"),
            "invalid_frames": r["observation_invalid"], "frames": r["observation_frames"],
            "bridged_fraction": g("observation_bridged_fraction"),
        }
    row["noise_floor_sd_deg"] = s["observation_noise_floor"]["pooled_sd_deg"]
    row["pacing_drift_ppm"] = {k: v["drift_ppm"] for k, v in s["timing"].items()}
    row["failure_cases_all_rejected"] = {c["case"]: c["all_rejected"] for c in s["failure_cases"]
                                         if "all_rejected" in c}
    row["failure_cases_plant_moved_deg"] = {c["case"]: c["plant_moved_deg"] for c in s["failure_cases"]
                                            if "plant_moved_deg" in c}
    row["resume_max_applied_rate_deg_per_s"] = {
        p: s["resume_metrics"][p].get("max_applied_rate_deg_per_s_after_rearm")
        for p in ("continue", "restart")}
    return row


def spread(vals: list[float]) -> dict:
    return {"median": statistics.median(vals), "min": min(vals), "max": max(vals), "n": len(vals)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default="1,2,3,4,5,6,7,8")
    ap.add_argument("--out", default=str(ROOT / "reference_results" / "seed_table.json"))
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        rows = list(ex.map(one, seeds))
    summary: dict = {"motions": {}}
    for m in MOTION_NAMES:
        keys = rows[0]["motions"][m].keys()
        summary["motions"][m] = {k: spread([r["motions"][m][k] for r in rows]) for k in keys}
    summary["noise_floor_sd_deg"] = spread([r["noise_floor_sd_deg"] for r in rows])
    for k in ("deadline", "relative"):
        summary[f"pacing_drift_ppm_{k}"] = spread([r["pacing_drift_ppm"][k] for r in rows])
    out = {"source": "synthetic", "seeds": seeds, "repeats": 5,
           "note": "per-seed values and median / min / max over the seeds; the checked-in demo "
                   "reference is seed 7, one draw among these",
           "summary": summary, "per_seed": rows}
    p = Path(a.out)
    if p.exists():
        sys.exit(f"{p} exists; remove it to regenerate")
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", p)
    for m in MOTION_NAMES:
        s = summary["motions"][m]
        print(m)
        for k in ("replay_vs_recording_commanded", "replay_vs_recording_applied", "replay_vs_recording_observed",
                  "replay_vs_replay_observed", "observed_repeat_sd", "lag_commanded_to_observed_ms",
                  "lag_applied_to_observed_ms"):
            print(f"   {k:38s} median {s[k]['median']:.3f}  range {s[k]['min']:.3f}..{s[k]['max']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
