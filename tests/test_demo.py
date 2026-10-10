"""The demo end to end (about 30 s). Everything it checks is about SYNTHETIC data."""
import json
import hashlib
from pathlib import Path

import pytest

from motion_io_lab.cli import main
from motion_io_lab.demo import run_demo
from motion_io_lab.reference import compare
from motion_io_lab.triallog import iter_trials, load_trial

REF = Path(__file__).resolve().parent.parent / "reference_results" / "demo_summary.json"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "demo"
    summary = run_demo(out)
    return out, summary


def test_every_trial_validates_and_is_labelled_synthetic(demo):
    out, summary = demo
    trials = list(iter_trials(out))
    # 18 recordings and replays, 6 still-marker trials (noise floor), 3 resume, 2 pacing, 2 hard step
    assert len(trials) == 31
    for d in trials:
        t = load_trial(d)
        for s in t.streams.values():
            assert s.header["source"] in ("synthetic", "rendered_video")
    assert summary["source"] == "synthetic"
    assert "SYNTHETIC" in (out / "report.md").read_text()


def test_replays_were_made_in_a_new_session_epoch_each_time(demo):
    out, _ = demo
    epochs = set()
    for m in ("sweep", "step_hold", "look_wait_nod"):
        for tid in [f"{m}-rec"] + [f"{m}-rep{r:02d}" for r in range(1, 6)]:
            e = {r["session_epoch"] for r in load_trial(out / tid).stream("command").of("set")}
            assert len(e) == 1
            epochs |= e
    assert len(epochs) == 18


def test_summary_answers_the_question_with_numbers_for_all_three_motions(demo):
    _, s = demo
    for m, r in s["motions"].items():
        assert r["repeats"] == 5
        assert 0 < r["rms_deg_replay_vs_recording_observed"]["mean"] < 0.5
        assert 0 < r["repeat_sd_deg"]["observed_deg"]["pooled_sd"] < 0.2
        # the camera is behind the firmware: total lag is positive and bigger than the applied->observed lag
        assert r["lag_ms_applied_to_observed"]["mean"] > 60
        assert r["lag_ms_commanded_to_observed"]["mean"] > r["lag_ms_applied_to_observed"]["mean"]
        assert r["observation_invalid"] > 0 and set(r["observation_invalid_reasons"]) <= {"none_found", "multiple_blobs"}


def test_failure_cases_are_what_the_docs_claim(demo):
    _, s = demo
    cases = {c["case"]: c for c in s["failure_cases"]}
    for name in ("replayed_arm_and_set_after_quick_reconnect", "replayed_arm_and_set_after_reconnect",
                 "replayed_arm_and_set_after_reconnect_device_reboot"):
        c = cases[name]
        assert c["all_rejected"] and c["armed"] == 0 and c["plant_moved_deg"] == 0.0
        assert len(c["device_replies"]) == 4
    # what the device looked like when the new connection found it: only the quick case is a
    # fair test of the epoch check (still armed, not latched); the other two had stopped on their own
    assert cases["replayed_arm_and_set_after_quick_reconnect"]["device_before_hello"] == {"fault": 0, "armed": 1}
    assert cases["replayed_arm_and_set_after_quick_reconnect"]["fault"] == 0
    assert cases["replayed_arm_and_set_after_reconnect"]["device_before_hello"] == {"fault": 1, "armed": 0}
    assert cases["replayed_arm_and_set_after_reconnect_device_reboot"]["device_before_hello"] == {"fault": 0, "armed": 0}
    assert cases["duplicate_seq_with_different_value"]["device_replies"] == ["ACCEPTED_NOT_EXECUTED SET seq=1 dup=1"]
    assert cases["duplicate_seq_with_different_value"]["fault"] == 0
    assert cases["duplicate_seq_with_different_value"]["sim_truth_applied_us_after"] == 1520
    assert cases["pulse_out_of_range_then_valid"]["device_replies"][0].startswith("REJECTED SET reason=range")
    assert cases["pulse_out_of_range_then_valid"]["fault"] == 0
    assert cases["negative_sequence_number"]["device_replies"][0] == "REJECTED SET reason=parse"
    assert cases["lease_expired_then_set"]["device_replies"][0] == "STOP lease_expired"


def test_resume_cases(demo):
    _, s = demo
    r = s["resume"]
    assert r["sweep-resume-none"]["status"] == "interrupted" and r["sweep-resume-none"]["sim_truth_device_latched_at_end"]
    for p in ("continue", "restart"):
        x = r[f"sweep-resume-{p}"]
        assert x["status"] == "completed" and x["resumed"] == 1
        assert x["stale_lines_replayed"] == 4 and sum(x["stale_lines_rejected"].values()) == 4
        assert "SET:epoch" in x["stale_lines_rejected"]
    m = s["resume_metrics"]
    assert m["continue"]["max_applied_rate_deg_per_s_after_rearm"] <= m["continue"]["device_slew_limit_deg_per_s"] + 1e-9
    assert abs(m["continue"]["final_applied_deg"]) < 0.3 and abs(m["restart"]["final_applied_deg"]) < 0.3
    assert m["continue"]["outage_ms_interrupt_to_rearm"] > 600


def test_pacing_deadline_does_not_drift_relative_does(demo):
    _, s = demo
    assert abs(s["timing"]["deadline"]["drift_ppm"]) < 50
    assert s["timing"]["relative"]["drift_ppm"] > 3000
    assert s["timing"]["relative"]["lateness_ms_max"] > 50 * s["timing"]["deadline"]["lateness_ms_max"]


def test_hard_step_shows_the_device_limit(demo):
    _, s = demo
    h = s["hard_step_case"]
    assert h["device_slew_only"]["max_commanded_minus_applied_us"] >= 45
    assert h["host_slew_400"]["max_commanded_minus_applied_us"] < 20
    assert 90 <= h["device_slew_only"]["applied_reaches_90pct_ms_after_first_command_of_step"] <= 140


def test_manifest_hashes_match_the_files(demo):
    out, _ = demo
    m = json.loads((out / "run_manifest.json").read_text())
    assert m["source"] == "synthetic" and m["seed"] == 7 and m["command"] == "demo"
    for rel, digest in list(m["output_sha256"].items())[:25]:
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    assert "report.md" in m["output_sha256"] and "summary.json" in m["output_sha256"]
    assert m["environment"]["numpy"] and m["environment"]["opencv"]


def test_demo_refuses_a_used_output_directory(demo, tmp_path):
    out, _ = demo
    with pytest.raises(FileExistsError):
        run_demo(out)


@pytest.mark.skipif(not REF.exists(), reason="reference_results not generated yet")
def test_summary_matches_the_checked_in_reference_within_tolerance(demo):
    out, summary = demo
    ref = json.loads(REF.read_text())
    assert compare(summary, ref) == []
    assert main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(REF)]) == 0


def test_reference_comparison_catches_real_differences():
    ref = {"a": {"rms_deg_observed": {"mean": 0.1}, "count": 5, "name": "s", "ok": True, "v": 1.0}}
    assert compare(json.loads(json.dumps(ref)), ref) == []
    new = json.loads(json.dumps(ref))
    new["a"]["rms_deg_observed"]["mean"] = 0.13
    assert compare(new, ref) == []                          # video-derived: cross-build slack 0.06 deg
    assert compare(new, ref, strict=True)                   # ... but not identical
    new["a"]["rms_deg_observed"]["mean"] = 0.3
    assert compare(new, ref)
    new = json.loads(json.dumps(ref)); new["a"]["count"] = 6
    assert compare(new, ref)
    new = json.loads(json.dumps(ref)); new["a"]["ok"] = False
    assert compare(new, ref)
    new = json.loads(json.dumps(ref)); new["a"]["v"] = 1.001
    assert compare(new, ref)                                # numbers that do not pass the video are strict
    new = json.loads(json.dumps(ref)); del new["a"]["name"]
    assert compare(new, ref)
    new = json.loads(json.dumps(ref)); new["a"]["extra"] = 1
    assert compare(new, ref)


def test_reference_comparison_checks_list_lengths_and_types():
    ref = {"a": {"values": [1.0, 2.0, 3.0]}}
    assert compare({"a": {"values": [1.0, 2.0]}}, ref)               # shorter
    assert compare({"a": {"values": [1.0, 2.0, 3.0, 4.0]}}, ref)     # longer
    assert compare({"a": {"values": "x"}}, ref)                      # not a list
    assert compare({"a": {"values": [1.0, 2.0, 3.0000001]}}, ref) == []


def test_only_the_video_derived_numbers_get_the_cross_build_slack():
    from motion_io_lab.reference import match_level
    ref = {"motions": {"sweep": {
        "rms_deg_replay_vs_recording_commanded": {"mean": 0.0915},      # never sees the video
        "rms_deg_replay_vs_recording_applied": {"mean": 0.0168},
        "rms_deg_replay_vs_recording_observed": {"mean": 0.0608},       # reads the decoded video
        "lag_ms_commanded_to_observed": {"mean": 130.4},
        "lag_ms_applied_to_observed_expected": {"mean": 113.8},         # noiseless servo: strict
        "observation_frames": 419, "observation_invalid": 30}}}

    def with_(path, value):
        new = json.loads(json.dumps(ref))
        d = new["motions"]["sweep"]
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
        return new
    assert match_level(json.loads(json.dumps(ref)), ref)[0] == "identical"
    # the examples that slipped through before: applied stage x4, observed SD-sized shifts, 10 % of the frames
    assert match_level(with_(("rms_deg_replay_vs_recording_applied", "mean"), 0.066), ref)[0] == "different"
    assert match_level(with_(("rms_deg_replay_vs_recording_commanded", "mean"), 0.141), ref)[0] == "different"
    assert match_level(with_(("observation_frames",), 380), ref)[0] == "different"
    assert match_level(with_(("lag_ms_applied_to_observed_expected", "mean"), 118.0), ref)[0] == "different"
    assert match_level(with_(("lag_ms_commanded_to_observed", "mean"), 150.0), ref)[0] == "different"
    # a different build: video-derived numbers move a little
    lvl, diffs = match_level(with_(("rms_deg_replay_vs_recording_observed", "mean"), 0.075), ref)
    assert lvl == "within_cross_build_tolerance" and len(diffs) == 1
    assert match_level(with_(("lag_ms_commanded_to_observed", "mean"), 132.0), ref)[0] == "within_cross_build_tolerance"
    assert match_level(with_(("observation_invalid",), 32), ref)[0] == "within_cross_build_tolerance"


def test_compare_reference_cli_reports_the_level_and_can_require_identical(demo, tmp_path, capsys):
    out, summary = demo
    ref_path = tmp_path / "ref.json"
    shifted = json.loads(json.dumps(summary))
    shifted["motions"]["sweep"]["rms_deg_replay_vs_recording_observed"]["mean"] += 0.02
    ref_path.write_text(json.dumps(shifted))
    assert main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(ref_path)]) == 0
    assert "cross-build" in capsys.readouterr().out
    assert main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(ref_path),
                 "--require-identical"]) == 1
    ref_path.write_text(json.dumps(summary))
    assert main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(ref_path),
                 "--require-identical"]) == 0
    assert "identical" in capsys.readouterr().out
    shifted["motions"]["sweep"]["rms_deg_replay_vs_recording_applied"]["mean"] += 0.05
    ref_path.write_text(json.dumps(shifted))
    assert main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(ref_path)]) == 1
    # a missing reference file is a one-line error, not a traceback
    with pytest.raises(SystemExit):
        main(["compare-reference", "--summary", str(out / "summary.json"), "--reference", str(tmp_path / "none.json")])
