"""Capture a camera or video file and log frame timing. No servo output.

Host timestamps are NOT exposure time (``capture_time_ns`` stays null, provenance
``unknown``). For a video file only ``media_pos_ms`` is meaningful; the host times are
decode times. Output goes to ``<out>/<trial-id>/observation.jsonl`` (record_type "frame");
an existing trial is never overwritten.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .manifest import write_run_manifest
from .triallog import SOURCES, StreamWriter, TrialContext


def list_cameras(max_index: int = 5) -> list[dict]:
    """Try camera indices 0..max_index-1. Opening a camera may trigger the OS permission prompt."""
    import cv2

    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            found.append({"index": i, "reads_frame": bool(ok),
                          "size": None if not ok else [int(frame.shape[1]), int(frame.shape[0])]})
        cap.release()
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motion_io_lab camera-probe", description=__doc__)
    ap.add_argument("--list", action="store_true", help="list camera indices that open, then exit")
    ap.add_argument("--source", default="0", help="camera index or local video file")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--out", default="outputs/camera")
    ap.add_argument("--session-id", default="probe")
    ap.add_argument("--trial-id", default=None)
    ap.add_argument("--mode", default="DRY_RUN", choices=["DRY_RUN", "LOOPBACK", "HARDWARE_ARMED", "REPLAY"])
    ap.add_argument("--source-label", default="recorded", choices=list(SOURCES))
    ap.add_argument("--save-avi", action="store_true", help="also write video.avi (MJPG, 30 fps container)")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for c in list_cameras():
            print(c)
        return 0
    if not 1 <= a.frames <= 100000:
        ap.error("--frames must be 1..100000")
    if a.trial_id is None:
        ap.error("--trial-id is required (a new name; trials are never overwritten)")
    is_file = not a.source.isdecimal()
    if is_file and not Path(a.source).is_file():
        ap.error(f"video file not found: {a.source}")
    import cv2

    ctx = TrialContext(a.session_id, a.trial_id, a.mode, a.source_label,
                       link="none" if a.mode == "DRY_RUN" else ("virtual" if a.mode == "LOOPBACK" else "serial"),
                       config={"frames_requested": a.frames})
    tdir = Path(a.out) / a.trial_id
    try:
        w = StreamWriter(tdir / "observation.jsonl", "observation", ctx, units="n/a",
                         coordinate_frame="camera_image",
                         header_extra={"opencv": cv2.__version__,
                                       "capture_time_note": "null: OpenCV exposes no exposure time"})
    except FileExistsError as exc:
        ap.error(f"{exc}; use a new --trial-id")
    cap = cv2.VideoCapture(a.source if is_file else int(a.source))
    if not cap.isOpened():
        w.abort()
        print("error: source cannot be opened. For a camera, check the OS permission and the index "
              "(--list).", file=sys.stderr)
        return 2
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    writer = None
    n = 0
    stop_reason = "frame_limit"
    try:
        with w:
            while n < a.frames:
                before = time.monotonic_ns()
                ok, frame = cap.read()
                after = time.monotonic_ns()
                if not ok:
                    stop_reason = "read_failed_or_end_of_file"
                    break
                if a.save_avi and writer is None:
                    writer = cv2.VideoWriter(str(tdir / "video.avi"), cv2.VideoWriter_fourcc(*"MJPG"),
                                             30.0, (frame.shape[1], frame.shape[0]))
                if writer is not None:
                    writer.write(frame)
                mpos = (n * 1000.0 / fps) if (is_file and fps > 0) else None
                w.write("frame", frame_id=n, source_kind="file" if is_file else "live",
                        media_pos_ms=mpos, host_before_read_ns=before, host_after_read_ns=after,
                        received_monotonic_ns=after, processed_monotonic_ns=after,
                        width=int(frame.shape[1]), height=int(frame.shape[0]))
                n += 1
                if a.preview:
                    cv2.imshow("CAMERA ONLY - no hardware actuation", frame)
                    if cv2.waitKey(1) & 255 == ord("q"):
                        stop_reason = "quit_key"
                        break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if a.preview:
            cv2.destroyAllWindows()
    write_run_manifest(tdir, "camera-probe", vars(a), source=a.source_label)
    print(f"{n} frames ({stop_reason}). Host times are arrival times, not exposure times; the AVI "
          "container fps is not the capture rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
