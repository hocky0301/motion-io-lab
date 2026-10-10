import csv
import json

import numpy as np
import pytest

from motion_io_lab import prepare_real_dataset as prd

HEAD = ["session_id", "trial_id", "step", "observed_x_before", "command_u", "observed_x_after", "dt_s",
        "valid", "state_definition", "action_definition"]


def write_csv(path, rows, head=HEAD, bom=False):
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def good_rows(sessions=4, trials=3, steps=2):
    rows = []
    for s in range(sessions):
        for t in range(trials):
            for k in range(steps):
                rows.append([f"s{s}", f"t{t}", k, 0.1 * k, 1500 + k, 0.1 * k + 0.05, 0.2, "true", "x_norm", "u_us"])
    return rows


def conv(tmp_path, rows, name="train.npz", **kw):
    src = tmp_path / "in.csv"
    write_csv(src, rows, bom=kw.pop("bom", False))
    args = dict(x_units="norm_angle", u_units="pulse_us")
    args.update(kw)
    return prd.convert(src, tmp_path / name, 0.2, 0.1, **args)


def test_good_file_converts_and_manifest_has_units_hashes_and_versions(tmp_path):
    rep = conv(tmp_path, good_rows(), x_range=(-2, 2), u_range=(1450, 1550))
    assert rep["accepted"] == 24 and rep["sessions"] == 4
    z = np.load(tmp_path / "train.npz")
    assert z["x"].shape == (24, 2) and z["y"].shape == (24, 1)
    assert set(z["groups"]) == {"s0", "s1", "s2", "s3"}
    np.testing.assert_allclose(z["y"], 0.05)
    m = json.loads((tmp_path / "train.manifest.json").read_text())
    assert m["units"] == {"observed_x": "norm_angle", "command_u": "pulse_us", "dt_s": "s"}
    assert len(m["source_csv_sha256"]) == 64 and len(m["npz_sha256"]) == 64
    assert m["tool"].startswith("motion-io-lab") and "split by session" in m["note"]


def test_too_few_sessions_is_refused_with_the_reason(tmp_path):
    with pytest.raises(prd.DatasetError, match="3 session.*split by session|at least 4"):
        conv(tmp_path, good_rows(sessions=3))
    conv(tmp_path, good_rows(sessions=3), name="ok.npz", min_sessions=3)


def test_short_row_gives_a_row_numbered_error_not_an_attribute_error(tmp_path):
    rows = good_rows()
    rows[4] = rows[4][:5]
    with pytest.raises(prd.DatasetError, match=r"in.csv:6: row has the wrong number of columns"):
        conv(tmp_path, rows)


def test_non_numeric_and_non_finite_values_name_the_row_and_column(tmp_path):
    rows = good_rows()
    rows[2][4] = "abc"
    with pytest.raises(prd.DatasetError, match=r"in.csv:4: command_u is not a number"):
        conv(tmp_path, rows)
    rows = good_rows()
    rows[0][3] = "nan"
    with pytest.raises(prd.DatasetError, match=r"in.csv:2: observed_x_before is not finite"):
        conv(tmp_path, rows)
    rows = good_rows()
    rows[0][5] = "inf"
    with pytest.raises(prd.DatasetError, match="not finite"):
        conv(tmp_path, rows)


def test_valid_column_is_strict_a_typo_is_an_error_not_a_silent_rejection(tmp_path):
    rows = good_rows()
    rows[3][7] = "ok"
    with pytest.raises(prd.DatasetError, match=r"in.csv:5: valid must be true/false/1/0"):
        conv(tmp_path, rows)
    rows = good_rows()
    for r in rows[:3]:
        r[7] = "false"
        r[3] = ""                                         # an invalid row may have empty values
    rep = conv(tmp_path, rows)
    assert rep["rejected"]["valid_false"] == 3 and rep["accepted"] == 21


def test_duplicate_keys_are_found_even_when_the_second_row_is_invalid(tmp_path):
    rows = good_rows()
    dup = list(rows[0])
    dup[7] = "false"
    rows.append(dup)
    with pytest.raises(prd.DatasetError, match="duplicate"):
        conv(tmp_path, rows)


def test_blank_ids_and_definitions_are_errors(tmp_path):
    rows = good_rows()
    rows[1][0] = " "
    with pytest.raises(prd.DatasetError, match="must not be blank"):
        conv(tmp_path, rows)
    rows = good_rows()
    rows[1][8] = ""
    with pytest.raises(prd.DatasetError, match="definition"):
        conv(tmp_path, rows)
    rows = good_rows()
    rows[1][8] = "other"
    with pytest.raises(prd.DatasetError, match="mixed"):
        conv(tmp_path, rows)


def test_dt_outside_tolerance_is_counted_not_silently_dropped(tmp_path):
    rows = good_rows()
    rows[0][6] = 0.5
    rows[1][6] = 0.19
    rep = conv(tmp_path, rows)
    assert rep["rejected"]["dt_out_of_tolerance"] == 1 and rep["accepted"] == 23


def test_degrees_versus_radians_style_mistakes_hit_the_declared_range(tmp_path):
    rows = good_rows()
    rows[5][3] = 57.3                                     # a degree value in a normalised column
    with pytest.raises(prd.DatasetError, match=r"in.csv:7: observed_x_before=57.3 is outside"):
        conv(tmp_path, rows, x_range=(-2, 2))
    rows = good_rows()
    rows[5][4] = 15.0                                     # millisecond / wrong unit in command_u
    with pytest.raises(prd.DatasetError, match="command_u=15.0 is outside"):
        conv(tmp_path, rows, u_range=(1450, 1550))


def test_utf8_bom_from_a_spreadsheet_is_accepted(tmp_path):
    rep = conv(tmp_path, good_rows(), bom=True)
    assert rep["accepted"] == 24


def test_missing_columns_and_empty_template(tmp_path):
    src = tmp_path / "t.csv"
    write_csv(src, [], head=HEAD)
    with pytest.raises(prd.DatasetError, match="no rows"):
        prd.convert(src, tmp_path / "o.npz", 0.2, 0.1, x_units="a", u_units="b")
    write_csv(src, [[1, 2]], head=["session_id", "trial_id"])
    with pytest.raises(prd.DatasetError, match="missing required columns"):
        prd.convert(src, tmp_path / "o.npz", 0.2, 0.1, x_units="a", u_units="b")


def test_overwrite_is_refused_with_or_without_the_extension_and_for_the_manifest(tmp_path):
    conv(tmp_path, good_rows(), name="train.npz")
    for name in ("train.npz", "train"):                   # "train" used to overwrite train.npz silently
        with pytest.raises(FileExistsError):
            conv(tmp_path, good_rows(), name=name)
    (tmp_path / "other.manifest.json").write_text("{}")
    with pytest.raises(FileExistsError, match="manifest"):
        conv(tmp_path, good_rows(), name="other.npz")
    assert not (tmp_path / "other.npz").exists()


def test_no_open_file_handles_leak(recwarn, tmp_path):
    conv(tmp_path, good_rows())
    import gc
    gc.collect()
    assert not [w for w in recwarn if issubclass(w.category, ResourceWarning)]


def test_invalid_interval_configuration(tmp_path):
    src = tmp_path / "in.csv"
    write_csv(src, good_rows())
    for dt, tol in ((0, 0.1), (-1, 0.1), (float("nan"), 0.1), (0.2, 1.0), (0.2, -0.1)):
        with pytest.raises(prd.DatasetError):
            prd.convert(src, tmp_path / "x.npz", dt, tol, x_units="a", u_units="b")


def test_extract_pairs_commands_with_observations_from_trial_logs(tmp_path):
    from tests.test_analysis import build_trial
    tr, _ = build_trial(tmp_path)
    out = tmp_path / "tr.csv"
    n = prd.extract([tr.path], 0.1, out, media_offset_ms=0.0)
    assert n == 10
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["session_id"] == "s" and rows[0]["trial_id"] == "t" and rows[0]["step"] == "1"
    first = rows[0]
    assert first["valid"] == "true" and float(first["observed_x_before"]) == pytest.approx(0.0)
    assert float(first["observed_x_after"]) == pytest.approx(0.1)           # 100 ms later in the video
    assert float(first["command_u"]) == 1500.0 and float(first["dt_s"]) == 0.1
    assert any(r["valid"] == "false" and r["observed_x_before"] == "" for r in rows)   # no observation -> no number
    with pytest.raises(FileExistsError):
        prd.extract([tr.path], 0.1, out)


def test_cli_main_reports_errors_without_a_traceback(tmp_path, capsys):
    src = tmp_path / "in.csv"
    write_csv(src, good_rows(sessions=2))
    rc = prd.main(["convert", str(src), "--out", str(tmp_path / "o.npz"), "--dt", "0.2",
                   "--x-units", "n", "--u-units", "us"])
    assert rc == 2 and "error:" in capsys.readouterr().err
