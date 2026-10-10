"""Protocol v2 (host side): command formatting and reply parsing.

See code/firmware/guarded_servo/guarded_servo_core.h for the device side.
A reply is one of REJECTED / ACCEPTED_NOT_EXECUTED / EXECUTED. EXECUTED means "the
pulse was written to the PWM chip"; it is never a measured position.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROTO_VERSION = 2
MAX_LINE = 95  # firmware buffer is 96 including the terminator
KINDS = ("HELLO", "STATUS", "REJECTED", "ACCEPTED_NOT_EXECUTED", "EXECUTED", "STOP", "BOOT")


@dataclass
class Reply:
    kind: str                       # one of KINDS or "OTHER"
    cmd: str | None = None          # SET / ARM / HEARTBEAT / CLEAR / LINE / UNKNOWN
    fields: dict[str, int | str] = field(default_factory=dict)
    reason: str | None = None       # REJECTED reason, or the STOP reason
    raw: str = ""

    @property
    def seq(self) -> int | None:
        v = self.fields.get("seq")
        return v if isinstance(v, int) else None


def _val(s: str) -> int | str:
    return int(s) if (s.isascii() and s.isdigit()) else s


def parse_line(text: str) -> Reply:
    text = text.strip()
    toks = text.split(" ")
    if not toks or not toks[0]:
        return Reply("OTHER", raw=text)
    kind = toks[0]
    if kind == "BOOT":
        return Reply("BOOT", raw=text)
    if kind == "STOP":
        return Reply("STOP", reason=toks[1] if len(toks) > 1 else None, raw=text)
    if kind in ("HELLO", "STATUS"):
        return Reply(kind, fields=_kv(toks[1:]), raw=text)
    if kind in ("REJECTED", "ACCEPTED_NOT_EXECUTED", "EXECUTED"):
        cmd = toks[1] if len(toks) > 1 else None
        f = _kv(toks[2:])
        reason = f.pop("reason", None) if kind == "REJECTED" else None
        return Reply(kind, cmd=cmd, fields=f, reason=str(reason) if reason is not None else None,
                     raw=text)
    return Reply("OTHER", raw=text)


def _kv(tokens: list[str]) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            out[k] = _val(v)
    return out


def fmt_set(epoch: int, seq: int, pulse_us: int, valid_for_ms: int) -> str:
    return f"SET {epoch} {seq} {pulse_us} {valid_for_ms}"


def fmt_arm(epoch: int) -> str:
    return f"ARM {epoch}"


def fmt_heartbeat(epoch: int) -> str:
    return f"HEARTBEAT {epoch}"


def check_line(text: str) -> str:
    if len(text) > MAX_LINE or not text.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in text):
        raise ValueError(f"not a valid protocol line: {text!r}")
    return text
