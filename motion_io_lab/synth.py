"""Synthetic parts: a servo plant and a renderer that turns its motion into a video.

Everything produced here is SYNTHETIC. It exists so the whole path (command ->
device -> plant -> rendered video -> marker detection -> analysis) can be tested and
demonstrated on a laptop. The plant constants are assumptions, not measurements of
any real servo; do not quote results made with them as hardware results.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .vdevice import PRESCALE, TICK_US  # noqa: F401  (TICK_US: 4.88 us per PWM count)

CENTER_US = 1500.0


class ServoPlant:
    """First-order lag with a dead band and a speed limit. Angle 0 = pulse 1500 us.

    ``deadband_deg`` models stiction: the horn does not move while the target is
    closer than that, so the settle position depends on the direction of approach.
    """

    def __init__(self, deg_per_us: float = 0.1, tau_s: float = 0.05, deadband_deg: float = 0.12,
                 max_rate_dps: float = 300.0, seed: int = 0, deadband_spread: float = 0.5):
        rng = random.Random(seed)
        self.deg_per_us = deg_per_us
        self.tau_s = tau_s
        self.deadband_deg = deadband_deg * (1.0 + deadband_spread * (2 * rng.random() - 1))
        self.max_rate_dps = max_rate_dps
        self.angle_deg = 0.0
        self.params = {"deg_per_us": deg_per_us, "tau_s": tau_s,
                       "deadband_deg": round(self.deadband_deg, 4), "max_rate_dps": max_rate_dps}

    def step(self, dt_s: float, pulse_us: float | None) -> None:
        """Advance by dt. ``pulse_us`` None = no drive (OE high / full-off): the horn holds."""
        if pulse_us is None:
            return
        target = (pulse_us - CENTER_US) * self.deg_per_us
        err = target - self.angle_deg
        if abs(err) <= self.deadband_deg:
            return
        rate = max(-self.max_rate_dps, min(self.max_rate_dps, err / self.tau_s))
        self.angle_deg += rate * dt_s


@dataclass
class VideoSpec:
    width: int = 480
    height: int = 360
    fps: float = 30.0
    pivot_px: tuple[float, float] = (240.0, 300.0)
    radius_px: float = 140.0
    marker_radius_px: float = 10.0
    reference_angle_rad: float = -math.pi / 2     # marker straight above the pivot at angle 0
    angle_scale_rad: float = 0.1                  # +-1 normalised = +-0.1 rad (5.73 deg)

    def camera_config(self, source: str | int) -> dict:
        return {"source": source, "capture_backend": "default", "hsv_lower": [35, 80, 70],
                "hsv_upper": [85, 255, 255], "min_area_px": 40, "max_area_px": 10000,
                "pivot_px": list(self.pivot_px), "reference_angle_rad": self.reference_angle_rad,
                "angle_scale_rad": self.angle_scale_rad,
                "notes": "SYNTHETIC rendered video; not a camera calibration."}


def render_video(path: str | Path, t_s: np.ndarray, angle_deg: np.ndarray, duration_s: float,
                 spec: VideoSpec | None = None, latency_s: float = 0.07, seed: int = 0,
                 dropout_p: float = 0.03, distractor_p: float = 0.03,
                 noise_px_sigma: float = 0.0) -> dict:
    """Draw the marker at the plant angle seen ``latency_s`` earlier and encode an mp4.

    Returns the ground truth per frame so tests can check the detector.
    """
    import cv2

    spec = spec or VideoSpec()
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    rng = np.random.default_rng(seed)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), spec.fps,
                         (spec.width, spec.height))
    if not vw.isOpened():
        raise RuntimeError("cannot open the video writer (mp4v)")
    n = int(math.floor(duration_s * spec.fps)) + 1
    truth = {"frame_t_s": [], "true_angle_deg": [], "dropout": [], "distractor": []}
    yy, xx = np.mgrid[0:spec.height, 0:spec.width]
    bg = (90 + 40 * (yy / spec.height)).astype(np.uint8)
    base = np.dstack([bg, bg, bg])
    cv2.rectangle(base, (int(spec.pivot_px[0]) - 30, int(spec.pivot_px[1]) - 10),
                  (int(spec.pivot_px[0]) + 30, int(spec.pivot_px[1]) + 40), (60, 60, 60), -1)
    for k in range(n):
        m = k / spec.fps
        ang = float(np.interp(m - latency_s, t_s, angle_deg, left=angle_deg[0]))
        th = spec.reference_angle_rad + math.radians(ang)
        cx = spec.pivot_px[0] + spec.radius_px * math.cos(th) + float(rng.normal(0, noise_px_sigma))
        cy = spec.pivot_px[1] + spec.radius_px * math.sin(th) + float(rng.normal(0, noise_px_sigma))
        frame = base.copy()
        noise = rng.normal(0, 3.0, frame.shape)
        frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        drop = bool(rng.random() < dropout_p)
        dis = bool(rng.random() < distractor_p) and not drop
        if not drop:
            cv2.circle(frame, (round(cx * 16), round(cy * 16)), round(spec.marker_radius_px * 16),
                       (0, 200, 0), -1, cv2.LINE_AA, 4)
        if dis:
            dx, dy = int(rng.integers(40, spec.width - 40)), int(rng.integers(40, 120))
            cv2.circle(frame, (dx, dy), 9, (0, 200, 0), -1, cv2.LINE_AA)
        vw.write(frame)
        truth["frame_t_s"].append(m)
        truth["true_angle_deg"].append(ang)
        truth["dropout"].append(drop)
        truth["distractor"].append(dis)
    vw.release()
    truth["spec"] = spec
    truth["latency_s"] = latency_s
    return truth


def new_trial(plant: ServoPlant, rng: random.Random, gain_sigma: float = 0.01,
              home: bool = True) -> None:
    """Start of a trial. The gain changes a little (supply droop, temperature): a 1 % assumption,
    not a measurement.

    ``home=True`` puts the horn back at angle 0 first: every trial starts from the same state,
    (move to centre, wait, then start). ``home=False``
    keeps the horn where the last trial left it, so a trial's start differs by whatever the
    previous one ended on; that difference then shows up in the comparison."""
    plant.deg_per_us = plant.params["deg_per_us"] * (1.0 + rng.gauss(0.0, gain_sigma))
    if home:
        plant.angle_deg = 0.0
