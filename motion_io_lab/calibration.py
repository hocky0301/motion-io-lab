"""Pulse -> angle calibration from points measured by hand (e.g. with a protractor).

The device is commanded in microseconds; "degrees" only exist once somebody has
measured them. Without a calibration file the analysis stays in microseconds.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Calibration:
    center_us: float
    deg_per_us: float
    offset_deg: float
    rms_residual_deg: float
    n_points: int
    source: str                  # "synthetic" for the demo; "hardware" when measured
    text: str                    # exact file content (the version is its hash)

    def pulse_to_deg(self, pulse_us):
        return (np.asarray(pulse_us, dtype=float) - self.center_us) * self.deg_per_us + self.offset_deg

    @property
    def version(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:8]


def fit(points: list[tuple[float, float]], source: str, center_us: float = 1500.0) -> Calibration:
    if len(points) < 3:
        raise ValueError("need at least 3 (pulse_us, angle_deg) points")
    arr = np.asarray(points, dtype=float)
    if not np.isfinite(arr).all():
        raise ValueError("non-finite calibration point")
    if len(set(arr[:, 0].tolist())) < 3:
        raise ValueError("need at least 3 distinct pulse values")
    x = arr[:, 0] - center_us
    A = np.c_[x, np.ones(len(x))]
    (slope, offset), *_ = np.linalg.lstsq(A, arr[:, 1], rcond=None)
    if not slope > 0:
        raise ValueError(f"angle must increase with pulse width (fitted slope {slope:.4g} deg/us)")
    res = arr[:, 1] - A @ np.array([slope, offset])
    rms = float(math.sqrt(float((res ** 2).mean())))
    body = {"schema_version": "motion-io-lab/calibration-v1", "model": "linear",
            "center_us": center_us, "deg_per_us": round(float(slope), 8) + 0.0,
            # "+ 0.0" turns -0.0 into 0.0, so the text (and its hash) does not depend on the
            # numpy build's sign of zero
            "offset_deg": round(float(offset), 6) + 0.0, "rms_residual_deg": round(rms, 6) + 0.0,
            "n_points": len(points), "source": source,
            "points": [[float(a), float(b)] for a, b in points]}
    return Calibration(center_us, body["deg_per_us"], body["offset_deg"], body["rms_residual_deg"],
                       len(points), source, json.dumps(body, indent=2, sort_keys=True) + "\n")


def fit_csv(csv_path: str | Path, source: str = "hardware") -> Calibration:
    pts = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        if not rd.fieldnames or not {"pulse_us", "angle_deg"} <= set(rd.fieldnames):
            raise ValueError("calibration csv needs columns pulse_us,angle_deg")
        for i, row in enumerate(rd, start=2):
            try:
                pts.append((float(row["pulse_us"]), float(row["angle_deg"])))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{csv_path}:{i}: not a number ({exc})") from exc
    return fit(pts, source)


def load(path: str | Path) -> Calibration:
    text = Path(path).read_text(encoding="utf-8")
    d = json.loads(text)
    if d.get("schema_version") != "motion-io-lab/calibration-v1":
        raise ValueError(f"{path}: not a calibration-v1 file")
    return Calibration(d["center_us"], d["deg_per_us"], d["offset_deg"], d["rms_residual_deg"],
                       d["n_points"], d["source"], text)
