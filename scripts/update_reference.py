#!/usr/bin/env python
"""Regenerate reference_results/ from a fresh demo run. Only code output goes in there.

    python scripts/update_reference.py

Review the diff before committing: a changed number means the behaviour changed.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from motion_io_lab.demo import run_demo
from motion_io_lab.manifest import environment

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "reference_results"


def main() -> int:
    REF.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "demo"
        summary = run_demo(out)
        shutil.copyfile(out / "summary.json", REF / "demo_summary.json")
        shutil.copyfile(out / "report.md", REF / "demo_report.md")
        shutil.copyfile(out / "calibration_synthetic.json", REF / "calibration_synthetic.json")
    env = {"source": "synthetic",
           "what": "output of `python -m motion_io_lab demo --out ...` (seed 7, 5 repeats)",
           "environment": environment(),
           "note": "Everything in this folder was produced by code on a virtual device, a synthetic "
                   "servo and a rendered video. None of it is a hardware measurement."}
    (REF / "environment.json").write_text(json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("updated", REF, "seed", summary["seed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
