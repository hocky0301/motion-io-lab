"""Manually driven serial console for protocol v2. No auto-HELLO, no auto-ARM, no heartbeat.

You type protocol lines (HELLO, STATUS, ARM <epoch>, SET <epoch> <seq> <us> <ms>, DISARM, ESTOP,
CLEAR). ARM lines are blocked by the host unless --allow-arm is given; the firmware's own lock
(HARDWARE_VERIFIED) is separate. The deadman keeps running on the device, so a hand-typed SET
sequence will time out; that is by design. Everything is written to a command-stream log.
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable

from .devclient import CommandLog
from .link import Link, LinkDown, SerialLink
from .manifest import write_run_manifest
from .protocol import check_line, parse_line
from .triallog import StreamWriter, TrialContext

NS_MS = 1_000_000


def run_console(link: Link, lines: Iterable[str], log: CommandLog, allow_arm: bool,
                out: Callable[[str], None] = print, idle_ms: int = 30) -> int:
    """Send each line, print and log whatever comes back. Returns the number of lines sent."""
    sent = 0
    for raw in lines:
        cmd = raw.strip()
        if cmd == "QUIT":
            break
        if not cmd:
            continue
        try:
            check_line(cmd)
        except ValueError:
            out("invalid line (ASCII printable, max 95 characters)")
            continue
        if cmd.upper().startswith("ARM") and not allow_arm:   # a lower-case "arm" is no loophole
            out("ARM blocked by the host; rerun with --allow-arm only after the safety checks.")
            continue
        try:
            tx = link.write_line(cmd)
        except LinkDown as exc:
            out(f"link lost: {exc}")
            break
        sent += 1
        log.event("tx", cmd, tx)
        link.sleep_until_ns(link.now_ns() + idle_ms * NS_MS)
        try:
            for rx, line in link.read_lines():
                log.event("rx", line, rx)
                out(line)
        except LinkDown as exc:
            out(f"link lost: {exc}")
            break
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motion_io_lab console", description=__doc__)
    ap.add_argument("--port", required=True)
    ap.add_argument("--out", default="outputs/console")
    ap.add_argument("--session-id", default="console")
    ap.add_argument("--trial-id", required=True, help="new name; logs are never appended to")
    ap.add_argument("--allow-arm", action="store_true")
    a = ap.parse_args(argv)
    try:
        link = SerialLink(a.port)
    except LinkDown as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # The port is open to real hardware; whether ARM may be typed is recorded in the header.
    ctx = TrialContext(a.session_id, a.trial_id, "HARDWARE_ARMED", "hardware", link="serial",
                       armed=a.allow_arm)
    try:
        w = StreamWriter(Path(a.out) / a.trial_id / "command.jsonl", "command", ctx, units="pulse_us",
                         coordinate_frame="servo_axis_0")
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    log = CommandLog(w)
    q: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            q.put(line)
        q.put("QUIT")

    threading.Thread(target=reader, daemon=True).start()

    def gen():
        while True:
            yield q.get()

    try:
        with w:
            run_console(link, gen(), log, a.allow_arm)
            try:
                link.write_line("DISARM")
            except LinkDown:
                pass
    finally:
        link.close()
    write_run_manifest(Path(a.out) / a.trial_id, "console", vars(a), source="hardware")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
