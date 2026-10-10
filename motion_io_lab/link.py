"""Transports between the host tools and a device speaking protocol v2.

* SimWorld          virtual time, virtual device, optional plant. Deterministic and fast.
                    Used by the demo and most tests. Everything it produces is synthetic.
* RealtimeLoopback  virtual device on the real monotonic clock, in process.
* PtyDeviceServer   virtual device behind a pseudo-terminal, so pyserial-based tools can
                    be exercised end to end without a board.
* SerialLink        a real serial port. Only the HARDWARE_ARMED path creates one.

All links expose the same small interface (``Link``). Host timestamps are
``time.monotonic_ns()``-style integers (clock domain ``host_monotonic``).
"""
from __future__ import annotations

import heapq
import os
import random
import select
import threading
import time
from abc import ABC, abstractmethod

from .protocol import check_line
from .vdevice import VirtualDevice


MAX_LINE_BUFFER = 4096        # protocol lines are at most 95 characters


class LinkDown(IOError):
    """The link is unplugged / the port vanished."""


class Link(ABC):
    kind = "abstract"
    #: "virtual" for simulated links, "serial" for a real port
    link_type = "virtual"

    @abstractmethod
    def now_ns(self) -> int: ...

    @abstractmethod
    def sleep_until_ns(self, t_ns: int) -> None: ...

    @abstractmethod
    def write_line(self, text: str) -> int:
        """Send one line; return the host time at which it was handed to the link."""

    @abstractmethod
    def read_lines(self) -> list[tuple[int, str]]:
        """Non-blocking. Returns [(host_rx_ns, line), ...] in arrival order."""

    def close(self) -> None:  # pragma: no cover - trivial
        pass


# ---------------------------------------------------------------------------
# virtual time


class SimWorld(Link):
    """Virtual host clock + virtual device + link delays (+ optional plant)."""

    kind = "sim"

    def __init__(self, *, seed: int = 0, plant=None, hardware_verified: bool = True,
                 uplink_ms: tuple[float, float] = (0.4, 0.3),
                 downlink_ms: tuple[float, float] = (0.4, 0.3),
                 start_ns: int = 1_000_000_000_000, device_uptime_ms: int = 5_000,
                 wake_jitter_ms: tuple[float, float, float] | None = (0.12, 0.01, 4.0)):
        """``wake_jitter_ms`` = (mean exponential lateness, spike probability, max spike):
        an assumed model of how late a sleeping host process wakes up (synthetic)."""
        self.rng = random.Random(seed)
        self.wake_jitter = wake_jitter_ms
        self.t = int(start_ns)
        self.hw_verified = hardware_verified
        self.plant = plant
        self.uplink = uplink_ms
        self.downlink = downlink_ms
        # the device has been up for a while; its tick phase against the host schedule is random
        self._boot_ns = (self.t - (device_uptime_ms + self.rng.randrange(0, 10_000)) * 1_000_000
                         - self.rng.randrange(0, 1_000_000))
        self.device = VirtualDevice(hardware_verified=hardware_verified, board="VIRTUAL",
                                    random_values=[self.rng.getrandbits(32) | 1 for _ in range(64)],
                                    now_ms=self._dev_ms(self.t))
        self.device.take()
        self.device.take_trace()
        self.connected = True
        self._last_ms = self._dev_ms(self.t)
        self._up: list[tuple[int, int, str]] = []
        self._down: list[tuple[int, int, str]] = []
        self._n = 0
        self._last_up = 0
        self._last_down = 0
        self.history: list[tuple[int, float, float | None]] = []  # (t_ns, true_angle, pulse_us)
        self.dropped_in_flight = 0
        #: a real serial port raises on read as well as on write once the cable is out
        self.read_raises_when_unplugged = False

    # -- helpers
    def _dev_ms(self, t: int) -> int:
        return ((t - self._boot_ns) // 1_000_000) & 0xFFFFFFFF

    def _delay(self, spec: tuple[float, float]) -> int:
        return int((spec[0] + self.rng.random() * spec[1]) * 1e6)

    def now_ns(self) -> int:
        return self.t

    # -- Link
    def write_line(self, text: str) -> int:
        check_line(text)
        if not self.connected:
            raise LinkDown("virtual cable is unplugged")
        tx = self.t
        due = max(self._last_up, tx + self._delay(self.uplink))
        self._last_up = due
        self._n += 1
        heapq.heappush(self._up, (due, self._n, text))
        return tx

    def read_lines(self) -> list[tuple[int, str]]:
        if not self.connected and self.read_raises_when_unplugged:
            raise LinkDown("virtual cable is unplugged")   # like a real port that vanished
        out = []
        while self._down and self._down[0][0] <= self.t:
            due, _, line = heapq.heappop(self._down)
            out.append((due, line))
        return out

    def sleep_until_ns(self, t_ns: int) -> None:
        if t_ns > self.t and self.wake_jitter:
            mean, p, spike = self.wake_jitter
            late = self.rng.expovariate(1.0 / mean) if mean > 0 else 0.0
            if self.rng.random() < p:
                late += self.rng.uniform(1.0, spike)
            t_ns += int(late * 1e6)
        self.advance_to(t_ns)

    # -- the simulation loop
    def advance_to(self, t_target: int) -> None:
        while self.t < t_target:
            ms_now = (self.t - self._boot_ns) // 1_000_000
            next_boundary = self._boot_ns + (ms_now + 1) * 1_000_000
            nxt = min(t_target, next_boundary)
            if self._up:
                nxt = min(nxt, max(self._up[0][0], self.t + 1))
            self.t = nxt
            self._step()

    def _step(self) -> None:
        dev = self.device
        ms = self._dev_ms(self.t)
        if ms != self._last_ms:
            self._last_ms = ms
            dev.advance_to(ms)  # the device keeps running when the cable is out
            if self.plant is not None:
                pulse = dev.pwm.pulse_us()
                self.plant.step(0.001, pulse)
                self.history.append((self.t, self.plant.angle_deg, pulse))
        while self._up and self._up[0][0] <= self.t:
            _, _, text = heapq.heappop(self._up)
            dev.feed_line(text)
        for line in dev.take():
            if self.connected:
                due = max(self._last_down, self.t + self._delay(self.downlink))
                self._last_down = due
                self._n += 1
                heapq.heappush(self._down, (due, self._n, line))
        dev.take_trace()

    # -- failure injection
    def unplug(self, read_raises: bool = False) -> None:
        """Host-side link goes away; the device keeps running.

        ``read_raises=True`` also makes ``read_lines`` raise ``LinkDown``, as ``SerialLink``
        does when the port vanishes (without it only ``write_line`` fails)."""
        self.connected = False
        self.read_raises_when_unplugged = read_raises
        self.dropped_in_flight += len(self._up) + len(self._down)
        self._up.clear()
        self._down.clear()

    def replug(self, reboot: bool = False) -> None:
        self.connected = True
        self.read_raises_when_unplugged = False
        if reboot:
            self._boot_ns = self.t
            self._last_ms = 0
            self.device = VirtualDevice(
                hardware_verified=self.hw_verified, board="VIRTUAL",
                random_values=[self.rng.getrandbits(32) | 1 for _ in range(64)], now_ms=0)
            self.device.take_trace()
            for line in self.device.take():  # the boot banner reaches the host
                self._n += 1
                heapq.heappush(self._down, (self.t + self._delay(self.downlink), self._n, line))


# ---------------------------------------------------------------------------
# real clock, in process


def precise_sleep_until(target_ns: int, tick=None) -> None:
    """Coarse sleep, then a short spin. ``tick`` is called while waiting."""
    while True:
        rem = target_ns - time.monotonic_ns()
        if rem <= 0:
            return
        if tick is not None:
            tick()
        if rem > 1_500_000:
            time.sleep(min(rem - 1_000_000, 500_000) / 1e9)
        elif rem > 100_000:
            time.sleep(0)


class RealtimeLoopback(Link):
    """Virtual device on the real clock. The device is advanced whenever the host acts."""

    kind = "realtime"

    def __init__(self, hardware_verified: bool = True, seed: int = 0):
        rng = random.Random(seed)
        self._t0 = time.monotonic_ns()
        self.device = VirtualDevice(hardware_verified, "VIRTUAL",
                                    [rng.getrandbits(32) | 1 for _ in range(64)], now_ms=0)
        self._pending: list[tuple[int, str]] = []
        self.device.take()

    def _ms(self) -> int:
        return ((time.monotonic_ns() - self._t0) // 1_000_000) & 0xFFFFFFFF

    def _run(self) -> None:
        self.device.advance_to(self._ms())
        now = time.monotonic_ns()
        for line in self.device.take():
            self._pending.append((now, line))
        self.device.take_trace()

    def now_ns(self) -> int:
        return time.monotonic_ns()

    def sleep_until_ns(self, t_ns: int) -> None:
        precise_sleep_until(t_ns, self._run)
        self._run()

    def write_line(self, text: str) -> int:
        check_line(text)
        self._run()
        tx = time.monotonic_ns()
        self.device.feed_line(text)
        self._run()
        return tx

    def read_lines(self) -> list[tuple[int, str]]:
        self._run()
        out, self._pending = self._pending, []
        return out


# ---------------------------------------------------------------------------
# pseudo-terminal device


class PtyDeviceServer:
    """Run a VirtualDevice behind a pty so serial-port tools can talk to it."""

    def __init__(self, hardware_verified: bool = True, seed: int = 0):
        import pty
        import tty

        rng = random.Random(seed)
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.device = VirtualDevice(hardware_verified, "VIRTUAL_PTY",
                                    [rng.getrandbits(32) | 1 for _ in range(64)], now_ms=0)
        self.rx_log: list[tuple[int, str]] = []
        self._stop = threading.Event()
        self._t0 = time.monotonic_ns()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _ms(self) -> int:
        return ((time.monotonic_ns() - self._t0) // 1_000_000) & 0xFFFFFFFF

    def _flush(self) -> None:
        for line in self.device.take():
            os.write(self.master, (line + "\n").encode("ascii"))
        self.device.take_trace()

    def _loop(self) -> None:
        self._flush()
        while not self._stop.is_set():
            r, _, _ = select.select([self.master], [], [], 0.001)
            self.device.advance_to(self._ms())
            if r:
                try:
                    data = os.read(self.master, 4096)
                except OSError:
                    break
                for ch in data.decode("ascii", errors="replace"):
                    self.device.feed_byte(ch)
            self._flush()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "PtyDeviceServer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# real serial port


class SerialLink(Link):
    kind = "serial"
    link_type = "serial"

    def __init__(self, port: str, baud: int = 115200, settle_s: float = 1.0):
        import serial

        self._serial_mod = serial
        try:
            self.ser = serial.Serial(port, baud, timeout=0, write_timeout=0.5)
        except serial.SerialException as exc:
            raise LinkDown(f"cannot open {port}: {exc}") from exc
        self._buf = b""
        if settle_s:
            time.sleep(settle_s)  # many boards reset when the port opens
        self.ser.reset_input_buffer()

    def now_ns(self) -> int:
        return time.monotonic_ns()

    def sleep_until_ns(self, t_ns: int) -> None:
        precise_sleep_until(t_ns)

    def write_line(self, text: str) -> int:
        check_line(text)
        tx = time.monotonic_ns()
        try:
            self.ser.write((text + "\n").encode("ascii"))
        except (self._serial_mod.SerialException, OSError) as exc:
            raise LinkDown(str(exc)) from exc
        return tx

    def read_lines(self) -> list[tuple[int, str]]:
        try:
            n = self.ser.in_waiting
            data = self.ser.read(n) if n else b""
        except (self._serial_mod.SerialException, OSError) as exc:
            raise LinkDown(str(exc)) from exc
        now = time.monotonic_ns()
        self._buf += data
        if len(self._buf) > MAX_LINE_BUFFER and b"\n" not in self._buf:
            self._buf = b""            # noise with no line ends: drop it instead of growing forever
        out = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            text = line.decode("ascii", errors="replace").strip("\r")
            if text:
                out.append((now, text))
        return out

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass


class NullLink(Link):
    """No device at all (DRY_RUN). A clock only; write_line is a programming error.

    ``realtime=False`` uses a virtual clock so a dry run finishes instantly.
    """

    kind = "null"
    link_type = "none"

    def __init__(self, realtime: bool = False, start_ns: int = 1_000_000_000_000):
        self.realtime = realtime
        self._t = start_ns

    def now_ns(self) -> int:
        return time.monotonic_ns() if self.realtime else self._t

    def sleep_until_ns(self, t_ns: int) -> None:
        if self.realtime:
            precise_sleep_until(t_ns)
        else:
            self._t = max(self._t, t_ns)

    def write_line(self, text: str) -> int:
        raise RuntimeError("DRY_RUN must never write to a device")

    def read_lines(self) -> list[tuple[int, str]]:
        return []
