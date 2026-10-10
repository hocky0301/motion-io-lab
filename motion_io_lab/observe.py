"""Observation: find a coloured marker in a live camera or a video file and log it.

Never actuates anything. What this module is careful about:

* ``capture_time_ns`` is null and ``capture_time_provenance`` is ``unknown``. OpenCV
  does not expose the exposure time; the host receive time is stored separately in
  ``received_monotonic_ns`` and is NOT an exposure time.
* for a video *file*, the host receive time is only the decode time. The usable time
  base is ``media_pos_ms`` (position inside the file); how that maps onto the command
  timeline is an explicit input of the analysis, not something this module guesses.
* a frame with no marker, or with more than one candidate, is ``valid=false`` with a
  reason; coordinates are null, never 0 and never "the biggest blob".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .manifest import write_run_manifest
from .triallog import SOURCES, StreamWriter, TrialContext


class ConfigError(ValueError):
    pass


@dataclass
class CameraConfig:
    source: int | str
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    min_area_px: float
    max_area_px: float
    pivot_px: tuple[float, float] | None
    reference_angle_rad: float | None
    angle_scale_rad: float | None
    capture_backend: str
    min_radius_px: float
    raw: dict
    config_hash: str

    @property
    def angle_calibrated(self) -> bool:
        return None not in (self.pivot_px, self.reference_angle_rad, self.angle_scale_rad)


_REQUIRED = ("source", "hsv_lower", "hsv_upper", "min_area_px", "max_area_px")


def parse_config(d: dict, text: str | None = None) -> CameraConfig:
    """Validate a camera config. Every problem is a ConfigError naming the key."""
    if not isinstance(d, dict):
        raise ConfigError("camera config must be a JSON object")
    missing = [k for k in _REQUIRED if k not in d]
    if missing:
        raise ConfigError(f"camera config is missing {missing}")

    def triple(key: str, hi: tuple[int, int, int]) -> tuple[int, int, int]:
        v = d[key]
        if not (isinstance(v, (list, tuple)) and len(v) == 3):
            raise ConfigError(f"{key} must be 3 numbers [H,S,V]")
        out = []
        for x, m in zip(v, hi):
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= m:
                raise ConfigError(f"{key} values must be numbers within 0..{list(hi)}")
            out.append(int(x))
        return tuple(out)  # type: ignore[return-value]

    lo, hi = triple("hsv_lower", (179, 255, 255)), triple("hsv_upper", (179, 255, 255))
    if any(a > b for a, b in zip(lo[1:], hi[1:])):
        raise ConfigError("hsv_lower must be <= hsv_upper in the S and V channels")
    # H may wrap through 0 (a red marker): hsv_lower H > hsv_upper H means "lower..179 and 0..upper"
    amin, amax = d["min_area_px"], d["max_area_px"]
    if not all(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a)
               for a in (amin, amax)) or not 0 < amin < amax:
        raise ConfigError("need 0 < min_area_px < max_area_px")
    src = d["source"]
    if isinstance(src, bool) or not isinstance(src, (int, str)):
        raise ConfigError("source must be a camera index or a local video file path")
    if isinstance(src, str) and "://" in src:
        raise ConfigError("network sources are not supported; use a camera index or a local file")
    pivot = d.get("pivot_px")
    if pivot is not None:
        if not (isinstance(pivot, (list, tuple)) and len(pivot) == 2
                and all(isinstance(x, (int, float)) and math.isfinite(x) for x in pivot)):
            raise ConfigError("pivot_px must be [x, y] or null")
        pivot = (float(pivot[0]), float(pivot[1]))
    ref, scale = d.get("reference_angle_rad"), d.get("angle_scale_rad")
    for k, v in (("reference_angle_rad", ref), ("angle_scale_rad", scale)):
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)
                              or not math.isfinite(v)):
            raise ConfigError(f"{k} must be a finite number or null")
    if scale is not None and scale <= 0:
        raise ConfigError("angle_scale_rad must be positive")
    backend = d.get("capture_backend", "default")
    if backend not in ("default", "avfoundation", "ffmpeg"):
        raise ConfigError("capture_backend must be default, avfoundation or ffmpeg")
    text = text if text is not None else json.dumps(d, sort_keys=True)
    return CameraConfig(src, lo, hi, float(amin), float(amax), pivot,
                        None if ref is None else float(ref), None if scale is None else float(scale),
                        backend, float(d.get("min_radius_px", 5.0)), d,
                        hashlib.sha256(text.encode("utf-8")).hexdigest()[:8])


def load_config(path: str | Path) -> CameraConfig:
    text = Path(path).read_text(encoding="utf-8")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    return parse_config(d, text)


@dataclass
class Detection:
    n_blobs: int
    cx: float | None = None
    cy: float | None = None
    area: float | None = None
    reason: str | None = None       # None when valid
    blob_areas: tuple[float, ...] = ()


def detect_marker(frame: np.ndarray, cfg: CameraConfig) -> Detection:
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo, hi = cfg.hsv_lower, cfg.hsv_upper
    if lo[0] <= hi[0]:
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    else:                       # hue wraps through 0 (e.g. red: lower 170, upper 10)
        m1 = cv2.inRange(hsv, np.array(lo, np.uint8), np.array((179, hi[1], hi[2]), np.uint8))
        m2 = cv2.inRange(hsv, np.array((0, lo[1], lo[2]), np.uint8), np.array(hi, np.uint8))
        mask = cv2.bitwise_or(m1, m2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    good = []
    for c in contours:
        a = float(cv2.contourArea(c))
        if cfg.min_area_px <= a <= cfg.max_area_px:
            m = cv2.moments(c)
            if m["m00"] > 0:
                good.append((m["m10"] / m["m00"], m["m01"] / m["m00"], a))
    if not good:
        return Detection(0, reason="none_found")
    if len(good) > 1:
        # Ambiguous scene: do not pick the "plausible" one.
        return Detection(len(good), reason="multiple_blobs", blob_areas=tuple(g[2] for g in good))
    cx, cy, a = good[0]
    return Detection(1, float(cx), float(cy), float(a), None, (a,))


def normalized_angle(cx: float, cy: float, cfg: CameraConfig) -> tuple[float | None, str | None]:
    """Angle of the marker about the pivot, divided by ``angle_scale_rad`` (so +-1 = full scale)."""
    if not cfg.angle_calibrated:
        return None, "no_calibration"
    px, py = cfg.pivot_px  # type: ignore[misc]
    dx, dy = cx - px, cy - py
    if math.hypot(dx, dy) < cfg.min_radius_px:
        return None, "too_close_to_pivot"
    diff = math.atan2(math.sin(math.atan2(dy, dx) - cfg.reference_angle_rad),  # type: ignore[operator]
                      math.cos(math.atan2(dy, dx) - cfg.reference_angle_rad))  # type: ignore[operator]
    return diff / cfg.angle_scale_rad, None  # type: ignore[operator]


UNITS = "norm_angle"            # observed angle / angle_scale_rad (+-1 = full scale)
FRAME = "camera_image"


def open_capture(cfg_source: int | str, backend: str = "default"):
    import cv2

    if isinstance(cfg_source, str) and not Path(cfg_source).is_file():
        raise ConfigError(f"video file not found: {cfg_source}")
    api = {"default": cv2.CAP_ANY, "avfoundation": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
           "ffmpeg": cv2.CAP_FFMPEG}[backend]
    cap = cv2.VideoCapture(cfg_source, api)
    if not cap.isOpened():
        raise RuntimeError("camera/video cannot be opened; check the index and the OS camera "
                           "permission (live), or the file path and codec (file)")
    return cap


def _media_pos_ms(cap, frame_id: int, fps: float) -> tuple[float | None, str]:
    import cv2

    if fps > 0:
        # index / fps is exact for constant-rate files; back ends disagree on whether
        # CAP_PROP_POS_MSEC means this frame or the next one.
        return frame_id * 1000.0 / fps, "frame_index_over_fps"
    pos = cap.get(cv2.CAP_PROP_POS_MSEC)
    return (float(pos) if pos and pos > 0 else None), "cap_prop_pos_msec"


def observe(cfg: CameraConfig, writer: StreamWriter, *, seconds: float | None = None,
            max_frames: int | None = None, show: bool = False, save_video: Path | None = None,
            ) -> dict:
    """Run the observation loop. Returns a summary dict (also stored by the caller)."""
    import cv2

    cap = open_capture(cfg.source, cfg.capture_backend)
    live = isinstance(cfg.source, int)
    source_kind = "live" if live else "file"
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    fps_ok = math.isfinite(fps) and 0 < fps <= 240
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = None
    n = nvalid = 0
    reasons: dict[str, int] = {}
    start = time.monotonic()
    try:
        while True:
            if seconds is not None and time.monotonic() - start >= seconds:
                break
            if max_frames is not None and n >= max_frames:
                break
            before = time.monotonic_ns()
            ok, frame = cap.read()
            after = time.monotonic_ns()
            if not ok:
                if live:
                    writer.write("marker", frame_id=n, media_pos_ms=None, source_kind=source_kind,
                                 observed_value=None, cx_px=None, cy_px=None, area_px=None,
                                 n_blobs=0, invalid_reason="read_failed", valid=False,
                                 received_monotonic_ns=after, processed_monotonic_ns=after,
                                 host_before_read_ns=before)
                break           # end of file (or a dead camera): stop, do not loop
            if save_video is not None and vw is None:
                vw = cv2.VideoWriter(str(save_video), cv2.VideoWriter_fourcc(*"mp4v"),
                                     fps if fps_ok else 30.0, (frame.shape[1], frame.shape[0]))
                if not vw.isOpened():
                    raise RuntimeError("video writer unavailable; choose another codec")
            if vw is not None:
                vw.write(frame)
            det = detect_marker(frame, cfg)
            value: float | None = None
            if det.reason is not None:
                valid, reason_out = False, det.reason
            else:
                value, a_reason = normalized_angle(det.cx, det.cy, cfg)  # type: ignore[arg-type]
                if a_reason == "too_close_to_pivot":
                    valid, reason_out = False, a_reason
                else:      # "no_calibration": pixels are valid, the angle is simply absent
                    valid, reason_out = True, None
            mpos, mpos_src = (None, "none") if live else _media_pos_ms(cap, n, fps if fps_ok else 0.0)
            if not valid:
                reasons[reason_out or "unknown"] = reasons.get(reason_out or "unknown", 0) + 1
            else:
                nvalid += 1
            writer.write(
                "marker", frame_id=n, media_pos_ms=mpos, media_pos_source=mpos_src,
                source_kind=source_kind,
                observed_value=value if valid else None,
                cx_px=det.cx if valid else None, cy_px=det.cy if valid else None,
                area_px=det.area if valid else None, n_blobs=det.n_blobs,
                invalid_reason=reason_out, valid=valid,
                beyond_scale=(abs(value) > 1.0) if (valid and value is not None) else None,
                angle_available=bool(valid and value is not None),
                host_before_read_ns=before, received_monotonic_ns=after, processed_monotonic_ns=
                time.monotonic_ns(),
                received_time_meaning="decode_time_not_capture" if not live else
                "host_read_return_not_exposure")
            n += 1
            if show:
                disp = frame.copy()
                if valid:
                    cv2.circle(disp, (round(det.cx), round(det.cy)), 6, (0, 255, 0), 2)
                cv2.imshow("OBSERVATION ONLY - no motor commands", disp)
                if cv2.waitKey(1) & 255 == ord("q"):
                    break
    finally:
        cap.release()
        if vw is not None:
            vw.release()
        if show:
            cv2.destroyAllWindows()
    return {"frames": n, "valid_frames": nvalid, "invalid_reasons": reasons,
            "source_kind": source_kind, "width": w, "height": h,
            "fps_reported": fps if fps_ok else None, "physical_actuation": False,
            "capture_time_ns": "null (OpenCV does not expose exposure time)"}


def make_writer(out_root: Path, session_id: str, trial_id: str, cfg: CameraConfig, mode: str,
                source: str, stream_file: str = "observation",
                calibration_version: str | None = None) -> tuple[StreamWriter, Path]:
    import cv2

    trial_dir = Path(out_root) / trial_id
    ctx = TrialContext(session_id, trial_id, mode, source, calibration_version=calibration_version,
                       link="none" if mode == "DRY_RUN" else ("virtual" if mode == "LOOPBACK" else
                                                              "serial"),
                       config={"camera_config_hash": cfg.config_hash, "camera": cfg.raw})
    w = StreamWriter(trial_dir / f"{stream_file}.jsonl", "observation", ctx, units=UNITS,
                     coordinate_frame=FRAME,
                     header_extra={"opencv": cv2.__version__, "camera_config_hash": cfg.config_hash,
                                   "capture_backend": cfg.capture_backend,
                                   "capture_time_note": "null: OpenCV exposes no exposure time"})
    return w, trial_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m motion_io_lab observe",
        description="Observe a coloured marker in a camera or video file. Observation only; "
                    "never drives hardware. Host time is not exposure time.")
    ap.add_argument("--config", required=True, help="camera config JSON (examples/camera_config.json)")
    ap.add_argument("--out", required=True, help="output root; a <trial-id>/ directory is created")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--trial-id", required=True)
    ap.add_argument("--mode", default="DRY_RUN", choices=["DRY_RUN", "LOOPBACK", "HARDWARE_ARMED", "REPLAY"],
                    help="mode of the trial this observation belongs to (default DRY_RUN)")
    ap.add_argument("--source", default=None, choices=list(SOURCES),
                    help="label for the log (default: recorded)")
    ap.add_argument("--video", default=None, help="video file; overrides config.source")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--save-video", action="store_true", help="also save capture.mp4 (live only)")
    ap.add_argument("--calibration", default=None,
                    help="calibration JSON (from `calibrate`); its version goes into every log row")
    a = ap.parse_args(argv)
    if not 0 < a.seconds <= 600:
        ap.error("--seconds must be in (0, 600]")
    try:
        cfg = load_config(a.config)
        if a.video:
            cfg = parse_config({**cfg.raw, "source": a.video})
    except (ConfigError, OSError) as exc:
        ap.error(str(exc))
    try:
        cal_version = None
        if a.calibration:
            from . import calibration

            try:
                cal_version = calibration.load(a.calibration).version
            except (ValueError, OSError, KeyError) as exc:
                ap.error(f"--calibration {a.calibration}: {exc}")
        writer, trial_dir = make_writer(Path(a.out), a.session_id, a.trial_id, cfg, a.mode,
                                        a.source or "recorded", calibration_version=cal_version)
    except FileExistsError as exc:
        ap.error(f"{exc}; use a new --trial-id")
    live = isinstance(cfg.source, int)
    vid = (trial_dir / "capture.mp4") if (a.save_video and live) else None
    try:
        with writer:
            summary = observe(cfg, writer, seconds=None if not live else a.seconds,
                              max_frames=a.max_frames, show=a.show, save_video=vid)
    except (ConfigError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    (trial_dir / "observation_summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                        encoding="utf-8")
    write_run_manifest(trial_dir, "observe", vars(a), source=a.source or "recorded")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
