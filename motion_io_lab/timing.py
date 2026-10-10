"""Timing statistics for a paced send loop."""
from __future__ import annotations

import numpy as np


def timing_stats(sched_ns: list[int], actual_ns: list[int]) -> dict:
    """Lateness of each send against its host-monotonic deadline.

    ``drift_ppm`` is the slope of lateness against schedule time: with deadline pacing it
    should be ~0 (errors do not accumulate); with sleep-after-send pacing it grows.
    """
    if len(sched_ns) != len(actual_ns):
        raise ValueError("length mismatch")
    n = len(sched_ns)
    if n == 0:
        return {"n": 0}
    s = np.asarray(sched_ns, dtype=np.float64)
    late_ms = (np.asarray(actual_ns, dtype=np.float64) - s) / 1e6
    out = {
        "n": n,
        "lateness_ms_p50": float(np.percentile(late_ms, 50)),
        "lateness_ms_p95": float(np.percentile(late_ms, 95)),
        "lateness_ms_p99": float(np.percentile(late_ms, 99)),
        "lateness_ms_max": float(late_ms.max()),
        "lateness_ms_mean": float(late_ms.mean()),
        "early_count": int((late_ms < 0).sum()),
    }
    if n >= 3 and np.ptp(s) > 0:
        slope = np.polyfit((s - s[0]) / 1e6, late_ms, 1)[0]     # ms of lateness per ms
        out["drift_ppm"] = float(slope * 1e6)
    else:
        out["drift_ppm"] = None
    return out
