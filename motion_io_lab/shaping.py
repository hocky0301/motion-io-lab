"""Host-side shaping of a desired pulse before it is sent: clamp, then slew limit.

The device enforces its own limits (range, 5 us per 10 ms). The host limits are
normally tighter or equal; they exist so the recording shows what was *asked for*
(``desired_pulse_us``) next to what was *sent* (``commanded_pulse_us``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

CENTER_US = 1500
DEVICE_MIN_US, DEVICE_MAX_US = 1450, 1550


@dataclass
class Shaper:
    min_us: int = DEVICE_MIN_US
    max_us: int = DEVICE_MAX_US
    max_slew_us_per_s: float | None = 400.0
    last: float | None = None

    def __post_init__(self) -> None:
        if not (DEVICE_MIN_US <= self.min_us <= self.max_us <= DEVICE_MAX_US):
            raise ValueError(f"host range must lie inside the device range "
                             f"{DEVICE_MIN_US}..{DEVICE_MAX_US}")
        if self.max_slew_us_per_s is not None and not self.max_slew_us_per_s > 0:
            raise ValueError("max_slew_us_per_s must be positive or None")

    def reset(self, value: float | None = None) -> None:
        self.last = value

    def shape(self, desired: float, dt_s: float) -> tuple[int, str]:
        """Return (pulse_us, flags). ``flags`` is 'none', 'clamp', 'slew' or 'clamp+slew'."""
        if not math.isfinite(desired):
            raise ValueError("desired pulse must be finite")
        flags = []
        v = min(max(desired, self.min_us), self.max_us)
        if v != desired:
            flags.append("clamp")
        if self.max_slew_us_per_s is not None and self.last is not None and dt_s > 0:
            step = self.max_slew_us_per_s * dt_s
            lim = min(max(v, self.last - step), self.last + step)
            if lim != v:
                flags.append("slew")
            v = lim
        self.last = v
        return int(round(v)), "+".join(flags) if flags else "none"


def x_to_pulse(x: float, span_us: float = 40.0, center_us: int = CENTER_US) -> float:
    """Map a normalised axis value in [-1, 1] to a pulse width. Out-of-range input is clipped."""
    if not math.isfinite(x):
        raise ValueError("axis value must be finite")
    return center_us + min(max(x, -1.0), 1.0) * span_us
