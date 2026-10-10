"""The observation path is tested without a camera: a moving marker is drawn, encoded to a
video file, read back and detected."""
import json
import math

import numpy as np
import pytest

from motion_io_lab import camera_probe, observe as obs
from motion_io_lab.synth import VideoSpec, render_video
from motion_io_lab.triallog import load_trial

SPEC = VideoSpec()


def cfg_dict(src="x.mp4", **kw):
    d = SPEC.camera_config(src)
    d.update(kw)
    return d


def make_video(tmp_path, dropout=0.0, distractor=0.0, latency=0.0, seconds=2.0, name="v.mp4"):
    t = np.linspace(0, seconds, int(seconds * 1000) + 1)
    ang = 4.0 * np.sin(2 * np.pi * t / seconds)                 # degrees
    p = tmp_path / name
    truth = render_video(p, t, ang, seconds, SPEC, latency_s=latency, seed=3, dropout_p=dropout,
                         distractor_p=distractor)
    return p, truth


def run_observe(tmp_path, video, trial="o1", **kw):
    cfg = obs.parse_config(cfg_dict(str(video)))
    w, d = obs.make_writer(tmp_path / "out", "s", trial, cfg, "DRY_RUN", "rendered_video")
    with w:
        summary = obs.observe(cfg, w, **kw)
    return load_trial(d), summary


def test_rendered_video_self_test_recovers_the_true_angle(tmp_path):
    video, truth = make_video(tmp_path)
    tr, summary = run_observe(tmp_path, video)
    rows = tr.stream("observation").of("marker")
    assert len(rows) == len(truth["true_angle_deg"]) == summary["frames"]
    assert all(r["valid"] for r in rows)
    got = np.array([r["observed_value"] for r in rows])
    want = np.radians(truth["true_angle_deg"]) / SPEC.angle_scale_rad
    err = got - want
    assert math.sqrt(np.mean(err ** 2)) < 0.03          # < 0.17 deg through mp4 compression
    assert np.abs(err).max() < 0.06
    # and it is not trivially zero
    assert np.abs(got).max() > 0.6


def test_dropout_and_second_blob_frames_are_invalid_with_a_reason_and_null_values(tmp_path):
    video, truth = make_video(tmp_path, dropout=0.15, distractor=0.15)
    tr, summary = run_observe(tmp_path, video)
    rows = tr.stream("observation").of("marker")
    drop = np.array(truth["dropout"])
    dis = np.array(truth["distractor"])
    assert drop.sum() > 3 and dis.sum() > 3
    for r, d, x in zip(rows, drop, dis):
        if d:
            assert (not r["valid"]) and r["invalid_reason"] == "none_found" and r["n_blobs"] == 0
        elif x:
            assert (not r["valid"]) and r["invalid_reason"] == "multiple_blobs" and r["n_blobs"] == 2
        else:
            assert r["valid"] and r["invalid_reason"] is None
        if not r["valid"]:
            assert r["observed_value"] is None and r["cx_px"] is None and r["cy_px"] is None
    assert summary["invalid_reasons"] == {"none_found": int(drop.sum()), "multiple_blobs": int(dis.sum())}


def test_file_backend_uses_media_position_and_never_invents_capture_time(tmp_path):
    video, _ = make_video(tmp_path, seconds=1.0)
    tr, _ = run_observe(tmp_path, video)
    h = tr.stream("observation").header
    rows = tr.stream("observation").of("marker")
    assert h["capture_time_ns"] is None and h["capture_time_provenance"] == "unknown"
    assert all(r["capture_time_ns"] is None and r["capture_time_provenance"] == "unknown" for r in rows)
    assert all(r["source_kind"] == "file" for r in rows)
    assert [r["media_pos_ms"] for r in rows[:4]] == pytest.approx([0.0, 1000 / 30, 2000 / 30, 100.0])
    assert all(r["received_time_meaning"] == "decode_time_not_capture" for r in rows)
    assert h["camera_config_hash"] and h["opencv"] and h["config"]["camera"]["hsv_lower"]


def test_no_pivot_calibration_gives_pixels_but_no_angle(tmp_path):
    video, _ = make_video(tmp_path, seconds=0.5)
    cfg = obs.parse_config(cfg_dict(str(video), pivot_px=None, reference_angle_rad=None, angle_scale_rad=None))
    w, d = obs.make_writer(tmp_path / "o", "s", "nocal", cfg, "DRY_RUN", "rendered_video")
    with w:
        obs.observe(cfg, w)
    rows = load_trial(d).stream("observation").of("marker")
    assert all(r["valid"] and r["observed_value"] is None and r["cx_px"] is not None for r in rows)
    assert all(r["angle_available"] is False for r in rows)


def test_marker_on_the_pivot_has_no_angle_and_is_marked_invalid():
    cfg = obs.parse_config(cfg_dict())
    assert obs.normalized_angle(SPEC.pivot_px[0] + 1, SPEC.pivot_px[1], cfg) == (None, "too_close_to_pivot")
    v, why = obs.normalized_angle(SPEC.pivot_px[0], SPEC.pivot_px[1] - 140, cfg)
    assert why is None and abs(v) < 1e-9


def test_angle_wraps_and_scales_correctly():
    cfg = obs.parse_config(cfg_dict())
    for deg in (-5.0, -1.0, 0.0, 2.0, 5.0):
        th = SPEC.reference_angle_rad + math.radians(deg)
        v, _ = obs.normalized_angle(SPEC.pivot_px[0] + 140 * math.cos(th),
                                    SPEC.pivot_px[1] + 140 * math.sin(th), cfg)
        assert v == pytest.approx(math.radians(deg) / SPEC.angle_scale_rad, abs=1e-9)
    cfg2 = obs.parse_config(cfg_dict(reference_angle_rad=math.pi - 0.01))      # near the +-pi seam
    th = -math.pi + 0.02
    v, _ = obs.normalized_angle(SPEC.pivot_px[0] + 100 * math.cos(th), SPEC.pivot_px[1] + 100 * math.sin(th), cfg2)
    assert v == pytest.approx(0.03 / SPEC.angle_scale_rad)


@pytest.mark.parametrize("patch,msg", [
    ({"hsv_lower": [0, 0]}, "3 numbers"),
    ({"hsv_lower": [0, 0, 300]}, "within"),
    ({"hsv_lower": [0, 90, 0], "hsv_upper": [179, 80, 255]}, "<="),
    ({"min_area_px": 50, "max_area_px": 10}, "min_area_px"),
    ({"min_area_px": float("nan")}, "min_area_px"),
    ({"source": "rtsp://cam/1"}, "network"),
    ({"source": True}, "source"),
    ({"pivot_px": [1]}, "pivot_px"),
    ({"angle_scale_rad": -1}, "positive"),
    ({"capture_backend": "gstreamer"}, "capture_backend"),
])
def test_config_errors_name_the_problem(patch, msg):
    with pytest.raises(obs.ConfigError, match=msg):
        obs.parse_config(cfg_dict(**patch))


def test_missing_config_keys_are_a_config_error_not_a_keyerror():
    d = cfg_dict()
    del d["min_area_px"]
    with pytest.raises(obs.ConfigError, match="min_area_px"):
        obs.parse_config(d)
    with pytest.raises(obs.ConfigError):
        obs.parse_config([1, 2])


def test_config_hash_changes_with_the_config():
    a = obs.parse_config(cfg_dict("a.mp4"))
    assert a.config_hash == obs.parse_config(cfg_dict("a.mp4")).config_hash
    assert a.config_hash != obs.parse_config(cfg_dict("a.mp4", min_area_px=41)).config_hash


def test_cli_rejects_missing_video_and_refuses_to_overwrite(tmp_path, capsys):
    cfgp = tmp_path / "cfg.json"
    cfgp.write_text(json.dumps(cfg_dict("/no/such.mp4")))
    with pytest.raises(obs.ConfigError, match="not found"):
        obs.open_capture("/no/such.mp4")
    video, _ = make_video(tmp_path, seconds=0.5)
    cfgp.write_text(json.dumps(cfg_dict(str(video))))
    args = ["--config", str(cfgp), "--out", str(tmp_path / "root"), "--session-id", "s",
            "--trial-id", "t1", "--source", "rendered_video"]
    assert obs.main(args) == 0
    assert (tmp_path / "root" / "t1" / "run_manifest.json").exists()
    summary = json.loads(capsys.readouterr().out)
    assert summary["physical_actuation"] is False and summary["frames"] == 16
    with pytest.raises(SystemExit):
        obs.main(args)                                       # same trial id again
    cfgp.write_text(json.dumps({"source": 0}))
    with pytest.raises(SystemExit):
        obs.main(args[:-4] + ["--trial-id", "t2"])           # KeyError used to leak here


def test_camera_probe_logs_frames_and_refuses_overwrite(tmp_path, capsys):
    video, _ = make_video(tmp_path, seconds=0.5)
    args = ["--source", str(video), "--frames", "100", "--out", str(tmp_path / "cp"),
            "--session-id", "s", "--trial-id", "c1", "--source-label", "rendered_video"]
    assert camera_probe.main(args) == 0
    tr = load_trial(tmp_path / "cp" / "c1")
    rows = tr.stream("observation").of("frame")
    assert len(rows) == 16 and rows[0]["source_kind"] == "file"
    assert rows[1]["media_pos_ms"] == pytest.approx(1000 / 30)
    assert all(r["host_after_read_ns"] >= r["host_before_read_ns"] for r in rows)
    assert all(r["capture_time_ns"] is None for r in rows)
    assert "stop" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        camera_probe.main(args)                              # used to silently replace the CSV
    with pytest.raises(SystemExit):
        camera_probe.main(["--source", "/nope.mp4", "--trial-id", "c9", "--out", str(tmp_path)])
    with pytest.raises(SystemExit):
        camera_probe.main(["--source", str(video), "--out", str(tmp_path)])      # no trial id


def test_camera_probe_help_does_not_import_opencv():
    import subprocess, sys
    code = ("import sys; from motion_io_lab import camera_probe; "
            "sys.argv=['x']; "
            "assert 'cv2' not in sys.modules; "
            "\ntry:\n camera_probe.main(['--help'])\nexcept SystemExit: pass\n"
            "assert 'cv2' not in sys.modules, 'cv2 imported before argument parsing'")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_example_camera_config_loads_and_is_marked_as_an_example():
    from pathlib import Path
    cfg = obs.load_config(Path(__file__).resolve().parent.parent / "examples" / "camera_config.json")
    assert cfg.source == 0 and not cfg.angle_calibrated          # no pivot: pixels only until one is set
    assert "EXAMPLE" in cfg.raw["notes"]


def test_old_module_name_still_runs_the_same_program(tmp_path):
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "motion_io_lab.observe_camera", "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "Observe a coloured marker" in r.stdout
