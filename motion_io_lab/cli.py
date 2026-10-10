"""Command line. Run ``python -m motion_io_lab --help``.

The commands that create a trial or output folder (demo, record, play, observe, camera-probe,
probe, console) write ``run_manifest.json`` into it; the single-file commands (calibrate,
export-motion, summarize-session, prepare-dataset, ...) do not (``prepare-dataset convert``
writes its own ``<output>.manifest.json``). The default target of record/play is ``dry``:
nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .manifest import write_run_manifest

HW_HELP = ("Real hardware needs --port and --i-checked-hardware AND a typed confirmation; the "
           "firmware additionally refuses ARM until a person sets HARDWARE_VERIFIED. "
           "See docs/HARDWARE_RUN.md.")


def _add_trial_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", required=True, help="output root; <trial-id>/ is created inside")
    p.add_argument("--session-id", required=True)
    p.add_argument("--trial-id", required=True)
    p.add_argument("--target", choices=["dry", "loopback", "hardware"], default="dry",
                   help="dry (default): nothing is sent; loopback: virtual device; hardware: "
                        "serial port. " + HW_HELP)
    p.add_argument("--port", help="serial port for --target hardware")
    p.add_argument("--i-checked-hardware", action="store_true",
                   help="you have checked wiring, fixing, power and the e-stop (self-declaration)")
    p.add_argument("--realtime", action="store_true",
                   help="use the real clock for dry/loopback runs (default: virtual time)")
    p.add_argument("--calibration", default=None,
                   help="calibration JSON (from `calibrate`); its version goes into every log row "
                        "(default: null, the log then says no calibration was in use)")


def _calibration_version(path: str | None) -> str | None:
    """The version (hash prefix) of a calibration file, for the ``calibration_version`` column."""
    if not path:
        return None
    from . import calibration

    try:
        return calibration.load(path).version
    except (ValueError, OSError, KeyError) as exc:
        sys.exit(f"error: --calibration {path}: {exc}")


def _link_for(args, need_real_time: bool):
    from .link import NullLink, RealtimeLoopback, SerialLink, SimWorld

    if args.target == "dry":
        return NullLink(realtime=args.realtime or need_real_time)
    if args.target == "loopback":
        return RealtimeLoopback() if (args.realtime or need_real_time) else SimWorld(seed=0)
    if not args.port or not args.i_checked_hardware:
        sys.exit("error: --target hardware needs --port and --i-checked-hardware. " + HW_HELP)
    if not sys.stdin.isatty():
        sys.exit("error: hardware runs need an interactive terminal for the confirmation")
    from .link import LinkDown

    try:
        return SerialLink(args.port)
    except LinkDown as exc:
        sys.exit(f"error: {exc}")


def _confirm_for(args):
    from .session import ask_on_terminal

    return ask_on_terminal(args.port) if args.target == "hardware" else None


def _int_at_least(n: int):
    def conv(text: str) -> int:
        try:
            v = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
        if v < n:
            raise argparse.ArgumentTypeError(f"must be at least {n} (a spread needs {n}+ replays)")
        return v
    return conv


def cmd_demo(a) -> int:
    from .demo import run_demo

    try:
        s = run_demo(Path(a.out), seed=a.seed, repeats=a.repeats, keep_video=a.keep_video)
    except FileExistsError as exc:
        sys.exit(f"error: {exc}")
    print(f"SYNTHETIC demo written to {a.out}  (report.md, summary.json, run_manifest.json)")
    for m, r in s["motions"].items():
        print(f"  {m:14s} replay-vs-recording RMS {r['rms_deg_replay_vs_recording_observed']['mean']:.3f} deg"
              f" | repeat SD {r['repeat_sd_deg']['observed_deg']['pooled_sd']:.3f} deg"
              f" | lag cmd->obs {r['lag_ms_commanded_to_observed']['mean']:.0f} ms")
    return 0


def cmd_record(a) -> int:
    from . import motions
    from .devclient import DeviceRefused, DeviceTimeout
    from .link import LinkDown
    from .session import NotConfirmed
    from .inputs import KeyboardSource, ScriptedSource, TouchPadServer
    from .recorder import RecordConfig, record
    from .triallog import TrialContext
    from .trialio import open_trial, resolve

    human = a.source in ("keyboard", "touch") or a.source.startswith("joycon:")
    mode, link_kind, source = resolve("record", a.target, human_input=human)
    if a.source.startswith("script:"):
        name = a.source.split(":", 1)[1]
        if name not in motions.MOTIONS:
            sys.exit(f"error: unknown motion {name!r}; choose from {sorted(motions.MOTIONS)}")
        fn, dur = motions.MOTIONS[name]
        src = ScriptedSource(fn, dur, seed=a.seed)
    elif a.source == "touch":
        src = TouchPadServer(port=a.http_port, lan=a.lan)
        print(f"Open on the phone (same network if --lan): {src.url()}")
        print("Press Ctrl+C to stop recording.")
    elif a.source.startswith("joycon:"):
        from .inputs import JoyConStdinSource

        src = JoyConStdinSource(a.source.split(":", 1)[1])
    elif a.source == "keyboard":
        if not sys.stdin.isatty():
            sys.exit("error: --source keyboard needs an interactive terminal")
        src = KeyboardSource()
    else:
        sys.exit("error: --source must be keyboard, touch, joycon:<element> or script:<motion>")
    link = _link_for(a, need_real_time=human)
    try:
        ctx = TrialContext(a.session_id, a.trial_id, mode, source, link=link_kind,
                           calibration_version=_calibration_version(a.calibration),
                           config={"source": a.source, "span_us": a.span_us})
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    cfg = RecordConfig(span_us=a.span_us, max_seconds=a.max_seconds)
    try:
        with open_trial(a.out, ctx, with_input=True) as t:
            if isinstance(src, KeyboardSource):
                with src:
                    print("a/d or arrows: move, space: centre, q: quit")
                    stats = record(src, cfg, link, t, confirm=_confirm_for(a))
            else:
                # Ctrl+C ends the recording normally (record() closes the log); it is not a failure
                stats = record(src, cfg, link, t, confirm=_confirm_for(a))
    except FileExistsError as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit("interrupted before recording started; nothing was published")
    except (LinkDown, DeviceTimeout, NotConfirmed, DeviceRefused) as exc:
        sys.exit(f"stopped: {exc}")
    write_run_manifest(Path(a.out) / a.trial_id, "record", vars(a), source=source)
    print(json.dumps(stats, indent=2))
    return 0


def cmd_play(a) -> int:
    from .devclient import DeviceRefused, DeviceTimeout
    from .link import LinkDown
    from .motions import load_motion
    from .player import PlayConfig, build_plan, play
    from .session import NotConfirmed
    from .triallog import TrialContext
    from .trialio import open_trial, resolve

    mode, link_kind, source = resolve("play", a.target)
    try:
        motion = load_motion(a.motion)
        cfg = PlayConfig(speed=a.speed, start_ms=a.start_ms, end_ms=a.end_ms, resume=a.resume,
                         allow_clear=a.allow_clear, pacing=a.pacing,
                         max_duration_ms=None if a.allow_long else 120_000.0)
        build_plan(motion, cfg)       # a bad --start-ms/--end-ms fails here, before any file is made
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {exc}")
    link = _link_for(a, need_real_time=False)
    reconnect = None
    if a.target == "hardware" and a.resume:
        from .link import SerialLink

        def reconnect():
            try:
                link.close()
            except Exception:  # noqa: BLE001
                pass
            while True:
                input("The cable is out or the board was reset. Plug it back in, wait for the port to "
                      "reappear, then press Enter: ")
                try:
                    return SerialLink(a.port)
                except LinkDown as exc:
                    print(f"still not there: {exc}")
    try:
        ctx = TrialContext(a.session_id, a.trial_id, mode, source, link=link_kind,
                           calibration_version=_calibration_version(a.calibration),
                           config={"motion": motion.name, "origin": motion.origin, "speed": a.speed,
                                   "start_ms": a.start_ms, "end_ms": a.end_ms})
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    try:
        with open_trial(a.out, ctx, with_input=False) as t:
            stats = play(motion, cfg, link, t, confirm=_confirm_for(a), reconnect=reconnect)
    except FileExistsError as exc:
        sys.exit(f"error: {exc}")
    except (NotConfirmed, DeviceRefused, LinkDown, DeviceTimeout) as exc:
        sys.exit(f"stopped: {exc}")
    write_run_manifest(Path(a.out) / a.trial_id, "play", vars(a), source=source)
    print(json.dumps(stats, indent=2))
    return 0


def cmd_export_motion(a) -> int:
    from .motions import load_motion, write_motion_file

    try:
        m = load_motion(a.trial)
        write_motion_file(a.out, m.t_ms, m.pulse_us, m.name, m.origin)
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {exc}")
    print(f"wrote {a.out} ({len(m.t_ms)} samples, {m.duration_ms:.0f} ms)")
    return 0


def cmd_validate(a) -> int:
    from .triallog import iter_trials, load_trial, TrialLogError

    bad = 0
    n = 0
    for p in a.paths:
        if not Path(p).is_dir():
            n += 1
            bad += 1
            print(f"FAIL {p}")
            print("    not a directory (give a trial folder, or a folder of trial folders)"
                  if Path(p).exists() else "    no such folder")
            continue
        for d in iter_trials(p):
            n += 1
            try:
                t = load_trial(d)
                print(f"ok   {d}  streams={sorted(t.streams)}  mode={t.mode}")
            except TrialLogError as exc:
                bad += 1
                print(f"FAIL {d}")
                for e in exc.errors[:20]:
                    print("   ", e)
    if n == 0:
        print("no trial directories found")
        return 1
    return 1 if bad else 0


def cmd_calibrate(a) -> int:
    from . import calibration

    try:
        cal = calibration.fit_csv(a.points, source=a.source)
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {exc}")
    out = Path(a.out)
    if out.exists():
        sys.exit(f"error: {out} exists; calibrations are versioned by hash, write a new file")
    out.write_text(cal.text, encoding="utf-8")
    print(f"deg_per_us={cal.deg_per_us:.5f} rms_residual={cal.rms_residual_deg:.3f} deg "
          f"version={cal.version}")
    return 0


def cmd_analyze(a) -> int:
    from . import analysis as an
    from . import calibration
    from .triallog import load_trial
    import numpy as np

    from .triallog import TrialLogError

    try:
        cal = calibration.load(a.calibration) if a.calibration else None
        ts = an.extract(load_trial(a.trial), media_offset_ms=a.media_offset_ms)
    except (ValueError, OSError, KeyError, TrialLogError) as exc:
        sys.exit(f"error: {exc}")
    dur = float(max(ts.commanded[0].max(), ts.applied[0].max() if len(ts.applied[0]) else 0)) + 600
    grid = np.arange(0.0, dur, an.GRID_MS)
    cv = an.to_deg(ts, cal, grid)
    out = {"trial_id": ts.trial_id, "notes": ts.notes,
           "commands": int(len(ts.commanded[0])), "applied_reports": int(len(ts.applied[0])),
           "observed_valid": int(len(ts.observed[0])), "observed_invalid": ts.obs_invalid}
    if cal and "observed_deg" in cv:
        out["lag_ms_commanded_to_observed"] = an.estimate_lag_ms(cv["commanded_deg"], cv["observed_deg"])
        out["lag_ms_applied_to_observed"] = an.estimate_lag_ms(cv["applied_deg"], cv["observed_deg"])
        out["note"] = ("lag = total delay between two series (servo response + camera pipeline "
                       "+ the time-alignment assumption), not camera latency")
    print(json.dumps(out, indent=2))
    return 0


def cmd_virtual_device(a) -> int:
    import time

    from .link import PtyDeviceServer

    with PtyDeviceServer(hardware_verified=not a.unverified) as srv:
        print(f"virtual device on {srv.path}  (Ctrl+C to stop; SYNTHETIC)")
        try:
            time.sleep(a.seconds)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_summarize_session(a) -> int:
    from . import calibration
    from .session_report import summarize_session

    out = Path(a.out)
    if out.exists():
        sys.exit(f"error: {out} exists; summaries are not overwritten")
    try:
        cal = calibration.load(a.calibration)
        still = [t for t in a.still_trials.split(",") if t] if a.still_trials else None
        summary = summarize_session(Path(a.root), cal, a.motions.split(","), a.repeats, still)
    except (ValueError, OSError, KeyError) as exc:
        sys.exit(f"error: {exc}")
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}  source={summary['source']}")
    for m, r in summary["motions"].items():
        sd = r["repeat_sd_deg"]["observed_deg"]["pooled_sd"]
        rr = r["rms_deg_replay_vs_recording_observed"]["mean"]
        print(f"  {m:14s} replay-vs-recording RMS {rr:.3f} deg | repeat SD {sd:.3f} deg | "
              f"lag cmd->obs {r['lag_ms_commanded_to_observed']['mean']:.0f} ms")
    return 0


def cmd_schema(a) -> int:
    from .triallog import schema_dict

    text = json.dumps(schema_dict(), indent=2, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def cmd_compare_reference(a) -> int:
    from .reference import level_of_files

    ref = a.reference
    if ref is None:       # next to the package source first, then the current folder
        for cand in (Path(__file__).resolve().parent.parent / "reference_results" / "demo_summary.json",
                     Path("reference_results/demo_summary.json")):
            if cand.exists():
                ref = str(cand)
                break
        else:
            sys.exit("error: reference_results/demo_summary.json not found; pass --reference")
    try:
        level, diffs = level_of_files(a.summary, ref)
    except (OSError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    if level == "different":
        print(f"{len(diffs)} difference(s) outside tolerance:")
        for d in diffs[:40]:
            print("  ", d)
        return 1
    if level == "identical":
        print("summary is identical to the reference (every number matches to 1e-6)")
        return 0
    print(f"summary matches the reference only within the cross-build tolerance: {len(diffs)} "
          "number(s) computed from the decoded video differ slightly (expected with another "
          "OpenCV / ffmpeg build; see motion_io_lab/reference.py)")
    for d in diffs[:10]:
        print("  ", d)
    if a.require_identical:
        print("error: --require-identical was given")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m motion_io_lab", description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="whole pipeline on a virtual device (SYNTHETIC)")
    p.add_argument("--out", default="outputs/demo")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--repeats", type=_int_at_least(2), default=5,
                   help="replays per motion (at least 2: the spread needs two)")
    p.add_argument("--keep-video", action="store_true")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("record", help="record a person's input (default target: dry)")
    _add_trial_args(p)
    p.add_argument("--source", default="keyboard",
                   help="keyboard | touch | joycon:<element name> (stdin from joycon_probe) | script:<motion>")
    p.add_argument("--span-us", type=float, default=40.0)
    p.add_argument("--max-seconds", type=float, default=30.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--http-port", type=int, default=8765)
    p.add_argument("--lan", action="store_true",
                   help="listen on all interfaces (default 127.0.0.1); the token stays mandatory")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("play", help="replay a motion file or a recorded trial (default target: dry)")
    _add_trial_args(p)
    p.add_argument("--motion", required=True, help="trial directory or motion-v1 .jsonl")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--start-ms", type=float, default=0.0)
    p.add_argument("--end-ms", type=float, default=None)
    p.add_argument("--resume", choices=["continue", "restart"], default=None,
                   help="after an interruption (default: stop)")
    p.add_argument("--allow-clear", action="store_true",
                   help="with --resume: you decide to CLEAR a latched fault")
    p.add_argument("--pacing", choices=["deadline", "relative"], default="deadline")
    p.add_argument("--allow-long", action="store_true",
                   help="allow plans longer than 120 s (the limit catches a t_ms written in the wrong unit)")
    p.set_defaults(fn=cmd_play)

    p = sub.add_parser("export-motion", help="write the commanded trajectory of a trial as motion-v1")
    p.add_argument("--trial", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export_motion)

    p = sub.add_parser("validate", help="check trial directories against the schema")
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("calibrate", help="fit pulse_us -> angle_deg from measured points (csv)")
    p.add_argument("--points", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--source", default="hardware", choices=["hardware", "synthetic"])
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("analyze", help="series summary and lag estimate for one trial")
    p.add_argument("--trial", required=True)
    p.add_argument("--calibration")
    p.add_argument("--media-offset-ms", type=float, default=0.0,
                   help="where a video file's time zero sits relative to the first command "
                        "(0 = assumed to start together)")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("summarize-session", help="per-motion repeat statistics of a recorded session "
                                                "(trials named <motion>-rec / <motion>-repNN)")
    p.add_argument("--root", required=True)
    p.add_argument("--calibration", required=True)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--motions", default="sweep,step_hold,look_wait_nod")
    p.add_argument("--still-trials", default=None,
                   help="comma list of observation-only trials with the marker NOT moving "
                        "(e.g. still-01,still-02,still-03): the detector's noise floor")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_summarize_session)

    p = sub.add_parser("schema", help="print the trial log schema v1 as JSON")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_schema)

    p = sub.add_parser("compare-reference", help="compare a demo summary.json with reference_results/")
    p.add_argument("--summary", required=True)
    p.add_argument("--require-identical", action="store_true",
                   help="exit 1 unless every number matches (use with the pinned requirements-lock.txt)")
    p.add_argument("--reference", default=None,
                   help="default: reference_results/demo_summary.json next to the package source")
    p.set_defaults(fn=cmd_compare_reference)

    p = sub.add_parser("virtual-device", help="serve the virtual device on a pseudo-terminal")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--unverified", action="store_true", help="behave like HARDWARE_VERIFIED=false")
    p.set_defaults(fn=cmd_virtual_device)

    for name, mod in (("observe", "observe"), ("camera-probe", "camera_probe"),
                      ("prepare-dataset", "prepare_real_dataset"), ("probe", "bounded_probe"),
                      ("console", "serial_console")):
        sub.add_parser(name, help=f"see: python -m motion_io_lab {name} --help", add_help=False)
    return ap


_PASS_THROUGH = {"observe": "observe", "camera-probe": "camera_probe",
                 "prepare-dataset": "prepare_real_dataset", "probe": "bounded_probe",
                 "console": "serial_console"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _PASS_THROUGH:
        import importlib

        mod = importlib.import_module(f"motion_io_lab.{_PASS_THROUGH[argv[0]]}")
        r = mod.main(argv[1:])
        return r if isinstance(r, int) else 0
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
