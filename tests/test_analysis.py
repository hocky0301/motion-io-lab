import numpy as np
import pytest

from motion_io_lab import analysis as an
from motion_io_lab.calibration import fit
from motion_io_lab.triallog import StreamWriter, TrialContext, load_trial

G = np.arange(0.0, 3000.0, 10.0)


def bump(t, c=1000.0, w=300.0):
    return np.exp(-0.5 * ((t - c) / w) ** 2)


@pytest.mark.parametrize("lag", [0.0, 40.0, 87.0, 123.0, -50.0])
def test_lag_estimate_recovers_a_known_shift(lag):
    ref = bump(G) + 0.3 * bump(G, 2000, 200)
    other = bump(G - lag) + 0.3 * bump(G - lag, 2000, 200)
    est = an.estimate_lag_ms(ref, other)
    assert est["lag_ms"] == pytest.approx(lag, abs=3.0)
    assert est["peak_corr"] > 0.99


def test_lag_estimate_ignores_scale_and_offset_and_nan_gaps():
    ref = bump(G)
    other = 5.0 * bump(G - 60) + 2.0
    other[100:110] = np.nan
    assert an.estimate_lag_ms(ref, other)["lag_ms"] == pytest.approx(60, abs=3)


def test_lag_estimate_says_so_when_it_cannot():
    est = an.estimate_lag_ms(np.zeros(100), np.zeros(100))
    assert est["lag_ms"] is None
    est = an.estimate_lag_ms(np.full(100, np.nan), bump(G[:100]))
    assert est["lag_ms"] is None


def test_on_grid_zero_order_hold_and_gap_to_nan():
    t = np.array([0.0, 100.0, 200.0, 800.0])
    v = np.array([1.0, 2.0, 3.0, 4.0])
    g = np.array([-10.0, 0.0, 50.0, 150.0, 250.0, 799.0, 900.0])
    zoh = an.on_grid(t, v, g, hold=True)
    assert np.isnan(zoh[0]) and list(zoh[1:6]) == [1.0, 1.0, 2.0, 3.0, 3.0]
    lin = an.on_grid(t, v, np.array([50.0, 150.0, 500.0, 790.0, 900.0]), max_gap_ms=120)
    assert lin[0] == pytest.approx(1.5) and lin[1] == pytest.approx(2.5)
    assert np.isnan(lin[2])                        # in the middle of a 600 ms hole: no straight line
    assert lin[3] == pytest.approx(3.983, abs=0.01)          # near the next sample: bridged
    assert np.isnan(lin[4])                                  # after the last sample: no extrapolation
    assert np.isnan(an.on_grid(np.array([]), np.array([]), g)).all()


def test_repeat_stats_pooled_sd_is_the_spread_across_repeats():
    rng = np.random.default_rng(0)
    base = np.sin(G / 300.0)
    curves = [base + rng.normal(0, 0.1, len(G)) for _ in range(40)]
    st = an.repeat_stats(curves)
    assert st["pooled_sd"] == pytest.approx(0.1, rel=0.1)
    assert np.allclose(st["mean_curve"], base, atol=0.1)
    one = an.repeat_stats([base])
    assert one["pooled_sd"] is None                      # one repeat has no spread


def test_repeat_stats_handles_nan_gaps_without_warnings(recwarn):
    a, b, c = np.ones(5), np.ones(5) * 2, np.ones(5) * 3
    a[2] = np.nan
    b[2] = np.nan
    st = an.repeat_stats([a, b, c])
    assert np.isnan(st["sd_curve"][2]) and st["sd_curve"][0] == pytest.approx(1.0)
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_summarize_and_window_mean_and_rms():
    s = an.summarize_values([1.0, 2.0, 3.0, None, float("nan")])
    assert s["n"] == 3 and s["mean"] == 2.0 and s["sd"] == 1.0
    assert an.summarize_values([])["mean"] is None
    assert an.rms(np.array([3.0, 4.0, np.nan])) == pytest.approx(np.sqrt(12.5))
    assert np.isnan(an.rms(np.array([np.nan])))
    assert an.window_mean(np.array([1.0, 2.0, 3.0]), np.array([0.0, 10.0, 20.0]), 5, 25) == 2.5


def build_trial(tmp_path, file_obs=True, offset_ns=0):
    """Hand-made trial with exactly known timestamps."""
    ctx = TrialContext("s", "t", "REPLAY", "synthetic", link="virtual", calibration_version=None)
    d = tmp_path / "t"
    d.mkdir()
    t0 = 5_000_000_000
    with StreamWriter(d / "command.jsonl", "command", ctx, units="pulse_us", coordinate_frame="f") as w:
        ctx.session_epoch = 7
        for i in range(10):
            ns = t0 + i * 40_000_000
            w.write("set", seq=i + 1, desired_pulse_us=1500.0 + i, commanded_pulse_us=1500 + i,
                    valid_for_ms=100, scheduled_monotonic_ns=ns, sent_monotonic_ns=ns + 1000,
                    processed_monotonic_ns=ns, shaping="none")
            w.write("reply", reply_status="EXECUTED", reply_reason=None, reply_to_seq=i + 1,
                    reply_cmd="SET", device_millis=i, device_applied_pulse_us=1500 + i,
                    raw="x", received_monotonic_ns=ns + 12_000_000, processed_monotonic_ns=ns)
        w.write("set", seq=99, desired_pulse_us=1.0, commanded_pulse_us=1500, valid_for_ms=100,
                scheduled_monotonic_ns=t0 + 10**9, sent_monotonic_ns=None, processed_monotonic_ns=None,
                shaping="none", valid=False, drop_reason="late")
    octx = TrialContext("s", "t", "REPLAY", "rendered_video" if file_obs else "hardware", link="virtual")
    if not file_obs:
        octx = TrialContext("s", "t", "REPLAY", "synthetic", link="virtual")
    with StreamWriter(d / "observation.jsonl", "observation", octx, units="norm_angle",
                      coordinate_frame="camera_image",
                      header_extra=None) as w:
        octx.config = {}
        for k in range(5):
            common = dict(frame_id=k, cx_px=1.0, cy_px=2.0, area_px=3.0, n_blobs=1, invalid_reason=None,
                          observed_value=0.1 * k)
            if file_obs:
                w.write("marker", media_pos_ms=100.0 * k, source_kind="file", received_monotonic_ns=9, **common)
            else:
                w.write("marker", media_pos_ms=None, source_kind="live",
                        received_monotonic_ns=t0 + 1000 + k * 50_000_000 + offset_ns, **common)
        w.write("marker", frame_id=5, media_pos_ms=500.0, source_kind="file" if file_obs else "live",
                observed_value=None, cx_px=None, cy_px=None, area_px=None, n_blobs=0,
                invalid_reason="none_found", valid=False, received_monotonic_ns=10 ** 12)
    return load_trial(d), t0


def test_extract_uses_the_first_sent_command_as_time_zero_and_keeps_series_apart(tmp_path):
    tr, t0 = build_trial(tmp_path)
    ts = an.extract(tr)
    assert ts.t0_ns == t0 + 1000
    assert len(ts.commanded[0]) == 10                   # the dropped (not sent) command is excluded
    assert ts.commanded[0][1] == pytest.approx(40.0)
    assert ts.applied[0][0] == pytest.approx((12_000_000 - 1000) / 1e6)
    assert list(ts.commanded[1][:3]) == [1500, 1501, 1502]
    assert list(ts.applied[1][:3]) == [1500, 1501, 1502]
    assert ts.obs_total == 6 and ts.obs_invalid == {"none_found": 1}
    assert list(ts.observed[0]) == [0, 100, 200, 300, 400]      # file: media time, not host time
    assert "assumed" in ts.notes[0]
    assert ts.angle_scale_deg is None


def test_media_offset_moves_the_video_on_the_command_timeline(tmp_path):
    tr, _ = build_trial(tmp_path)
    assert list(an.extract(tr, media_offset_ms=-70.0).observed[0]) == [-70, 30, 130, 230, 330]


def test_live_observations_are_placed_by_host_receive_time(tmp_path):
    tr, t0 = build_trial(tmp_path, file_obs=False)
    ts = an.extract(tr)
    assert ts.observed[0][0] == pytest.approx(0.0, abs=0.01)
    assert ts.observed[0][1] == pytest.approx(50.0, abs=0.01)
    assert not ts.notes


def test_to_deg_needs_a_calibration_and_never_mixes_units(tmp_path):
    tr, _ = build_trial(tmp_path)
    ts = an.extract(tr)
    out = an.to_deg(ts, None, np.arange(0, 400.0, 10))
    assert "commanded_deg" not in out and "observed_deg" not in out and "observed_norm" in out
    cal = fit([(1450, -5), (1500, 0), (1550, 5)], "synthetic")
    out = an.to_deg(ts, cal, np.arange(0, 400.0, 10))
    assert out["commanded_deg"][0] == pytest.approx(0.0)
    assert out["commanded_deg"][4] == pytest.approx(0.1)          # 1501 us at 0.1 deg/us, sent at 40 ms
