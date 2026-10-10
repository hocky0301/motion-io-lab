"""Turn trial logs into (state, action, next-state) transitions, then into an NPZ that another repository can train on (this repository does not train).

Two steps, both strict:

    extract  trials -> transitions.csv   pairs a command with the observation just before it and
                                         one ``dt`` after it, from the trial log (never invented)
    convert  transitions.csv -> .npz     checks every row (row numbers in every error), refuses to
                                         overwrite, writes a manifest with units and file hashes

The tool does not certify that timing or pairing are right: the observation time base of a
video *file* is an assumption (see analysis.extract), and the manifest says so.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from . import __version__

REQUIRED = ["session_id", "trial_id", "step", "observed_x_before", "command_u", "observed_x_after",
            "dt_s", "valid", "state_definition", "action_definition"]
TRUE, FALSE = {"true", "1"}, {"false", "0"}
MIN_SESSIONS_DEFAULT = 4


class DatasetError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_bool(text: str, where: str) -> bool:
    t = (text or "").strip().lower()
    if t in TRUE:
        return True
    if t in FALSE:
        return False
    raise DatasetError(f"{where}: valid must be true/false/1/0, got {text!r}")


def convert(source: Path, output: Path, expected_dt: float, tolerance: float, *,
            x_units: str, u_units: str, x_range: tuple[float, float] | None = None,
            u_range: tuple[float, float] | None = None,
            min_sessions: int = MIN_SESSIONS_DEFAULT) -> dict:
    if not math.isfinite(expected_dt) or expected_dt <= 0 or not 0 <= tolerance < 1:
        raise DatasetError("invalid interval configuration (dt > 0, 0 <= tolerance < 1)")
    output = Path(output)
    if output.suffix != ".npz":
        output = output.with_name(output.name + ".npz")        # np.savez would add it silently
    manifest = output.with_suffix(".manifest.json")
    for p in (output, manifest):
        if p.exists():
            raise FileExistsError(f"{p} exists; datasets are not overwritten")
    source = Path(source)
    with source.open(newline="", encoding="utf-8-sig") as f:       # BOM from spreadsheet exports
        rd = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
        if missing:
            raise DatasetError(f"{source.name}: missing required columns {missing}")
        rows = list(rd)
    if not rows:
        raise DatasetError(f"{source.name}: no rows (the template is deliberately empty)")
    xs, ys, groups, keys = [], [], [], set()
    state_defs, action_defs = set(), set()
    rejected = {"valid_false": 0, "dt_out_of_tolerance": 0}
    for n, row in enumerate(rows, start=2):                            # line 1 is the header
        where = f"{source.name}:{n}"
        if None in row.values() or None in row:
            raise DatasetError(f"{where}: row has the wrong number of columns")
        valid = parse_bool(row["valid"], where)
        sid, tid, step = row["session_id"].strip(), row["trial_id"].strip(), row["step"].strip()
        if not (sid and tid and step):
            raise DatasetError(f"{where}: session_id, trial_id and step must not be blank")
        key = (sid, tid, step)
        if key in keys:
            raise DatasetError(f"{where}: duplicate (session, trial, step) {key}")
        keys.add(key)                                                  # also for valid=false rows
        if not valid:
            rejected["valid_false"] += 1
            continue
        vals = []
        for col in ("observed_x_before", "command_u", "observed_x_after", "dt_s"):
            try:
                v = float(row[col])
            except ValueError:
                raise DatasetError(f"{where}: {col} is not a number: {row[col]!r}") from None
            if not math.isfinite(v):
                raise DatasetError(f"{where}: {col} is not finite")
            vals.append(v)
        before, u, after, dt = vals
        if not row["state_definition"].strip() or not row["action_definition"].strip():
            raise DatasetError(f"{where}: state_definition and action_definition must not be blank")
        for name, v, rng in (("observed_x_before", before, x_range), ("observed_x_after", after, x_range),
                             ("command_u", u, u_range)):
            if rng is not None and not rng[0] <= v <= rng[1]:
                raise DatasetError(f"{where}: {name}={v} is outside the declared range {list(rng)} "
                                   f"({'x' if name != 'command_u' else 'u'} units: "
                                   f"{x_units if name != 'command_u' else u_units}); degrees vs radians?")
        if abs(dt - expected_dt) > tolerance * expected_dt:
            rejected["dt_out_of_tolerance"] += 1
            continue
        state_defs.add(row["state_definition"].strip())
        action_defs.add(row["action_definition"].strip())
        xs.append([before, u])
        ys.append([after - before])
        groups.append(sid)
    if not xs:
        raise DatasetError(f"{source.name}: no valid rows survived")
    if len(state_defs) != 1 or len(action_defs) != 1:
        raise DatasetError("incompatible state/action definitions are mixed in one file")
    n_sessions = len(set(groups))
    if n_sessions < min_sessions:
        raise DatasetError(
            f"only {n_sessions} session(s); at least {min_sessions} are required because the "
            "downstream evaluation splits by session (train / validation / test need separate "
            "sessions, plus one spare so a split is never empty). Lower --min-sessions on purpose "
            "if you accept a weaker evaluation.")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output, x=np.asarray(xs, dtype=np.float64), y=np.asarray(ys, dtype=np.float64),
             groups=np.asarray(groups, dtype=str))
    report = {
        "schema_version": "motion-io-lab/dataset-manifest-v1",
        "accepted": len(xs), "rejected": rejected, "sessions": n_sessions,
        "min_sessions_required": min_sessions,
        "expected_dt_s": expected_dt, "relative_dt_tolerance": tolerance,
        "x_columns": ["observed_x_before", "command_u"], "y_columns": ["observed_x_after - observed_x_before"],
        "units": {"observed_x": x_units, "command_u": u_units, "dt_s": "s"},
        "declared_ranges": {"x": x_range, "u": u_range},
        "state_definition": next(iter(state_defs)), "action_definition": next(iter(action_defs)),
        "source_csv": source.name, "source_csv_sha256": _sha256(source),
        "npz_sha256": _sha256(output), "tool": f"motion-io-lab {__version__}",
        "note": "Timing and pairing are taken from the logs as given; this converter does not "
                "certify them. Rows come from different trials of the same sessions: split by "
                "session, never by row.",
    }
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def extract(trials: list[Path], dt_s: float, out_csv: Path, *, max_gap_ms: float = 120.0,
            media_offset_ms: float = 0.0) -> int:
    """Pair each valid command with the observation at its send time and ``dt_s`` later."""
    from . import analysis as an
    from .triallog import load_trial

    out_csv = Path(out_csv)
    if out_csv.exists():
        raise FileExistsError(f"{out_csv} exists")
    n = 0
    with out_csv.open("x", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(REQUIRED)
        for d in trials:
            tr = load_trial(d)
            if "observation" not in tr.streams:
                raise DatasetError(f"{d}: no observation stream; cannot pair commands with observations")
            ts = an.extract(tr, media_offset_ms)
            ot, ov = ts.observed
            step = 0
            for t_cmd, u in zip(*ts.commanded):
                step += 1
                t_after = t_cmd + dt_s * 1000.0
                b = an.on_grid(ot, ov, np.array([t_cmd]), max_gap_ms=max_gap_ms)[0]
                a = an.on_grid(ot, ov, np.array([t_after]), max_gap_ms=max_gap_ms)[0]
                ok = np.isfinite(b) and np.isfinite(a)
                w.writerow([tr.session_id, tr.trial_id, step, "" if not ok else f"{b:.6f}", f"{u:.1f}",
                            "" if not ok else f"{a:.6f}", f"{dt_s:.6f}", "true" if ok else "false",
                            "observed marker angle, normalised to angle_scale_rad (camera_image frame)",
                            "commanded pulse width in microseconds (servo_axis_0)"])
                n += 1
    return n


def _range(s: str | None):
    if not s:
        return None
    lo, hi = (float(x) for x in s.split(","))
    return (lo, hi)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motion_io_lab prepare-dataset", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="trial directories -> transitions.csv")
    e.add_argument("trials", nargs="+")
    e.add_argument("--dt", type=float, required=True, help="seconds between before/after observations")
    e.add_argument("--out", required=True)
    e.add_argument("--media-offset-ms", type=float, default=0.0)
    c = sub.add_parser("convert", help="transitions.csv -> .npz + manifest")
    c.add_argument("csv")
    c.add_argument("--out", required=True)
    c.add_argument("--dt", type=float, required=True)
    c.add_argument("--tolerance", type=float, default=0.1)
    c.add_argument("--x-units", required=True, help="e.g. norm_angle")
    c.add_argument("--u-units", required=True, help="e.g. pulse_us")
    c.add_argument("--x-range", help="lo,hi allowed for observed_x, e.g. -2,2")
    c.add_argument("--u-range", help="lo,hi allowed for command_u, e.g. 1450,1550")
    c.add_argument("--min-sessions", type=int, default=MIN_SESSIONS_DEFAULT)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "extract":
            n = extract([Path(t) for t in a.trials], a.dt, Path(a.out), media_offset_ms=a.media_offset_ms)
            print(f"wrote {n} rows to {a.out}")
        else:
            rep = convert(Path(a.csv), Path(a.out), a.dt, a.tolerance, x_units=a.x_units,
                          u_units=a.u_units, x_range=_range(a.x_range), u_range=_range(a.u_range),
                          min_sessions=a.min_sessions)
            print(json.dumps(rep, indent=2, ensure_ascii=False))
    except (DatasetError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
