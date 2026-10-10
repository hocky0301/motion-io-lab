"""Short, bounded single-servo pulse probe: hold one pulse for up to 3 seconds.

DRY RUN unless --execute is given. Real hardware additionally needs --port,
--i-checked-hardware and a typed confirmation, and the firmware must have been built
with HARDWARE_VERIFIED=true by a person. This is a probe of the command path, not a
power-stop test, and the log holds commands and acknowledgements, not measured position.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .devclient import DeviceRefused, DeviceTimeout
from .link import LinkDown, NullLink, SerialLink, SimWorld
from .manifest import write_run_manifest
from .motions import Motion
from .player import PlayConfig, play
from .session import NotConfirmed, ask_on_terminal
from .triallog import TrialContext
from .trialio import open_trial


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motion_io_lab probe", description=__doc__)
    ap.add_argument("--port")
    ap.add_argument("--pulse-us", type=int, default=1500)
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--execute", action="store_true", help="actually talk to a device")
    ap.add_argument("--loopback", action="store_true", help="with --execute: use the virtual device")
    ap.add_argument("--i-checked-hardware", action="store_true")
    ap.add_argument("--out", default="outputs/probe")
    ap.add_argument("--session-id", default="probe")
    ap.add_argument("--trial-id", default=None)
    a = ap.parse_args(argv)
    if not 1450 <= a.pulse_us <= 1550 or not 0 < a.seconds <= 3:
        ap.error("probe only: 1450..1550 us and 0 < duration <= 3 seconds")
    if not a.execute:
        print(json.dumps({"mode": "DRY_RUN_NO_SERIAL_OPEN", "pulse_us": a.pulse_us,
                          "seconds": a.seconds}))
        return 0
    if a.trial_id is None:
        ap.error("--trial-id is required with --execute (a new name; logs are never appended to)")
    if not a.loopback:
        if not a.port or not a.i_checked_hardware:
            ap.error("--port and --i-checked-hardware are required; this is not a safety certification")
        if not sys.stdin.isatty():
            ap.error("hardware runs need an interactive terminal for the confirmation")
    motion = Motion("probe_hold", np.array([0.0, a.seconds * 1000.0]), np.array([a.pulse_us] * 2, float),
                    "bounded_probe")
    cfg = PlayConfig(hold_ms=0, host_slew_us_per_s=None, lead_ms=50)
    try:
        link = SimWorld(seed=0) if a.loopback else SerialLink(a.port)
    except LinkDown as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    mode, lk, source = ("REPLAY", "virtual", "synthetic") if a.loopback else ("REPLAY", "serial", "hardware")
    ctx = TrialContext(a.session_id, a.trial_id, mode, source, link=lk,
                       config={"tool": "bounded_probe", "pulse_us": a.pulse_us, "seconds": a.seconds})
    try:
        with open_trial(a.out, ctx, with_input=False) as t:
            stats = play(motion, cfg, link, t,
                         confirm=None if a.loopback else ask_on_terminal(a.port))
    except (NotConfirmed, DeviceRefused, DeviceTimeout) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    except LinkDown as exc:
        print(f"error: link lost: {exc}", file=sys.stderr)
        return 2
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        link.close()
    write_run_manifest(Path(a.out) / a.trial_id, "probe", vars(a), source=source)
    print(json.dumps(stats))
    print("Probe done. The log has commands and acknowledgements, not measured position.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
