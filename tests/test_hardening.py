"""Regression tests: estimator limits, sequence numbers, manifests, size limits."""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import motion_io_lab
from motion_io_lab import analysis as an
from motion_io_lab import calibration, observe as obs
from motion_io_lab.devclient import DeviceClient, INBOX_MAX
from motion_io_lab.link import MAX_LINE_BUFFER, SerialLink, SimWorld
from motion_io_lab.motions import Motion
from motion_io_lab.player import PlayConfig, build_plan, play
from motion_io_lab.triallog import TrialContext, load_trial
from motion_io_lab.trialio import open_trial, resolve

REPO = Path(motion_io_lab.__file__).resolve().parent.parent


def _wave(n=400, delay=0):
    """One smooth bump (not periodic, so a too-long delay cannot alias onto another peak)."""
    t = np.arange(n) * 10.0
    return np.roll(np.exp(-((t - 1500) / 250.0) ** 2), delay)


def test_lag_beyond_the_search_window_is_not_reported_as_the_edge_value():
    ref = _wave()
    ok = an.estimate_lag_ms(ref, _wave(delay=39))
    assert ok["lag_ms"] == pytest.approx(390.0, abs=3.0)
    for delay in (45, 70, 100):                        # 450, 700, 1000 ms: outside +-400 ms
        r = an.estimate_lag_ms(ref, _wave(delay=delay))
        assert r["lag_ms"] is None and r["at_search_limit"] is True
    assert an.summarize_values([None, 12.0, None])["n"] == 1      # a None lag is left out of a mean


def test_a_dropped_late_row_has_no_sequence_number_and_sent_seqs_are_unique(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1520.]), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)

    def stall(i, when):
        if i == 5:
            w.advance_to(w.now_ns() + 100_000_000)
    mode, lk, src = resolve("play", "loopback")
    ctx = TrialContext("s", "late", mode, src, link=lk)
    with open_trial(tmp_path, ctx, with_input=False) as t:
        play(m, PlayConfig(drop_if_late_ms=60, valid_for_ms=200), w, t, before_send=stall)
    sets = load_trial(tmp_path / "late").stream("command").of("set")
    dropped = [r for r in sets if not r["valid"]]
    sent = [r["seq"] for r in sets if r["valid"]]
    assert dropped and all(r["seq"] is None for r in dropped)
    assert len(sent) == len(set(sent))
    idx = [r["planned_index"] for r in sets]
    assert idx == sorted(idx) and len(set(idx)) == len(idx)       # every planned sample appears once


def test_manifest_reports_the_commit_only_for_its_own_checkout(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not installed")
    pkg = REPO / "motion_io_lab"
    # (a) the package copied into the middle of someone else's repo
    other = tmp_path / "other"
    (other / "vendor").mkdir(parents=True)
    shutil.copytree(pkg, other / "vendor" / "motion_io_lab", ignore=shutil.ignore_patterns("__pycache__"))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.com",
                                                 "commit", "-q", "-m", "x"]):
        subprocess.run([git, "-C", str(other), *cmd], check=True, capture_output=True)
    code = "from motion_io_lab.manifest import git_commit, git_dirty; print(git_commit(), git_dirty())"
    out = subprocess.run([sys.executable, "-c", code], cwd=other / "vendor", capture_output=True,
                         text=True, env={"PYTHONPATH": str(other / "vendor"), "PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == "None None", out.stderr
    # (b) the package at the top level of its own checkout: the commit, and dirty after an edit
    own = tmp_path / "own"
    own.mkdir()
    shutil.copytree(pkg, own / "motion_io_lab", ignore=shutil.ignore_patterns("__pycache__"))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.com",
                                                 "commit", "-q", "-m", "x"]):
        subprocess.run([git, "-C", str(own), *cmd], check=True, capture_output=True)
    env = {"PYTHONPATH": str(own), "PATH": "/usr/bin:/bin"}
    clean = subprocess.run([sys.executable, "-c", code], cwd=own, capture_output=True, text=True, env=env)
    commit, dirty = clean.stdout.split()
    assert len(commit) == 40 and dirty == "False", clean.stderr
    (own / "motion_io_lab" / "timing.py").write_text("# edited\n", encoding="utf-8")
    again = subprocess.run([sys.executable, "-c", code], cwd=own, capture_output=True, text=True, env=env)
    assert again.stdout.split()[1] == "True"


def test_calibration_text_never_contains_a_negative_zero():
    rng = np.random.default_rng(0)
    for _ in range(50):
        slope = float(rng.uniform(0.05, 0.2))
        pts = [(p, (p - 1500) * slope) for p in (1450, 1475, 1500, 1525, 1550)]
        cal = calibration.fit(pts, source="synthetic")
        assert "-0.0," not in cal.text and '": -0.0\n' not in cal.text


def test_a_plan_longer_than_the_limit_is_refused_unless_asked_for():
    m = Motion("m", np.array([0., 600_000.]), np.array([1500., 1520.]), "t")     # t_ms in microseconds?
    with pytest.raises(ValueError, match="allow-long"):
        build_plan(m, PlayConfig())
    plan = build_plan(m, PlayConfig(max_duration_ms=None))
    assert plan.t_ms[-1] >= 600_000


class _FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.in_waiting = 0

    def read(self, n):
        data = self.chunks.pop(0)
        return data

    def close(self):
        pass


def test_serial_noise_without_line_ends_does_not_grow_the_buffer_forever():
    link = SerialLink.__new__(SerialLink)
    link._serial_mod = __import__("serial")
    link._buf = b""
    link.ser = _FakeSerial([b"x" * 1000] * 10 + [b"HELLO proto=2\n"])
    for _ in range(10):
        link.ser.in_waiting = 1000
        link.read_lines()
        assert len(link._buf) <= MAX_LINE_BUFFER + 1000
    link.ser.in_waiting = 14
    lines = [t for _, t in link.read_lines()]
    assert lines and lines[-1].endswith("HELLO proto=2")      # the stream still recovers
    assert len(link._buf) <= MAX_LINE_BUFFER + 1000


def test_the_client_inbox_is_trimmed_and_replies_are_still_matched():
    w = SimWorld(seed=1, wake_jitter_ms=None)
    c = DeviceClient(w)
    for i in range(INBOX_MAX + 500):
        w._down.append((w.now_ns(), i, f"EXECUTED SET seq={i + 1} applied_us=1500"))
    w._down.sort()
    c.pump()
    assert len(c.inbox) < INBOX_MAX
    c.hello()                                              # _await still sees the reply that follows
    assert c.epoch is not None


def _red_frame(h_bgr):
    import cv2
    frame = np.full((120, 160, 3), 90, np.uint8)
    cv2.circle(frame, (80, 60), 12, h_bgr, -1)
    return frame


def test_a_hue_range_may_wrap_through_zero_for_a_red_marker():
    base = {"source": "x.mp4", "min_area_px": 40, "max_area_px": 10000}
    wrap = obs.parse_config({**base, "hsv_lower": [170, 100, 100], "hsv_upper": [10, 255, 255]})
    plain = obs.parse_config({**base, "hsv_lower": [35, 80, 70], "hsv_upper": [85, 255, 255]})
    for bgr in ((0, 0, 255), (60, 0, 255)):                # H = 0 and H ~ 175: both are "red"
        det = obs.detect_marker(_red_frame(bgr), wrap)
        assert det.reason is None and abs(det.cx - 80) < 1 and abs(det.cy - 60) < 1
        assert obs.detect_marker(_red_frame(bgr), plain).reason == "none_found"
    green = _red_frame((0, 200, 0))
    assert obs.detect_marker(green, wrap).reason == "none_found"
    with pytest.raises(obs.ConfigError):                   # the wrap is for hue only
        obs.parse_config({**base, "hsv_lower": [0, 200, 0], "hsv_upper": [10, 100, 255]})
