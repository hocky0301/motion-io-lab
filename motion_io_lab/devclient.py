"""Host-side client for protocol v2 plus the command-stream logger.

The client never arms by itself and never retries an ARM. A REJECTED reply is
reported to the caller as it is; nothing here silently re-HELLOs or re-ARMs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .link import Link, LinkDown
from .protocol import Reply, fmt_arm, fmt_heartbeat, fmt_set, parse_line
from .triallog import StreamWriter

NS_MS = 1_000_000
INBOX_MAX, INBOX_KEEP = 20_000, 2_000


class DeviceRefused(RuntimeError):
    def __init__(self, what: str, reply: Reply | None):
        self.reply = reply
        super().__init__(f"{what}: {reply.raw if reply else 'no reply'}")


class DeviceTimeout(RuntimeError):
    pass


@dataclass
class Hello:
    epoch: int
    firmware_version: str
    board: str
    proto: int
    min_us: int
    max_us: int
    center_us: int
    step_us: int
    tick_ms: int
    grace_ms: int
    max_valid_ms: int
    hw_verified: bool
    fault: bool
    raw: str = ""


class CommandLog:
    """Thin layer over a command-stream writer."""

    def __init__(self, writer: StreamWriter):
        self.w = writer
        self.n_sets = 0

    def set_epoch(self, epoch: int | None) -> None:
        self.w.ctx.session_epoch = epoch

    def event(self, event: str, detail: str | None = None, t_ns: int | None = None) -> None:
        self.w.write("event", event=event, detail=detail, received_monotonic_ns=t_ns,
                     processed_monotonic_ns=t_ns)

    def set(self, *, seq: int, desired_pulse_us: float | None, commanded_pulse_us: int | None,
            valid_for_ms: int, scheduled_ns: int | None, sent_ns: int | None, shaping: str,
            valid: bool = True, drop_reason: str | None = None,
            planned_index: int | None = None) -> None:
        """``seq`` is the device sequence number; a row that was never sent has ``seq=None``
        (so no sequence number appears twice). ``planned_index`` is the sample's place in the plan."""
        self.n_sets += 1
        self.w.write("set", seq=seq, planned_index=planned_index, desired_pulse_us=desired_pulse_us,
                     commanded_pulse_us=commanded_pulse_us, valid_for_ms=valid_for_ms,
                     scheduled_monotonic_ns=scheduled_ns, sent_monotonic_ns=sent_ns,
                     processed_monotonic_ns=sent_ns, shaping=shaping, valid=valid,
                     drop_reason=drop_reason)

    def reply(self, rx_ns: int, rep: Reply, cmd_label: str) -> None:
        f = rep.fields
        self.w.write("reply", reply_status=rep.kind, reply_reason=rep.reason,
                     reply_to_seq=rep.seq, reply_cmd=rep.cmd or cmd_label,
                     device_millis=f.get("mcu_ms"), device_applied_pulse_us=f.get("applied_us"),
                     dup=bool(f.get("dup")), raw=rep.raw, received_monotonic_ns=rx_ns,
                     processed_monotonic_ns=rx_ns)

    def status(self, rx_ns: int, tx_ns: int | None, rep: Reply) -> None:
        f = rep.fields
        self.w.write("status", device_commanded_pulse_us=f.get("commanded_us"),
                     device_applied_pulse_us=f.get("applied_us"), device_millis=f.get("mcu_ms"),
                     armed=bool(f.get("armed")), fault=bool(f.get("fault")),
                     last_reject=f.get("last_reject"), last_stop=f.get("last_stop"),
                     device_epoch=f.get("epoch"), raw=rep.raw, tx_monotonic_ns=tx_ns,
                     received_monotonic_ns=rx_ns, processed_monotonic_ns=rx_ns)


@dataclass
class ClientStats:
    sent: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: dict = field(default_factory=dict)
    executed: int = 0
    stops: list = field(default_factory=list)


class DeviceClient:
    def __init__(self, link: Link, log: CommandLog | None = None, reply_timeout_ms: int = 300):
        self.link = link
        self.log = log
        self.timeout_ns = reply_timeout_ms * NS_MS
        self.epoch: int | None = None
        self.hello_info: Hello | None = None
        self.seq = 0
        self.stats = ClientStats()
        self.inbox: list[tuple[int, Reply]] = []
        self._inbox_dropped = 0              # entries trimmed from the front of ``inbox``
        self.executed: dict[int, tuple[int, int | None, int]] = {}   # seq -> (applied_us, mcu_ms, rx_ns)
        self.last_status: Reply | None = None
        self._status_tx: int | None = None
        self.stopped: str | None = None
        self.rejections: list[tuple[int, Reply]] = []
        self.sent_lines: list[str] = []      # last 256 lines handed to the link (for tests/demo)

    # -- receiving ---------------------------------------------------------
    def pump(self) -> list[tuple[int, Reply]]:
        """Read whatever has arrived, log it, return it."""
        got = []
        for rx, line in self.link.read_lines():
            rep = parse_line(line)
            got.append((rx, rep))
            self.inbox.append((rx, rep))
            self._route(rx, rep)
        if len(self.inbox) > INBOX_MAX:      # a very long recording must not grow without bound
            drop = len(self.inbox) - INBOX_KEEP
            del self.inbox[:drop]
            self._inbox_dropped += drop
        return got

    def _route(self, rx: int, rep: Reply) -> None:
        st = self.stats
        if rep.kind == "STATUS":
            self.last_status = rep
            if self.log:
                self.log.status(rx, self._status_tx, rep)
            return
        if rep.kind == "STOP":
            st.stops.append(rep.reason)
            self.stopped = rep.reason
            if self.log:
                self.log.event("device_stop", rep.reason, rx)
            return
        if rep.kind in ("HELLO", "BOOT", "OTHER"):
            if self.log:
                self.log.event(rep.kind.lower(), rep.raw, rx)
            return
        if rep.kind == "ACCEPTED_NOT_EXECUTED" and rep.cmd == "SET":
            if rep.fields.get("dup"):
                st.duplicates += 1
            else:
                st.accepted += 1
        elif rep.kind == "REJECTED":
            self.rejections.append((rx, rep))
            key = f"{rep.cmd}:{rep.reason}"
            st.rejected[key] = st.rejected.get(key, 0) + 1
        elif rep.kind == "EXECUTED" and rep.cmd == "SET" and rep.seq is not None:
            st.executed += 1
            ap = rep.fields.get("applied_us")
            self.executed[rep.seq] = (ap if isinstance(ap, int) else -1,
                                      rep.fields.get("mcu_ms") if isinstance(rep.fields.get("mcu_ms"), int) else None, rx)
        if self.log:
            self.log.reply(rx, rep, rep.cmd or "?")

    def _await(self, match, what: str) -> Reply:
        deadline = self.link.now_ns() + self.timeout_ns
        seen = self._inbox_dropped + len(self.inbox)   # only replies after the request count
        while True:
            self.pump()
            for rx, rep in self.inbox[max(0, seen - self._inbox_dropped):]:
                if match(rep):
                    return rep
            if self.link.now_ns() >= deadline:
                raise DeviceTimeout(f"no reply to {what}")
            self.link.sleep_until_ns(self.link.now_ns() + NS_MS // 2)

    # -- commands ----------------------------------------------------------
    def hello(self) -> Hello:
        self.link.write_line("HELLO")
        rep = self._await(lambda r: r.kind == "HELLO", "HELLO")
        f = rep.fields
        if f.get("proto") != 2:
            raise DeviceRefused("unsupported protocol", rep)
        self.epoch = int(f["epoch"])
        self.seq = 0
        self.stopped = None
        self.hello_info = Hello(
            epoch=self.epoch, firmware_version=str(f["fw"]), board=str(f["board"]),
            proto=int(f["proto"]), min_us=int(f["min_us"]), max_us=int(f["max_us"]),
            center_us=int(f["center_us"]), step_us=int(f["step_us"]), tick_ms=int(f["tick_ms"]),
            grace_ms=int(f["grace_ms"]), max_valid_ms=int(f["max_valid_ms"]),
            hw_verified=bool(f["hw_verified"]), fault=bool(f["fault"]), raw=rep.raw)
        if self.log:
            self.log.set_epoch(self.epoch)
            self.log.event("hello_ok", f"epoch={self.epoch} fw={f['fw']}", self.link.now_ns())
        return self.hello_info

    def arm(self) -> None:
        if self.epoch is None:
            raise RuntimeError("HELLO first")
        line = fmt_arm(self.epoch)
        self.link.write_line(line)
        self.sent_lines = (self.sent_lines + [line])[-256:]
        rep = self._await(lambda r: r.cmd == "ARM" and r.kind in ("REJECTED", "ACCEPTED_NOT_EXECUTED"), "ARM")
        if rep.kind == "REJECTED":
            raise DeviceRefused("ARM refused", rep)

    def clear(self) -> None:
        self.link.write_line("CLEAR")
        rep = self._await(lambda r: r.cmd == "CLEAR", "CLEAR")
        if rep.kind == "REJECTED":
            raise DeviceRefused("CLEAR refused", rep)

    def status(self) -> Reply:
        self._status_tx = self.link.write_line("STATUS")
        return self._await(lambda r: r.kind == "STATUS", "STATUS")

    def heartbeat(self) -> None:
        if self.epoch is None:
            raise RuntimeError("HELLO first")
        self.link.write_line(fmt_heartbeat(self.epoch))

    def disarm(self) -> None:
        self.link.write_line("DISARM")
        try:
            self._await(lambda r: r.kind == "STOP", "DISARM")
        except DeviceTimeout:
            pass
        self.epoch = None

    def estop(self) -> None:
        self.link.write_line("ESTOP")

    def send_set(self, pulse_us: int, valid_for_ms: int, *, desired: float | None = None,
                 scheduled_ns: int | None = None, shaping: str = "none",
                 planned_index: int | None = None) -> int:
        """Send one SET without waiting for the reply. Returns the sequence number used."""
        if self.epoch is None:
            raise RuntimeError("HELLO first")
        self.seq += 1
        seq = self.seq
        try:
            line = fmt_set(self.epoch, seq, pulse_us, valid_for_ms)
            sent = self.link.write_line(line)
            self.sent_lines = (self.sent_lines + [line])[-256:]
        except LinkDown:
            if self.log:
                self.log.set(seq=seq, desired_pulse_us=desired, commanded_pulse_us=pulse_us,
                             valid_for_ms=valid_for_ms, scheduled_ns=scheduled_ns, sent_ns=None,
                             shaping=shaping, valid=False, drop_reason="link_down",
                             planned_index=planned_index)
            raise
        self.stats.sent += 1
        if self.log:
            self.log.set(seq=seq, desired_pulse_us=desired, commanded_pulse_us=pulse_us,
                         valid_for_ms=valid_for_ms, scheduled_ns=scheduled_ns, sent_ns=sent,
                         shaping=shaping, planned_index=planned_index)
        return seq
