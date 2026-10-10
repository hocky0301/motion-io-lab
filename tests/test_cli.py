import json
import subprocess
import sys

import pytest

from motion_io_lab.cli import main
from motion_io_lab.triallog import load_trial

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def run(*args):
    return subprocess.run([sys.executable, "-m", "motion_io_lab", *args], capture_output=True, text=True,
                          cwd=REPO_ROOT, timeout=120)


def test_every_subcommand_has_help_and_exits_zero():
    for sub in ("demo", "record", "play", "export-motion", "validate", "calibrate", "analyze",
                "virtual-device", "observe", "camera-probe", "prepare-dataset", "probe", "console"):
        r = run(sub, "--help")
        assert r.returncode == 0, (sub, r.stderr)
        assert "usage" in r.stdout.lower()


def test_default_target_is_dry_and_nothing_is_sent(tmp_path, capsys):
    rc = main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "r1",
               "--source", "script:look_wait_nod"])
    assert rc == 0
    tr = load_trial(tmp_path / "r1")
    assert tr.mode == "DRY_RUN" and tr.stream("command").header["link"] == "none"
    assert all(r["sent_monotonic_ns"] is None for r in tr.stream("command").of("set"))
    assert (tmp_path / "r1" / "run_manifest.json").exists()
    m = json.loads((tmp_path / "r1" / "run_manifest.json").read_text())
    assert m["command"] == "record" and m["environment"]["python"] and "input.jsonl" in m["output_sha256"]
    assert m["args"]["target"] == "dry"


def test_record_play_export_roundtrip_through_the_cli(tmp_path):
    out = str(tmp_path)
    assert main(["record", "--out", out, "--session-id", "s", "--trial-id", "r1", "--target", "loopback",
                 "--source", "script:sweep"]) == 0
    assert main(["export-motion", "--trial", str(tmp_path / "r1"), "--out", str(tmp_path / "m.jsonl")]) == 0
    assert main(["play", "--out", out, "--session-id", "s", "--trial-id", "p1", "--motion",
                 str(tmp_path / "m.jsonl"), "--target", "loopback"]) == 0
    assert main(["play", "--out", out, "--session-id", "s", "--trial-id", "p2", "--motion",
                 str(tmp_path / "r1"), "--target", "dry", "--start-ms", "500", "--end-ms", "1500"]) == 0
    p1, p2 = load_trial(tmp_path / "p1"), load_trial(tmp_path / "p2")
    assert p1.mode == "REPLAY" and len(p2.stream("command").of("set")) < len(p1.stream("command").of("set"))
    assert main(["validate", out]) == 0


def test_hardware_target_refuses_without_the_flags(tmp_path):
    r = run("play", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "h", "--motion", "x",
            "--target", "hardware")
    assert r.returncode != 0 and not (tmp_path / "h").exists()
    r = run("record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "h", "--target",
            "hardware", "--source", "script:sweep", "--port", "/dev/null")
    assert r.returncode != 0 and "--i-checked-hardware" in (r.stderr + r.stdout)
    assert not (tmp_path / "h").exists()


def test_hardware_target_with_flags_still_needs_a_terminal(tmp_path):
    r = run("record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "h", "--target",
            "hardware", "--source", "script:sweep", "--port", "/dev/null", "--i-checked-hardware")
    assert r.returncode != 0 and "interactive terminal" in (r.stderr + r.stdout)


def test_bad_arguments_are_clean_errors(tmp_path):
    r = run("play", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "x", "--motion",
            str(tmp_path / "missing.jsonl"))
    assert r.returncode != 0 and "Traceback" not in r.stderr
    r = run("play", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "x", "--motion",
            str(tmp_path), "--speed", "2")
    assert r.returncode != 0 and "Traceback" not in r.stderr
    r = run("record", "--out", str(tmp_path), "--session-id", "../s", "--trial-id", "x",
            "--source", "script:nope")
    assert r.returncode != 0 and "Traceback" not in r.stderr


def test_validate_flags_a_broken_trial(tmp_path, capsys):
    main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "r1", "--source", "script:sweep"])
    p = tmp_path / "r1" / "command.jsonl"
    p.write_text(p.read_text() + "{broken\n")
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "command.jsonl:" in out


def test_calibrate_cli(tmp_path, capsys):
    c = tmp_path / "pts.csv"
    c.write_text("pulse_us,angle_deg\n1450,-5\n1500,0.1\n1550,5.2\n")
    assert main(["calibrate", "--points", str(c), "--out", str(tmp_path / "cal.json")]) == 0
    assert "version=" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["calibrate", "--points", str(c), "--out", str(tmp_path / "cal.json")])      # no overwrite


def test_virtual_device_prints_a_pty_path():
    r = run("virtual-device", "--seconds", "0.2")
    assert r.returncode == 0 and "/dev/" in r.stdout and "SYNTHETIC" in r.stdout


def test_summarize_session_reads_the_source_from_the_logs_and_never_overwrites(tmp_path, capsys):
    from motion_io_lab.demo import run_demo, synthetic_calibration

    out = tmp_path / "demo"
    run_demo(out, repeats=2)
    cal = tmp_path / "cal.json"
    cal.write_text(synthetic_calibration().text)
    res = tmp_path / "res.json"
    assert main(["summarize-session", "--root", str(out), "--calibration", str(cal), "--repeats", "2",
                 "--out", str(res), "--motions", "sweep,look_wait_nod"]) == 0
    s = json.loads(res.read_text())
    assert s["source"] == "synthetic" or s["source"] == "mixed"       # logs say synthetic + rendered_video
    assert set(s["motions"]) == {"sweep", "look_wait_nod"}
    assert s["motions"]["sweep"]["repeats"] == 2 and s["calibration_source"] == "synthetic"
    with pytest.raises(SystemExit):
        main(["summarize-session", "--root", str(out), "--calibration", str(cal), "--out", str(res)])
    with pytest.raises(SystemExit):
        main(["summarize-session", "--root", str(out), "--calibration", str(cal), "--repeats", "9",
              "--out", str(tmp_path / "other.json")])                  # trials that do not exist


def test_two_commands_in_one_trial_directory_keep_separate_manifests(tmp_path):
    from motion_io_lab.manifest import write_run_manifest

    (tmp_path / "a.txt").write_text("x")
    write_run_manifest(tmp_path, "record", {})
    write_run_manifest(tmp_path, "observe", {})
    assert (tmp_path / "run_manifest.json").exists() and (tmp_path / "run_manifest.observe.json").exists()
    m = json.loads((tmp_path / "run_manifest.observe.json").read_text())
    assert list(m["output_sha256"]) == ["a.txt"]             # manifests do not hash each other
    with pytest.raises(FileExistsError):
        write_run_manifest(tmp_path, "observe", {})
