"""Virtual device: a Python model of guarded_servo_core.h that speaks protocol v2.

It exists so the recorder, the player and the tests can run end to end without a
board. It is not the firmware: tests/test_conformance.py compiles the real core
header for the host and compares both implementations line by line on random
scripts, so the two cannot drift apart silently.

Time is passed in by the caller (``now_ms`` is the device's millis()), which keeps
every run deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PULSE_MIN, PULSE_MAX, PULSE_CENTER = 1450, 1550, 1500
GRACE_MS = 300
MIN_VALID_MS, MAX_VALID_MS = 20, 500
TICK_MS = 10
MAX_STEP_US = 5
PWM_HZ = 50.0
OSC_HZ = 25_000_000.0
LINE_CAP = 96
PRESCALE = int(round(OSC_HZ / (4096.0 * PWM_HZ))) - 1       # 121
TICK_US = (PRESCALE + 1) / (OSC_HZ / 1e6)                   # 4.88 us per PWM count
FW_VERSION = "0.1.0"
_M32 = 0xFFFFFFFF


def pulse_to_ticks(us: int) -> int:
    """Same expression as the firmware (float32 there, float64 here; no ties exist)."""
    return int(math.floor(us * PWM_HZ * 4096.0 / 1_000_000.0 + 0.5))


def ticks_to_us(ticks: int) -> float:
    return ticks * TICK_US


def _parse_u32(s: str) -> int | None:
    if not (1 <= len(s) <= 10) or not all("0" <= c <= "9" for c in s):
        return None
    v = int(s)
    return v if v <= _M32 else None


@dataclass
class PwmState:
    """What the PCA9685 channel 0 and OE pin look like to the outside."""
    oe_high: bool = True
    full_off: bool = True
    off_ticks: int | None = None

    def pulse_us(self) -> float | None:
        if self.oe_high or self.full_off or self.off_ticks is None:
            return None
        return ticks_to_us(self.off_ticks)


class VirtualDevice:
    def __init__(self, hardware_verified: bool = True, board: str = "VIRTUAL",
                 random_values: list[int] | None = None, now_ms: int = 0):
        self.hw_verified = hardware_verified
        self.board = board
        self._rnd = list(random_values) if random_values else []
        self.now = now_ms & _M32
        self.out: list[str] = []
        self.trace: list[tuple] = []
        self.pwm = PwmState()
        self.fail_i2c = False           # test hook: make every I2C write fail
        self.fail_i2c_next = 0          # test hook: make only the next n I2C writes fail
        self._line = ""
        self._discarding = False
        self.armed = False
        self.fault = True
        self.output_enabled = False
        self.epoch = 0
        self.last_seq = 0
        self.pending_seq = 0
        self.lease_start = 0
        self.lease_len = 0
        self.last_tick = 0
        self.applied = PULSE_CENTER
        self.target = PULSE_CENTER
        self.written_ticks = -1
        self.last_reject = "none"
        self.last_stop = "none"
        self._last_epoch_issued = 0
        # begin()
        self._oe(True)
        self.fault = not self._init_pwm()
        self.armed = False
        self.output_enabled = False
        self.epoch = 0
        self._emit("BOOT DISARMED; commanded/applied pulse is NOT measured position")

    # -- hardware abstraction ------------------------------------------------
    def _emit(self, s: str) -> None:
        self.out.append(s)

    def _oe(self, high: bool) -> None:
        self.trace.append(("oe", 1 if high else 0))
        self.pwm.oe_high = high

    def _i2c(self, reg: int, data: list[int]) -> bool:
        self.trace.append(("i2c", reg, tuple(data)))
        if self.fail_i2c_next > 0:
            self.fail_i2c_next -= 1
            return False
        if self.fail_i2c:
            return False
        if reg == 0xFD:                       # ALL_LED_OFF_H: bit 4 -> every channel full-off
            self.pwm.full_off = bool(data[0] & 0x10)
        elif reg == 0x06 and len(data) == 4:  # LED0_ON_L .. LED0_OFF_H
            self.pwm.full_off = bool(data[3] & 0x10)
            self.pwm.off_ticks = data[2] | ((data[3] & 0x0F) << 8)
        return True

    def take(self) -> list[str]:
        o, self.out = self.out, []
        return o

    def take_trace(self) -> list[tuple]:
        t, self.trace = self.trace, []
        return t

    def random32(self) -> int:
        return self._rnd.pop(0) if self._rnd else 0x1000

    # -- low level -----------------------------------------------------------
    def _write_channel(self, ticks: int) -> bool:
        if ticks <= 0 or ticks >= 4095:
            return False
        if not self._i2c(0x06, [0, 0, ticks & 0xFF, (ticks >> 8) & 0x0F]):
            return False
        self.written_ticks = ticks
        return True

    def _all_off(self) -> bool:
        self.written_ticks = -1
        return self._i2c(0xFD, [0x10])

    def _init_pwm(self) -> bool:
        if not self._i2c(0x00, [0x10]):
            return False
        if not self._i2c(0xFE, [PRESCALE]):
            return False
        if not self._i2c(0x00, [0x20]):
            return False
        return self._all_off()

    def _stop(self, reason: str, latch: bool) -> None:
        self._oe(True)
        self.output_enabled = False
        self.armed = False
        self.epoch = 0
        self.pending_seq = 0
        if not self._all_off():
            self.fault = True
        if latch:
            self.fault = True
        self.last_stop = reason[:23]
        self._emit(f"STOP {reason}")

    def _reject(self, cmd: str, reason: str, seq: int | None = None) -> None:
        self.last_reject = reason[:23]
        tail = f" seq={seq}" if seq is not None else ""
        self._emit(f"REJECTED {cmd} reason={reason}{tail}")

    def _lease_expired(self, now: int) -> bool:
        return ((now - self.lease_start) & _M32) > self.lease_len

    # -- clock ---------------------------------------------------------------
    def tick(self, now_ms: int) -> None:
        """One pass of loop() at time now_ms."""
        self.now = now_ms & _M32
        now = self.now
        if self.armed and self._lease_expired(now):
            self._stop("lease_expired", True)
            return
        if self.armed and self.output_enabled and ((now - self.last_tick) & _M32) >= TICK_MS:
            self.last_tick = now
            d = self.target - self.applied
            d = max(-MAX_STEP_US, min(MAX_STEP_US, d))
            self.applied += d
            ticks = pulse_to_ticks(self.applied)
            if ticks != self.written_ticks:
                if not self._write_channel(ticks):
                    self._stop("i2c", True)
                    return
            if self.pending_seq:
                self._emit(f"EXECUTED SET seq={self.pending_seq} applied_us={self.applied} "
                           f"mcu_ms={now}")
                self.pending_seq = 0

    def advance_to(self, now_ms: int) -> None:
        """Run tick() once per millisecond up to now_ms (inclusive), like W in the harness."""
        target = now_ms & _M32
        while self.now != target:
            self.tick((self.now + 1) & _M32)

    # -- input ---------------------------------------------------------------
    def feed_byte(self, c: str) -> None:
        if c == "\r":
            return
        if c == "\n":
            if self._discarding:
                self._discarding = False
                self._line = ""
                self._reject("LINE", "bad_line")
                return
            line, self._line = self._line, ""
            if line:
                self._handle(line)
            return
        if self._discarding:
            return
        if 32 <= ord(c) <= 126 and len(self._line) < LINE_CAP - 1:
            self._line += c
            return
        self._discarding = True
        self._line = ""
        if self.armed:
            self._stop("bad_line", True)

    def feed_text(self, s: str) -> None:
        for c in s:
            self.feed_byte(c)

    def feed_line(self, text: str) -> None:
        self.feed_text(text)
        self.feed_byte("\n")

    # -- commands ------------------------------------------------------------
    def _hello(self) -> None:
        if self.armed:
            self._stop("hello", False)
        e = self.random32() & _M32
        if e == 0 or e == self._last_epoch_issued:
            e = (self._last_epoch_issued + 1) & _M32
        if e == 0:
            e = 1
        self._last_epoch_issued = e
        self.epoch = e
        self.last_seq = 0
        self._emit(
            f"HELLO proto=2 fw={FW_VERSION} board={self.board} min_us={PULSE_MIN} "
            f"max_us={PULSE_MAX} center_us={PULSE_CENTER} step_us={MAX_STEP_US} "
            f"tick_ms={TICK_MS} grace_ms={GRACE_MS} max_valid_ms={MAX_VALID_MS} epoch={e} "
            f"hw_verified={int(self.hw_verified)} fault={int(self.fault)}")

    def _status(self) -> None:
        now = self.now
        left = 0
        if self.armed:
            used = (now - self.lease_start) & _M32
            left = 0 if used > self.lease_len else self.lease_len - used
        self._emit(
            f"STATUS armed={int(self.armed)} fault={int(self.fault)} "
            f"enabled={int(self.output_enabled)} epoch={self.epoch} commanded_us={self.target} "
            f"applied_us={self.applied} seq={self.last_seq} lease_left_ms={left} "
            f"last_reject={self.last_reject} last_stop={self.last_stop} mcu_ms={now}")

    def _handle(self, line: str) -> None:
        tok = line.split(" ")
        if len(tok) > 6:
            return self._reject("LINE", "too_many_tokens")
        if any(t == "" for t in tok):
            return self._reject("LINE", "empty_token")
        cmd, argc, now = tok[0], len(tok), self.now
        if cmd == "HELLO" and argc == 1:
            return self._hello()
        if cmd == "STATUS" and argc == 1:
            return self._status()
        if cmd == "ESTOP" and argc == 1:
            return self._stop("estop", True)
        if cmd == "DISARM" and argc == 1:
            return self._stop("disarm", False)
        if cmd == "CLEAR" and argc == 1:
            if self.armed:
                return self._reject("CLEAR", "armed")
            if not self._init_pwm():
                return self._stop("i2c", True)
            self.fault = False
            return self._emit("EXECUTED CLEAR")
        if cmd == "ARM":
            e = _parse_u32(tok[1]) if argc == 2 else None
            if e is None:
                return self._reject("ARM", "parse")
            if not self.hw_verified:
                return self._reject("ARM", "hardware_verified_false")
            if self.epoch == 0 or e != self.epoch:
                return self._reject("ARM", "epoch")
            if self.fault:
                return self._reject("ARM", "fault")
            if self.armed:
                return self._emit("ACCEPTED_NOT_EXECUTED ARM dup=1")
            self.armed = True
            self.lease_start, self.lease_len = now, GRACE_MS
            self.target = self.applied
            return self._emit(f"ACCEPTED_NOT_EXECUTED ARM epoch={self.epoch}")
        if cmd == "HEARTBEAT":
            e = _parse_u32(tok[1]) if argc == 2 else None
            if e is None:
                return self._reject("HEARTBEAT", "parse")
            if self.epoch == 0 or e != self.epoch:
                return self._reject("HEARTBEAT", "epoch")
            if not self.armed:
                return self._reject("HEARTBEAT", "disarmed")
            if self._lease_expired(now):
                self._stop("lease_expired", True)
                return self._reject("HEARTBEAT", "lease_expired")
            self.lease_start = now
            return self._emit("ACCEPTED_NOT_EXECUTED HEARTBEAT")
        if cmd == "SET":
            vals = [_parse_u32(t) for t in tok[1:]] if argc == 5 else None
            if vals is None or any(v is None for v in vals):
                return self._reject("SET", "parse")
            e, seq, us, valid = vals
            if self.epoch == 0 or e != self.epoch:
                return self._reject("SET", "epoch", seq)
            if not self.armed:
                return self._reject("SET", "disarmed", seq)
            if self.fault:
                return self._reject("SET", "fault", seq)
            if self._lease_expired(now):
                self._stop("lease_expired", True)
                return self._reject("SET", "lease_expired", seq)
            if seq == 0:
                return self._reject("SET", "seq_zero", seq)
            if seq == self.last_seq:
                return self._emit(f"ACCEPTED_NOT_EXECUTED SET seq={seq} dup=1")
            if seq < self.last_seq:
                return self._reject("SET", "seq_old", seq)
            if valid < MIN_VALID_MS or valid > MAX_VALID_MS:
                return self._reject("SET", "valid_for", seq)
            if us < PULSE_MIN or us > PULSE_MAX:
                return self._reject("SET", "range", seq)
            self.last_seq, self.target = seq, us
            self.lease_start, self.lease_len = now, valid
            self.pending_seq = seq
            if not self.output_enabled:
                if not self._write_channel(pulse_to_ticks(self.applied)):
                    return self._stop("i2c", True)
                self._oe(False)
                self.output_enabled = True
                self.last_tick = now
            return self._emit(f"ACCEPTED_NOT_EXECUTED SET seq={seq} target_us={us}")
        self._reject("UNKNOWN", "unknown_command")
