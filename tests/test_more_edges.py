"""Edges that the separately prepared set of deliberate defects (scripts/mutants_independent.py) got past."""
import os
import pty
import time

import numpy as np
import pytest

from motion_io_lab import analysis as an
from motion_io_lab import calibration
from motion_io_lab.cli import main
from motion_io_lab.demo import synthetic_calibration
from motion_io_lab.inputs import KeyboardSource
from motion_io_lab.link import PtyDeviceServer, SerialLink
from motion_io_lab.motions import MOTIONS
from motion_io_lab.reference import compare
from motion_io_lab.session_report import analyze_motion, still_marker_sd_deg
from motion_io_lab.shaping import Shaper
from motion_io_lab.triallog import StreamWriter, TrialContext

from tests.test_demo import demo  # noqa: F401  (the module fixture that runs the demo once)
from tests.test_inputs import FakeTTY


def test_the_slew_limit_also_holds_for_a_short_period():
    sh = Shaper(1450, 1550, 400.0)
    sh.reset(1500.0)
    pulse, flags = sh.shape(1550.0, 0.005)             # a 5 ms period: 2 us per step at 400 us/s
    assert pulse == 1502 and "slew" in flags


def test_a_lag_needs_enough_overlapping_samples():
    ref = np.full(300, np.nan)
    other = np.full(300, np.nan)
    ref[100:110] = np.sin(np.arange(10))                # 10 valid samples only: too few to correlate
    other[100:110] = np.sin(np.arange(10))
    r = an.estimate_lag_ms(ref, other)
    assert r["lag_ms"] is None and "not enough" in r["note"]


def test_a_nan_is_never_close_to_a_reference_number():
    assert compare({"a": {"rms_deg_observed": float("nan")}}, {"a": {"rms_deg_observed": 0.1}})
    assert compare({"a": float("nan")}, {"a": 0.1}, strict=True)
    assert compare({"a": float("inf")}, {"a": 0.1})


def test_a_calibration_needs_three_distinct_points():
    with pytest.raises(ValueError, match="at least 3"):
        calibration.fit([(1450, -5.0), (1550, 5.0)], source="hardware")
    with pytest.raises(ValueError, match="3 distinct"):
        calibration.fit([(1450, -5.0), (1450, -5.1), (1550, 5.0)], source="hardware")


def test_the_keyboard_axis_stops_at_plus_and_minus_one():
    master, slave = pty.openpty()
    try:
        with KeyboardSource(step=0.1, stream=FakeTTY(slave)) as src:
            os.write(master, b"d" * 14 + b"a" * 30)
            time.sleep(0.05)
            vals = [e.value for e in src.poll(0)]
        assert max(vals) == 1.0 and min(vals) == -1.0 and all(-1.0 <= v <= 1.0 for v in vals)
    finally:
        os.close(master)
        os.close(slave)


def test_the_return_error_compares_the_end_of_the_run_with_its_start(demo, monkeypatch):  # noqa: F811
    out, _ = demo
    calls = []
    real = an.window_mean

    def spy(curve, grid, lo, hi):
        calls.append((lo, hi))
        return real(curve, grid, lo, hi)
    monkeypatch.setattr(an, "window_mean", spy)
    analyze_motion(out, "sweep", 5, synthetic_calibration())
    dur = MOTIONS["sweep"][1] * 1000.0
    assert (0, 100) in calls and (dur + 200, dur + 500) in calls


class _FakeSerialModule:
    class SerialException(Exception):
        pass


class _FakeSerial:
    def __init__(self, fail_reads=False):
        self.reset_calls = 0
        self.fail_reads = fail_reads

    def reset_input_buffer(self):
        self.reset_calls += 1

    @property
    def in_waiting(self):
        if self.fail_reads:
            raise OSError("device not configured")
        return 0

    def close(self):
        pass


def test_opening_a_port_drops_whatever_the_board_said_while_it_reset(monkeypatch):
    import serial
    made = []

    def fake_serial(*a, **k):
        made.append(_FakeSerial())
        return made[-1]
    monkeypatch.setattr(serial, "Serial", fake_serial)
    SerialLink("/dev/fake", settle_s=0.0)
    assert made[0].reset_calls == 1


def test_a_vanished_port_is_a_link_down_on_read(monkeypatch):
    import serial
    from motion_io_lab.link import LinkDown
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: _FakeSerial(fail_reads=True))
    link = SerialLink("/dev/fake", settle_s=0.0)
    with pytest.raises(LinkDown, match="device not configured"):
        link.read_lines()


def test_a_non_ascii_byte_on_the_wire_ruins_that_line_only():
    with PtyDeviceServer(seed=2) as srv:
        link = SerialLink(srv.path, settle_s=0.0)
        link.ser.write(b"\xffSTATUS\n")
        link.ser.write(b"STATUS\n")
        t0 = time.monotonic()
        got = []
        while time.monotonic() - t0 < 2 and len(got) < 2:
            got += [t for _, t in link.read_lines()]
            time.sleep(0.01)
        link.close()
    assert any(l.startswith("REJECTED LINE reason=bad_line") for l in got)
    assert any(l.startswith("STATUS") for l in got)


def test_the_still_marker_spread_uses_n_minus_one(tmp_path):
    values = [0.10, -0.10] * 6                          # normalised units, scale 0.1 rad
    ctx = TrialContext("s", "still", "DRY_RUN", "recorded", link="none",
                       config={"camera": {"angle_scale_rad": 0.1}})
    with StreamWriter(tmp_path / "still" / "observation.jsonl", "observation", ctx, units="norm_angle",
                      coordinate_frame="camera_image") as w:
        for i, v in enumerate(values):
            w.write("marker", frame_id=i, media_pos_ms=None, source_kind="live", observed_value=v,
                    cx_px=1.0, cy_px=1.0, area_px=50.0, n_blobs=1, invalid_reason=None, valid=True,
                    received_monotonic_ns=1000 + i, host_before_read_ns=999 + i, processed_monotonic_ns=1000 + i)
    deg = np.degrees(0.1) * np.array(values)
    assert still_marker_sd_deg(tmp_path, "still") == pytest.approx(float(np.std(deg, ddof=1)))
    with pytest.raises(ValueError, match="fewer than 10"):
        w2 = StreamWriter(tmp_path / "few" / "observation.jsonl", "observation",
                          TrialContext("s", "few", "DRY_RUN", "recorded", link="none",
                                       config={"camera": {"angle_scale_rad": 0.1}}),
                          units="norm_angle", coordinate_frame="camera_image")
        w2.close()
        still_marker_sd_deg(tmp_path, "few")


def test_the_demo_wants_at_least_two_replays(capsys):
    with pytest.raises(SystemExit) as e:
        main(["demo", "--out", "unused-dir", "--repeats", "1"])
    assert e.value.code == 2 and "at least 2" in capsys.readouterr().err


def test_the_calibration_version_reaches_every_log_row(tmp_path):
    from motion_io_lab.triallog import load_trial
    cal = synthetic_calibration()
    p = tmp_path / "cal.json"
    p.write_text(cal.text)
    assert main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "r", "--source",
                 "script:sweep", "--calibration", str(p)]) == 0
    assert main(["play", "--motion", str(tmp_path / "r"), "--out", str(tmp_path), "--session-id", "s",
                 "--trial-id", "p", "--calibration", str(p)]) == 0
    for tid in ("r", "p"):
        t = load_trial(tmp_path / tid)
        for s in t.streams.values():
            assert s.header["calibration_version"] == cal.version
            assert all(r["calibration_version"] == cal.version for r in s.records)
    assert main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "r2", "--source",
                 "script:sweep"]) == 0
    assert load_trial(tmp_path / "r2").stream("command").header["calibration_version"] is None
    with pytest.raises(SystemExit):
        main(["play", "--motion", str(tmp_path / "r"), "--out", str(tmp_path), "--session-id", "s",
              "--trial-id", "p3", "--calibration", str(tmp_path / "none.json")])
