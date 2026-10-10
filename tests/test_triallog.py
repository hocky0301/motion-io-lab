import json
import os

import pytest

from motion_io_lab.triallog import (SCHEMA_VERSION, StreamWriter, TrialContext, TrialLogError,
                                    load_trial, read_stream, validate_stream)


def ctx(**kw):
    d = dict(session_id="s1", trial_id="t1", mode="LOOPBACK", source="synthetic", link="virtual")
    d.update(kw)
    return TrialContext(**d)


def write_set(w, seq, ns, pulse=1500):
    return w.write("set", seq=seq, desired_pulse_us=float(pulse), commanded_pulse_us=pulse,
                   valid_for_ms=100, scheduled_monotonic_ns=ns, sent_monotonic_ns=ns,
                   processed_monotonic_ns=ns, shaping="none")


def test_roundtrip_and_header(tmp_path):
    with StreamWriter(tmp_path / "command.jsonl", "command", ctx(), units="pulse_us",
                      coordinate_frame="servo_axis_0") as w:
        write_set(w, 1, 10)
        write_set(w, 2, 20)
    s = read_stream(tmp_path / "command.jsonl")
    assert s.header["schema_version"] == SCHEMA_VERSION
    assert s.header["record_type"] == "header"
    assert [r["seq"] for r in s.records] == [1, 2]
    r = s.records[0]
    for k in ("capture_time_ns", "capture_time_provenance", "received_monotonic_ns", "units",
              "coordinate_frame", "calibration_version", "valid", "clock_domain", "source"):
        assert k in r


def test_atomic_publish_part_file_until_closed(tmp_path):
    p = tmp_path / "command.jsonl"
    w = StreamWriter(p, "command", ctx(), units="pulse_us", coordinate_frame="servo_axis_0")
    assert not p.exists() and (tmp_path / "command.jsonl.part").exists()
    write_set(w, 1, 1)
    w.close()
    assert p.exists() and not (tmp_path / "command.jsonl.part").exists()


def test_crash_leaves_part_and_nothing_published(tmp_path):
    p = tmp_path / "command.jsonl"
    with pytest.raises(RuntimeError):
        with StreamWriter(p, "command", ctx(), units="pulse_us", coordinate_frame="f") as w:
            write_set(w, 1, 1)
            raise RuntimeError("boom")
    assert not p.exists()
    _, _, errs = validate_stream(tmp_path / "command.jsonl.part")
    assert errs and "unfinished" in errs[0]


def test_never_appends_or_overwrites(tmp_path):
    p = tmp_path / "input.jsonl"
    with StreamWriter(p, "input", ctx(), units="norm", coordinate_frame="f"):
        pass
    with pytest.raises(FileExistsError):
        StreamWriter(p, "input", ctx(), units="norm", coordinate_frame="f")
    (tmp_path / "x.jsonl.part").write_text("leftover")
    with pytest.raises(FileExistsError):
        StreamWriter(tmp_path / "x.jsonl", "input", ctx(), units="norm", coordinate_frame="f")


def test_non_finite_numbers_are_refused(tmp_path):
    with StreamWriter(tmp_path / "c.jsonl", "command", ctx(), units="pulse_us",
                      coordinate_frame="f") as w:
        with pytest.raises(ValueError, match="non-finite"):
            w.write("input_event" if False else "set", seq=1, desired_pulse_us=float("nan"),
                    commanded_pulse_us=1500, valid_for_ms=100, scheduled_monotonic_ns=1,
                    sent_monotonic_ns=1, shaping="none")


def test_capture_time_cannot_be_invented(tmp_path):
    with StreamWriter(tmp_path / "o.jsonl", "observation", ctx(), units="norm_angle",
                      coordinate_frame="camera_image") as w:
        base = dict(frame_id=0, media_pos_ms=0.0, source_kind="file", observed_value=0.1,
                    cx_px=1.0, cy_px=2.0, area_px=3.0, n_blobs=1, invalid_reason=None)
        with pytest.raises(TrialLogError, match="provenance"):
            w.write("marker", capture_time_ns=123, **base)       # set but provenance 'unknown'
        w.write("marker", capture_time_ns=123, capture_time_provenance="device", **base)


def test_invalid_observation_must_not_carry_zero(tmp_path):
    with StreamWriter(tmp_path / "o.jsonl", "observation", ctx(), units="norm_angle",
                      coordinate_frame="camera_image") as w:
        with pytest.raises(TrialLogError, match="never 0"):
            w.write("marker", frame_id=0, media_pos_ms=0.0, source_kind="file", observed_value=0.0,
                    cx_px=None, cy_px=None, area_px=None, n_blobs=0, invalid_reason="none_found",
                    valid=False)
        with pytest.raises(TrialLogError, match="invalid_reason"):
            w.write("marker", frame_id=0, media_pos_ms=0.0, source_kind="file", observed_value=None,
                    cx_px=None, cy_px=None, area_px=None, n_blobs=0, invalid_reason=None, valid=False)
        w.write("marker", frame_id=0, media_pos_ms=0.0, source_kind="file", observed_value=None,
                cx_px=None, cy_px=None, area_px=None, n_blobs=0, invalid_reason="none_found",
                valid=False)


def test_mode_link_combinations_enforced():
    with pytest.raises(ValueError):
        ctx(mode="DRY_RUN", link="virtual")
    with pytest.raises(ValueError):
        ctx(mode="LOOPBACK", link="serial")
    with pytest.raises(ValueError):
        ctx(mode="HARDWARE_ARMED", link="virtual")
    with pytest.raises(ValueError):
        ctx(source="hardware", mode="LOOPBACK", link="virtual")
    with pytest.raises(ValueError):
        ctx(mode="SOMETHING")
    ctx(mode="REPLAY", link="none", source="synthetic")


def test_ids_are_restricted():
    with pytest.raises(ValueError):
        ctx(trial_id="../escape")
    with pytest.raises(ValueError):
        ctx(session_id="")


def _mutate(path, fn):
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    fn(lines)
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def make(tmp_path):
    p = tmp_path / "command.jsonl"
    with StreamWriter(p, "command", ctx(), units="pulse_us", coordinate_frame="f") as w:
        for i in range(1, 4):
            write_set(w, i, i * 1000)
    return p


def test_validator_reports_line_numbers_for_every_defect(tmp_path):
    p = make(tmp_path)

    def bad(lines):
        lines[2]["seq"] = 1                       # not increasing
        lines[3]["received_monotonic_ns"] = 5
        lines[1]["received_monotonic_ns"] = 99    # then goes backwards at line 4
        lines[3]["mode"] = "HARDWARE_ARMED"
        del lines[2]["units"]
    _mutate(p, bad)
    _, _, errs = validate_stream(p)
    text = "\n".join(errs)
    assert "command.jsonl:3" in text and "missing common fields" in text
    assert "command.jsonl:4" in text and "backwards" in text
    assert "differs from header" in text


def test_validator_rejects_missing_header_second_header_blank_and_nan(tmp_path):
    p = make(tmp_path)
    raw = p.read_text().splitlines()
    (tmp_path / "nohdr.jsonl").write_text("\n".join(raw[1:]) + "\n")
    assert any("first line must be the header" in e for e in validate_stream(tmp_path / "nohdr.jsonl")[2])
    (tmp_path / "two.jsonl").write_text("\n".join(raw + [raw[0]]) + "\n")
    assert any("second header" in e for e in validate_stream(tmp_path / "two.jsonl")[2])
    (tmp_path / "blank.jsonl").write_text(raw[0] + "\n\n" + raw[1] + "\n")
    assert any("blank line" in e for e in validate_stream(tmp_path / "blank.jsonl")[2])
    (tmp_path / "nan.jsonl").write_text(raw[0] + "\n" + raw[1].replace('"seq":1', '"seq":NaN') + "\n")
    assert any("not valid JSON" in e for e in validate_stream(tmp_path / "nan.jsonl")[2])
    (tmp_path / "empty.jsonl").write_text("")
    assert validate_stream(tmp_path / "empty.jsonl")[2]


def test_sequence_must_increase_within_an_epoch_but_may_restart_in_a_new_one(tmp_path):
    p = tmp_path / "command.jsonl"
    c = ctx()
    with StreamWriter(p, "command", c, units="pulse_us", coordinate_frame="f") as w:
        c.session_epoch = 100
        write_set(w, 1, 1)
        write_set(w, 2, 2)
        c.session_epoch = 200            # reconnect: new epoch, sequence starts again
        write_set(w, 1, 3)
    assert len(read_stream(p).records) == 3
    p2 = tmp_path / "bad" / "command.jsonl"
    c2 = ctx()
    with pytest.raises(TrialLogError, match="not increasing"):
        with StreamWriter(p2, "command", c2, units="pulse_us", coordinate_frame="f") as w:
            c2.session_epoch = 100
            write_set(w, 2, 1)
            write_set(w, 2, 2)
        read_stream(p2)


def test_load_trial_checks_ids_across_streams(tmp_path):
    d = tmp_path / "t1"
    d.mkdir()
    with StreamWriter(d / "command.jsonl", "command", ctx(), units="pulse_us", coordinate_frame="f") as w:
        write_set(w, 1, 1)
    with StreamWriter(d / "input.jsonl", "input", ctx(trial_id="other"), units="norm", coordinate_frame="f"):
        pass
    with pytest.raises(TrialLogError, match="disagree"):
        load_trial(d)


def test_not_a_trial_directory(tmp_path):
    with pytest.raises(TrialLogError):
        load_trial(tmp_path)


def test_checked_in_schema_file_is_the_one_the_code_uses():
    from pathlib import Path
    from motion_io_lab.triallog import schema_dict

    f = Path(__file__).resolve().parent.parent / "docs" / "trial_schema_v1.json"
    assert json.loads(f.read_text()) == json.loads(json.dumps(schema_dict()))


# --- holes in the validator -------------------------------------------------------------------

def _write_command(path, rows):
    with StreamWriter(path, "command", ctx(), units="pulse_us", coordinate_frame="servo_axis_0") as w:
        for seq, ns in rows:
            write_set(w, seq, ns)


def _edit(path, fn):
    lines = path.read_text().split("\n")
    path.write_text("\n".join(fn(lines)))


def test_set_rows_whose_scheduled_or_sent_time_goes_backwards_are_flagged(tmp_path):
    p = tmp_path / "command.jsonl"
    _write_command(p, [(1, 10), (2, 20), (3, 30)])
    _edit(p, lambda L: [L[0], L[1], L[2], L[3].replace('"scheduled_monotonic_ns":30', '"scheduled_monotonic_ns":5')]
          + L[4:])
    _, _, errs = validate_stream(p)
    assert any("scheduled_monotonic_ns goes backwards" in e for e in errs)
    q = tmp_path / "b" / "command.jsonl"
    _write_command(q, [(1, 10), (2, 20), (3, 30)])
    _edit(q, lambda L: [L[0], L[1], L[2], L[3].replace('"sent_monotonic_ns":30', '"sent_monotonic_ns":7')] + L[4:])
    assert any("sent_monotonic_ns goes backwards" in e for e in validate_stream(q)[2])


def test_equal_times_are_fine_and_dropped_rows_count_for_scheduled(tmp_path):
    p = tmp_path / "command.jsonl"
    _write_command(p, [(1, 10), (2, 10)])
    assert validate_stream(p)[2] == []


def test_a_number_that_parses_to_infinity_is_flagged_anywhere(tmp_path):
    p = tmp_path / "command.jsonl"
    _write_command(p, [(1, 10), (2, 20)])
    _edit(p, lambda L: [L[0], L[1].replace('"commanded_pulse_us":1500', '"commanded_pulse_us":1e999')] + L[2:])
    assert any("not finite" in e for e in validate_stream(p)[2])
    q = tmp_path / "q" / "command.jsonl"
    _write_command(q, [(1, 10)])
    _edit(q, lambda L: [L[0].replace('"config":{}', '"config":{"x":[1,{"y":1e999}]}')] + L[1:])
    assert any("not finite" in e for e in validate_stream(q)[2])


def test_a_file_that_is_not_utf8_is_an_error_not_a_traceback(tmp_path):
    p = tmp_path / "command.jsonl"
    p.write_bytes(b'{"record_type":"header"}\n\xff\xfe\x00bad\n')
    header, records, errs = validate_stream(p)
    assert header is None and any("UTF-8" in e for e in errs)


def test_a_command_stream_copied_under_the_input_name_is_refused(tmp_path):
    d = tmp_path / "t1"
    d.mkdir()
    _write_command(d / "command.jsonl", [(1, 10)])
    (d / "input.jsonl").write_bytes((d / "command.jsonl").read_bytes())
    with pytest.raises(TrialLogError, match="header says stream 'command'"):
        load_trial(d)


def test_ids_may_not_end_with_a_newline_or_hide_a_path():
    from motion_io_lab.triallog import check_id
    for bad in ("abc\n", "a/..", "a/b", "../x", ".hidden", "", "a b", "x" * 65):
        with pytest.raises(ValueError):
            check_id("trial_id", bad)
    assert check_id("trial_id", "sweep-rep01") == "sweep-rep01"


def test_nested_nan_in_the_header_or_a_row_is_refused_by_the_writer(tmp_path):
    with pytest.raises(TrialLogError):
        StreamWriter(tmp_path / "a.jsonl", "command",
                     ctx(config={"span": float("nan")}), units="pulse_us", coordinate_frame="f")
    with StreamWriter(tmp_path / "b.jsonl", "command", ctx(), units="pulse_us", coordinate_frame="f") as w:
        with pytest.raises(TrialLogError, match="not finite"):
            w.write("event", event="x", detail={"v": float("nan")}, received_monotonic_ns=1,
                    processed_monotonic_ns=1)
    assert (tmp_path / "b.jsonl").exists()          # the failed row did not poison the file
    read_stream(tmp_path / "b.jsonl")
