"""Player: send a stored motion with host-monotonic deadlines, and say how well it kept them.

Pacing is by deadline (start + i * period), not by sleeping one period after each send,
so a late wake-up does not push every later command later. ``pacing="relative"`` exists
only to show that difference.

Nothing resumes by itself. After an interruption (cable out, device stop, rejection that
means "you are no longer in a session") the run ends as ``interrupted``; ``resume`` has to
be asked for, and then the sequence is always HELLO (new epoch) -> optional CLEAR (only if
``allow_clear``) -> ARM -> rejoin from the device's reported position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .devclient import DeviceClient, DeviceRefused, LinkDown
from .link import Link
from .motions import Motion
from .session import NotConfirmed, start_device
from .shaping import Shaper
from .timing import timing_stats
from .trialio import OpenTrial

NS_MS = 1_000_000
FATAL_REJECTS = ("epoch", "disarmed", "lease_expired", "fault")


@dataclass
class PlayConfig:
    period_ms: int = 40
    valid_for_ms: int = 120
    speed: float = 1.0
    start_ms: float = 0.0
    end_ms: float | None = None
    host_min_us: int = 1450
    host_max_us: int = 1550
    host_slew_us_per_s: float | None = 400.0
    lead_ms: int = 50
    drop_if_late_ms: int = 60         # a command later than this is dropped, not sent stale
    hold_ms: int = 400                # keep sending the last value so the device can settle
    pacing: str = "deadline"          # "deadline" | "relative" (comparison only)
    resume: str | None = None         # None | "restart" | "continue"
    allow_clear: bool = False         # CLEAR a latched fault during resume (human decision)
    max_speed: float = 1.0            # refuse to play faster than the recording by default
    max_duration_ms: float | None = 120_000.0   # refuse longer plans (None = no limit, --allow-long)

    def __post_init__(self) -> None:
        if self.pacing not in ("deadline", "relative"):
            raise ValueError("pacing must be 'deadline' or 'relative'")
        if self.resume not in (None, "restart", "continue"):
            raise ValueError("resume must be None, 'restart' or 'continue'")
        if not 0 < self.speed <= self.max_speed:
            raise ValueError(f"speed must be in (0, {self.max_speed}]; faster-than-recorded "
                             "playback is refused unless max_speed is raised on purpose")
        if self.valid_for_ms < 20 or self.valid_for_ms > 500:
            raise ValueError("valid_for_ms must be 20..500 (device limits)")
        if self.period_ms < 5:
            raise ValueError("period_ms must be >= 5")


@dataclass
class Outcome:
    status: str                       # "completed" | "interrupted"
    index: int                        # next sample index to send
    reason: str | None = None


@dataclass
class Plan:
    t_ms: np.ndarray                  # relative to the first sample
    desired: np.ndarray
    pulse: np.ndarray
    flags: list[str]
    hold_from: int                    # index where the hold samples start


def build_plan(motion: Motion, cfg: PlayConfig) -> Plan:
    t, u = motion.resample(cfg.period_ms, cfg.start_ms, cfg.end_ms, cfg.speed)
    if cfg.max_duration_ms is not None and t[-1] > cfg.max_duration_ms:
        raise ValueError(f"the plan is {t[-1] / 1000:.0f} s long (limit {cfg.max_duration_ms / 1000:.0f} s). "
                         "If t_ms is in the wrong unit (microseconds?) fix the file; if the length is "
                         "intended, pass --allow-long")
    n_hold = int(round(cfg.hold_ms / cfg.period_ms))
    t_all = np.concatenate([t, t[-1] + cfg.period_ms * np.arange(1, n_hold + 1)])
    d_all = np.concatenate([u, np.full(n_hold, u[-1])])
    plan = Plan(t_all, d_all, np.zeros(len(t_all)), [""] * len(t_all), len(t))
    reshape(plan, cfg, 0, None)
    return plan


def reshape(plan: Plan, cfg: PlayConfig, from_index: int, start_us: float | None) -> None:
    """(Re)compute the shaped pulses from ``from_index`` on, starting at ``start_us``."""
    sh = Shaper(cfg.host_min_us, cfg.host_max_us, cfg.host_slew_us_per_s)
    sh.reset(start_us)
    for i in range(from_index, len(plan.t_ms)):
        p, f = sh.shape(float(plan.desired[i]), cfg.period_ms / 1000.0)
        plan.pulse[i] = p
        plan.flags[i] = "hold" if i >= plan.hold_from and f == "none" else f


class Player:
    def __init__(self, plan: Plan, cfg: PlayConfig, link: Link, trial: OpenTrial,
                 client: DeviceClient | None,
                 before_send: Callable[[int, int], None] | None = None):
        self.plan, self.cfg, self.link, self.trial, self.client = plan, cfg, link, trial, client
        self.before_send = before_send
        self.sched: list[int] = []
        self.actual: list[int] = []
        self.dropped_late = 0
        self.sent_seq: list[int] = []
        self._rej_seen = 0

    def run(self, start_index: int, origin_ns: int | None = None) -> Outcome:
        cfg, plan, link, client, log = self.cfg, self.plan, self.link, self.client, self.trial.command
        period_ns = cfg.period_ms * NS_MS
        if origin_ns is None:
            origin_ns = link.now_ns() + cfg.lead_ms * NS_MS
        t_base = plan.t_ms[start_index]
        next_wake = origin_ns
        for i in range(start_index, len(plan.t_ms)):
            ideal = origin_ns + int((plan.t_ms[i] - t_base) * 1e6)   # where this sample belongs
            when = ideal if cfg.pacing == "deadline" else next_wake
            link.sleep_until_ns(when)
            when = ideal                                           # lateness is always vs the ideal
            if self.before_send:
                self.before_send(i, when)
            if client:
                try:
                    client.pump()
                except LinkDown:                                   # read side of a pulled cable
                    return Outcome("interrupted", i, "link_down")
                if client.stopped:
                    return Outcome("interrupted", i, f"device_stop:{client.stopped}")
                fatal = self._new_fatal_rejection()
                if fatal:
                    return Outcome("interrupted", i, f"rejected:{fatal}")
            pulse, flag = int(plan.pulse[i]), plan.flags[i]
            link_down_after_send = False
            now = link.now_ns()                                    # the moment of the send decision
            if now - when > cfg.drop_if_late_ms * NS_MS:
                self.dropped_late += 1
                log.set(seq=None, planned_index=i,                 # never sent: no sequence number
                        desired_pulse_us=float(plan.desired[i]), commanded_pulse_us=pulse,
                        valid_for_ms=cfg.valid_for_ms, scheduled_ns=when, sent_ns=None,
                        shaping=flag, valid=False, drop_reason="late")
                next_wake = now + period_ns
                continue
            if client:
                try:
                    seq = client.send_set(pulse, cfg.valid_for_ms, desired=float(plan.desired[i]),
                                          scheduled_ns=when, shaping=flag, planned_index=i)
                except LinkDown:
                    return Outcome("interrupted", i, "link_down")
                self.sent_seq.append(seq)
                try:
                    client.pump()
                except LinkDown:
                    link_down_after_send = True
            else:
                log.set(seq=len(self.sched) + 1, planned_index=i,
                        desired_pulse_us=float(plan.desired[i]),
                        commanded_pulse_us=pulse, valid_for_ms=cfg.valid_for_ms,
                        scheduled_ns=when, sent_ns=None, shaping=flag)
            self.sched.append(when)
            self.actual.append(now)
            next_wake = link.now_ns() + period_ns
            if link_down_after_send:
                return Outcome("interrupted", i + 1, "link_down")
        return Outcome("completed", len(plan.t_ms))

    def _new_fatal_rejection(self) -> str | None:
        assert self.client is not None
        rej = self.client.rejections
        new, self._rej_seen = rej[self._rej_seen:], len(rej)
        for _, rep in new:
            if rep.cmd == "SET" and rep.reason in FATAL_REJECTS:
                return rep.reason
        return None


def play(motion: Motion, cfg: PlayConfig, link: Link, trial: OpenTrial, *,
         confirm=None, reconnect: Callable[[], Link] | None = None,
         before_send: Callable[[int, int], None] | None = None,
         after_resume_hello: Callable[[DeviceClient], None] | None = None) -> dict:
    """Play ``motion`` through ``link`` (a NullLink for DRY_RUN). Returns a stats dict."""
    plan = build_plan(motion, cfg)
    client = DeviceClient(link, trial.command) if link.link_type != "none" else None
    player = Player(plan, cfg, link, trial, client, before_send)
    log = trial.command

    def ev(name: str, detail: str | None) -> None:
        """Pump first so earlier replies are logged before this event: the stream stays in order."""
        if client:
            try:
                client.pump()
            except LinkDown:
                pass
        log.event(name, detail, link.now_ns())

    interruptions: list[dict] = []
    resumed = 0
    status = "completed"
    final_applied = None
    aborted = False
    try:
        if client:
            start_device(client, link.link_type, confirm)
            st = client.status()
            reshape(plan, cfg, 0, float(st.fields.get("applied_us", 1500)))
        ev("play_start", f"samples={len(plan.t_ms)} period_ms={cfg.period_ms} "
                         f"speed={cfg.speed} pacing={cfg.pacing}")
        origin = link.now_ns() + cfg.lead_ms * NS_MS
        out = player.run(0, origin)
        while out.status == "interrupted":
            interruptions.append({"index": out.index, "reason": out.reason,
                                  "t_ms": float(plan.t_ms[min(out.index, len(plan.t_ms) - 1)])})
            ev("interrupted", out.reason)
            if not (cfg.resume and reconnect and client) or resumed >= 2:
                status = "interrupted"
                break
            resumed += 1
            link = reconnect()
            client.link = link
            player.link = link
            client.inbox.clear()
            client.stopped = None
            ev("resume_begin", cfg.resume)
            info = client.hello()                     # new epoch; the old one is dead
            if after_resume_hello:
                after_resume_hello(client)
            if info.fault:
                if not cfg.allow_clear:
                    raise DeviceRefused("device is latched; resume needs allow_clear (a human "
                                        "decision) and was not given", None)
                client.clear()
                ev("clear", "allow_clear")
            if link.link_type == "serial" and not (confirm and confirm(info)):
                raise NotConfirmed("resuming on real hardware needs the typed confirmation again")
            client.arm()
            player._rej_seen = len(client.rejections)   # the handshake's own rejections are not faults
            st = client.status()
            applied = float(st.fields.get("applied_us", 1500))
            if cfg.resume == "restart":
                idx, origin = 0, link.now_ns() + cfg.lead_ms * NS_MS
            else:  # continue: drop the part of the motion that has already gone by
                elapsed_ms = (link.now_ns() + cfg.lead_ms * NS_MS - origin) / 1e6
                idx = int(np.searchsorted(plan.t_ms, elapsed_ms))
                idx = min(idx, len(plan.t_ms) - 1)
                origin = origin + int(plan.t_ms[idx] * 1e6)
            reshape(plan, cfg, idx, applied)          # smooth rejoin from where the device is
            out = player.run(idx, origin)
        if client and status == "completed":
            link.sleep_until_ns(link.now_ns() + 5 * NS_MS)
            try:
                client.pump()
                final_applied = client.status().fields.get("applied_us")
            except LinkDown:
                ev("final_status_unavailable", "link_down")
    except (NotConfirmed, DeviceRefused):
        aborted = True
        trial.abort()           # an unarmed or refused run publishes nothing
        raise
    finally:
        if client:
            try:
                if aborted:
                    link.write_line("DISARM")      # best effort; the log is already abandoned
                else:
                    client.pump()
                    client.disarm()
                    client.pump()
            except (LinkDown, ValueError):
                pass
    ev("play_end", status)
    return {
        "status": status, "samples_planned": int(len(plan.t_ms)), "sent": len(player.sched),
        "dropped_late": player.dropped_late, "resumed": resumed, "interruptions": interruptions,
        "timing": timing_stats(player.sched, player.actual), "pacing": cfg.pacing,
        "final_applied_us": final_applied,
        "device": None if not client else {
            "accepted": client.stats.accepted, "duplicates": client.stats.duplicates,
            "rejected": client.stats.rejected, "executed": client.stats.executed,
            "stops": client.stats.stops},
    }
