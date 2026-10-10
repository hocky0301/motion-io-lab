import json

import numpy as np
import pytest

from motion_io_lab import calibration
from motion_io_lab.motions import (MOTIONS, Motion, load_motion, load_motion_file, sample_motion,
                                   write_motion_file)
from motion_io_lab.shaping import Shaper, x_to_pulse


def test_clamp_and_flags():
    s = Shaper(1450, 1550, None)
    assert s.shape(1600, 0.04) == (1550, "clamp")
    assert s.shape(1400, 0.04) == (1450, "clamp")
    assert s.shape(1500, 0.04) == (1500, "none")


def test_slew_limit_is_per_second_not_per_call():
    s = Shaper(1450, 1550, 400.0)
    s.reset(1500)
    assert s.shape(1550, 0.04) == (1516, "slew")      # 400 us/s * 0.04 s = 16 us
    assert s.shape(1550, 0.02) == (1524, "slew")
    s.reset(1500)
    assert s.shape(1510, 0.04) == (1510, "none")


def test_clamp_plus_slew_flag_and_no_overshoot():
    s = Shaper(1450, 1550, 400.0)
    s.reset(1540)
    v, f = s.shape(1700, 0.04)
    assert v == 1550 and f == "clamp+slew" or (v == 1550 and f == "clamp")
    for _ in range(20):
        v, _ = s.shape(1700, 0.04)
        assert v <= 1550


def test_host_range_must_be_inside_the_device_range():
    with pytest.raises(ValueError):
        Shaper(1400, 1550)
    with pytest.raises(ValueError):
        Shaper(1450, 1600)
    with pytest.raises(ValueError):
        Shaper(1500, 1450)
    with pytest.raises(ValueError):
        Shaper(max_slew_us_per_s=0)


def test_nonfinite_input_is_an_error_not_a_pulse():
    with pytest.raises(ValueError):
        Shaper().shape(float("nan"), 0.04)
    with pytest.raises(ValueError):
        x_to_pulse(float("inf"))


def test_x_to_pulse_maps_and_clips():
    assert x_to_pulse(0) == 1500 and x_to_pulse(1) == 1540 and x_to_pulse(-1) == 1460
    assert x_to_pulse(5) == 1540


@pytest.mark.parametrize("name", sorted(MOTIONS))
def test_motions_start_and_end_at_rest_and_stay_in_range(name):
    t, x = sample_motion(name, 50.0)
    assert abs(x[0]) < 1e-9 and abs(x[-1]) < 1e-9
    assert np.abs(x).max() <= 1.0
    assert np.isclose(np.diff(t), 20.0).all()
    biggest = np.abs(np.diff(x)).max()
    if name == "step_hold":
        assert biggest > 0.35       # deliberately steeper than the host slew limit allows
    else:
        assert biggest < 0.35       # smooth: no jump between 20 ms samples


def test_look_wait_nod_has_the_three_phases():
    t, x = sample_motion("look_wait_nod", 50.0)
    i = lambda ms: int(ms / 20)
    assert x[i(500)] > 0.65 and abs(x[i(750)] - 0.7) < 1e-9          # turned and waiting
    assert x[i(1000):i(1600)].min() < 0.55 and x[i(1000):i(1600)].max() > 0.85   # nodding +-0.2


def test_motion_validation():
    with pytest.raises(ValueError):
        Motion("m", np.array([0., 1.]), np.array([1500.]), "x")
    with pytest.raises(ValueError):
        Motion("m", np.array([0., 0.]), np.array([1500., 1500.]), "x")
    with pytest.raises(ValueError):
        Motion("m", np.array([0., 1.]), np.array([1500., np.nan]), "x")


def test_resample_segment_speed_and_bounds():
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1520.]), "x")
    t, u = m.resample(100.0)
    assert len(t) == 11 and u[0] == 1500 and u[-1] == 1520
    t, u = m.resample(100.0, start_ms=200, end_ms=600)
    assert len(t) == 5 and u[0] == pytest.approx(1504) and u[-1] == pytest.approx(1512)
    t, u = m.resample(100.0, speed=0.5)           # half speed = twice as long
    assert t[-1] == 1900.0 or t[-1] == 2000.0 or len(t) > 11
    with pytest.raises(ValueError):
        m.resample(100.0, start_ms=900, end_ms=1500)
    with pytest.raises(ValueError):
        m.resample(100.0, speed=0)


def test_motion_file_roundtrip_and_errors(tmp_path):
    t, x = sample_motion("sweep", 50.0)
    p = tmp_path / "m.jsonl"
    write_motion_file(p, t, 1500 + 40 * x, "sweep", "test")
    m = load_motion(p)
    assert m.name == "sweep" and len(m.t_ms) == len(t)
    with pytest.raises(FileExistsError):
        write_motion_file(p, t, x, "sweep", "test")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"schema_version": "motion-io-lab/motion-v1", "record_type": "header",
                               "units": "deg"}) + "\n")
    with pytest.raises(ValueError, match="units"):
        load_motion_file(bad)
    bad.write_text('{"t_ms": 0}\n')
    with pytest.raises(ValueError, match="bad.jsonl:1"):
        load_motion_file(bad)
    bad.write_text("not json\n")
    with pytest.raises(ValueError, match="bad.jsonl:1"):
        load_motion_file(bad)
    bad.write_text("")
    with pytest.raises(ValueError, match="no samples"):
        load_motion_file(bad)


def test_calibration_fit_and_version(tmp_path):
    pts = [(1450, -4.6), (1475, -2.2), (1500, 0.1), (1525, 2.6), (1550, 4.9)]
    cal = calibration.fit(pts, "hardware")
    assert cal.deg_per_us == pytest.approx(0.0955, abs=1e-3)
    assert cal.rms_residual_deg < 0.2
    assert cal.pulse_to_deg(1500) == pytest.approx(cal.offset_deg)
    assert len(cal.version) == 8
    p = tmp_path / "c.json"
    p.write_text(cal.text)
    assert calibration.load(p).version == cal.version


def test_calibration_rejects_bad_data(tmp_path):
    with pytest.raises(ValueError, match="at least 3"):
        calibration.fit([(1500, 0), (1510, 1)], "hardware")
    with pytest.raises(ValueError, match="distinct"):
        calibration.fit([(1500, 0), (1500, 1), (1500, 2)], "hardware")
    with pytest.raises(ValueError, match="increase"):
        calibration.fit([(1450, 5), (1500, 0), (1550, -5)], "hardware")
    with pytest.raises(ValueError):
        calibration.fit([(1450, float("nan")), (1500, 0), (1550, 5)], "hardware")
    c = tmp_path / "c.csv"
    c.write_text("pulse_us,angle_deg\n1450,-5\n1500,x\n1550,5\n")
    with pytest.raises(ValueError, match="c.csv:3"):
        calibration.fit_csv(c)
    c.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="columns"):
        calibration.fit_csv(c)
