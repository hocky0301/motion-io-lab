"""Recorder: person's input -> clamp/slew -> command stream (and, unless dry, the device).

The three things stay separate on disk: ``input.jsonl`` holds what the person did,
``command.jsonl`` holds what was sent (``desired_pulse_us`` before shaping,
``commanded_pulse_us`` after) and what the device answered.
"""
from __future__ import annotations

from dataclasses import dataclass

from .devclient import DeviceClient, LinkDown
from .inputs import InputSource
from .link import Link
from .session import start_device
from .shaping import Shaper, x_to_pulse
from .timing import timing_stats
from .trialio import OpenTrial

NS_MS = 1_000_000


@dataclass
class RecordConfig:
    period_ms: int = 40               # command rate 25 Hz
    valid_for_ms: int = 120           # lease carried by every SET
    span_us: float = 40.0             # x = +-1 maps to centre +- span (device allows +-50)
    host_min_us: int = 1450
    host_max_us: int = 1550
    host_slew_us_per_s: float | None = 400.0
    max_seconds: float = 30.0
    lead_ms: int = 50


def record(source: InputSource, cfg: RecordConfig, link: Link, trial: OpenTrial, *,
           confirm=None) -> dict:
    ctx = trial.ctx
    client = DeviceClient(link, trial.command) if link.link_type != "none" else None
    shaper = Shaper(cfg.host_min_us, cfg.host_max_us, cfg.host_slew_us_per_s)
    period_ns = cfg.period_ms * NS_MS
    sched, actual = [], []
    n_events = 0
    last_x: float | None = None
    seq_local = 0
    interrupted = None
    stopped_by = "source_done"          # or "max_seconds", "ctrl_c", "interrupted"
    try:
        if client:
            start_device(client, link.link_type, confirm)
        t0 = link.now_ns() + cfg.lead_ms * NS_MS
        source.start(t0)
        trial.command.event("record_start", f"period_ms={cfg.period_ms}", link.now_ns())
        k = 0
        try:
            while True:
                when = t0 + k * period_ns
                link.sleep_until_ns(when)
                now = link.now_ns()
                events = source.poll(now)
                batch = [(ev.rx_ns, 0, ev) for ev in events]
                batch += [(rec["received_monotonic_ns"], 1, rec) for rec in source.sync_records()]
                for _, kind, item in sorted(batch, key=lambda b: (b[0], b[1])):
                    if kind == 1:
                        if trial.input:
                            trial.input.write("sync", **item, processed_monotonic_ns=now)
                        continue
                    ev = item
                    n_events += 1
                    if trial.input:
                        ok = ev.value is not None
                        trial.input.write(
                            "input_event", event_id=ev.event_id, input_kind=ev.kind,
                            input_value=ev.value, client_time_ms=ev.client_time_ms,
                            client_clock_domain=source.client_clock_domain,
                            received_monotonic_ns=ev.rx_ns, processed_monotonic_ns=now, valid=ok)
                    if ev.value is not None:
                        last_x = ev.value
                if source.done(now) or (now - t0) / 1e9 > cfg.max_seconds:
                    stopped_by = "source_done" if source.done(now) else "max_seconds"
                    break
                x = last_x if last_x is not None else 0.0
                desired = x_to_pulse(x, cfg.span_us)
                pulse, flags = shaper.shape(desired, cfg.period_ms / 1000.0)
                seq_local += 1
                if client:
                    try:
                        client.send_set(pulse, cfg.valid_for_ms, desired=desired, scheduled_ns=when,
                                        shaping=flags, planned_index=k)
                    except LinkDown:
                        interrupted = "link_down"
                        break
                    try:
                        client.pump()
                    except LinkDown:
                        interrupted = "link_down"
                        break
                    if client.stopped:
                        interrupted = f"device_stop:{client.stopped}"
                        break
                else:
                    trial.command.set(seq=seq_local, planned_index=k, desired_pulse_us=desired,
                                      commanded_pulse_us=pulse,
                                      valid_for_ms=cfg.valid_for_ms, scheduled_ns=when, sent_ns=None,
                                      shaping=flags)
                sched.append(when)
                actual.append(now)
                k += 1
        except KeyboardInterrupt:
            stopped_by = "ctrl_c"        # the person's way to end touch / Joy-Con recordings
    finally:
        source.close()
        if client and interrupted is None:
            try:
                link.sleep_until_ns(link.now_ns() + 3 * NS_MS)
                client.pump()
                client.disarm()
                client.pump()
            except LinkDown:
                pass
    if client:
        try:
            client.pump()
        except LinkDown:
            pass
    trial.command.event("record_end", interrupted or ("ok" if stopped_by != "ctrl_c" else "ctrl_c"),
                        link.now_ns())
    if interrupted:
        stopped_by = "interrupted"
    return {"commands": seq_local, "input_events": n_events, "interrupted": interrupted,
            "stopped_by": stopped_by,
            "timing": timing_stats(sched, actual),
            "device": None if not client else {
                "accepted": client.stats.accepted, "duplicates": client.stats.duplicates,
                "rejected": client.stats.rejected, "executed": client.stats.executed,
                "stops": client.stats.stops}}
