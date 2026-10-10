"""One-command demo: every stage of the pipeline, entirely on a virtual device.

    python -m motion_io_lab demo --out outputs/demo

What runs: a scripted "person" is recorded (LOOPBACK) -> the recorded commands are replayed N
times -> the synthetic plant's motion is rendered to a video -> the real marker detector reads
the video -> the three series are aligned and compared. Then the failure cases and the
disconnect/resume cases are run.

EVERYTHING HERE IS SYNTHETIC. The device is a Python model of the firmware, the "servo" is
a first-order lag with a dead band, the "camera" is a renderer. The numbers show that the
pipeline and the metrics work; they say nothing about a real servo or a real camera.
"""
from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

import numpy as np

from . import analysis as an
from .session_report import _grid, analyze_motion, pooled_floor_sd_deg, still_marker_sd_deg
from . import calibration
from .devclient import DeviceClient
from .inputs import ScriptedSource
from .link import NullLink, SimWorld
from .manifest import write_run_manifest
from .motions import MOTIONS, Motion, motion_from_trial
from .observe import make_writer, observe, parse_config
from .player import PlayConfig, play
from .recorder import RecordConfig, record
from .session import start_device
from .synth import ServoPlant, VideoSpec, new_trial, render_video
from .triallog import TrialContext, load_trial
from .trialio import open_trial

SESSION = "demo-s1"
NS_MS = 1_000_000
DEG_PER_US = 0.1          # SYNTHETIC assumption: the plant gain AND the "calibration"
CAMERA_LATENCY_S = 0.07   # SYNTHETIC assumption
MOTION_NAMES = ("sweep", "step_hold", "look_wait_nod")


def synthetic_calibration() -> calibration.Calibration:
    pts = [(p, (p - 1500) * DEG_PER_US) for p in (1450, 1475, 1500, 1525, 1550)]
    return calibration.fit(pts, source="synthetic")


class Rig:
    """A session on the virtual device: one persistent plant, a fresh device per trial.

    The keyword arguments switch the assumed noise sources on and off one at a time; the
    ablation script uses them to see which stage of the replay-vs-recording comparison each
    source moves. The defaults are the demo's assumptions.
    """

    def __init__(self, out: Path, seed: int, keep_video: bool = False, *,
                 gain_sigma: float = 0.01, deadband_deg: float = 0.12, deadband_spread: float = 0.5,
                 dropout_p: float = 0.03, distractor_p: float = 0.03,
                 timing_jitter: bool = True, start_state: str = "homed",
                 marker_noise_px: float = 0.0):
        if start_state not in ("homed", "carry_over"):
            raise ValueError("start_state must be 'homed' or 'carry_over'")
        self.out = out
        self.seed = seed
        self.keep_video = keep_video
        self.rng = random.Random(seed)
        self.plant = ServoPlant(deg_per_us=DEG_PER_US, seed=seed, deadband_deg=deadband_deg,
                                deadband_spread=deadband_spread)
        self.spec = VideoSpec()
        self.n = 0
        self.gain_sigma = gain_sigma
        self.dropout_p, self.distractor_p = dropout_p, distractor_p
        self.timing_jitter = timing_jitter
        self.start_state = start_state
        self.marker_noise_px = marker_noise_px
        self.plant_truth: dict[str, tuple[np.ndarray, np.ndarray]] = {}   # trial -> (t_ms, true deg)

    def settings(self) -> dict:
        return {"gain_sigma": self.gain_sigma, "deadband_deg": self.plant.params["deadband_deg"],
                "dropout_p": self.dropout_p, "distractor_p": self.distractor_p,
                "timing_jitter": self.timing_jitter, "start_state": self.start_state,
                "marker_noise_px": self.marker_noise_px}

    def world(self) -> SimWorld:
        self.n += 1
        new_trial(self.plant, self.rng, gain_sigma=self.gain_sigma,
                  home=self.start_state == "homed")
        kw = {} if self.timing_jitter else {"wake_jitter_ms": None, "uplink_ms": (0.4, 0.0),
                                            "downlink_ms": (0.4, 0.0)}
        return SimWorld(seed=self.seed * 1000 + self.n, plant=self.plant, **kw)

    def add_camera(self, trial_id: str, mode: str, world: SimWorld) -> None:
        """Render the plant's motion to a video and run the real detector on the file."""
        tr = load_trial(self.out / trial_id)
        t0 = an.extract(tr).t0_ns
        hist = world.history
        t_s = (np.array([h[0] for h in hist], dtype=np.float64) - t0) / 1e9
        ang = np.array([h[1] for h in hist])
        video = self.out / trial_id / "camera_rendered.mp4"
        self.plant_truth[trial_id] = (t_s * 1000.0, ang)
        render_video(video, t_s, ang, float(t_s[-1]), self.spec, CAMERA_LATENCY_S,
                     seed=self.seed * 7919 + self.n, dropout_p=self.dropout_p,
                     distractor_p=self.distractor_p, noise_px_sigma=self.marker_noise_px)
        cfg = parse_config(self.spec.camera_config("camera_rendered.mp4"))
        cfg = parse_config({**cfg.raw, "source": str(video)})
        writer, _ = make_writer(self.out, SESSION, trial_id, cfg, mode, "rendered_video")
        with writer:
            observe(cfg, writer)
        if not self.keep_video:
            video.unlink()

    def noise_floor(self, cal, angles=(0.0, 3.0), per_angle: int = 3, seconds: float = 3.0) -> dict:
        """Detector noise: render a marker that does not move, read it back through the same
        mp4 + detector path, and take the spread of the detected angle. The spread of five replays
        of a real motion cannot be smaller than this, so it is the floor of the repeatability."""
        sds, names = [], []
        for ang in angles:
            for k in range(1, per_angle + 1):
                tid = f"still-{ang:g}deg-{k:02d}"
                (self.out / tid).mkdir(parents=True)
                video = self.out / tid / "camera_rendered.mp4"
                render_video(video, np.array([0.0, seconds]), np.array([ang, ang]), seconds, self.spec,
                             0.0, seed=self.seed * 104729 + 17 * len(names), dropout_p=self.dropout_p,
                             distractor_p=self.distractor_p, noise_px_sigma=self.marker_noise_px)
                cfg = parse_config({**self.spec.camera_config("x.mp4"), "source": str(video)})
                writer, _ = make_writer(self.out, SESSION, tid, cfg, "DRY_RUN", "rendered_video")
                with writer:
                    observe(cfg, writer)
                if not self.keep_video:
                    video.unlink()
                sds.append(still_marker_sd_deg(self.out, tid))
                names.append(tid)
        return {"trials": names, "sd_deg_per_trial": sds,
                "pooled_sd_deg": pooled_floor_sd_deg(sds),
                "note": "angle SD of a marker that does not move, through the same video and "
                        "detector path; the detector's noise, not the servo's"}


def _record_trial(rig: Rig, motion_name: str, trial_id: str) -> dict:
    fn, dur = MOTIONS[motion_name]
    w = rig.world()
    ctx = TrialContext(SESSION, trial_id, "LOOPBACK", "synthetic", link="virtual",
                       calibration_version=None,
                       config={"motion": motion_name, "input": "scripted + tremor sigma 0.01"})
    with open_trial(rig.out, ctx, with_input=True) as t:
        stats = record(ScriptedSource(fn, dur, tremor_sigma=0.01, seed=rig.seed + rig.n),
                       RecordConfig(), w, t)
    rig.add_camera(trial_id, "LOOPBACK", w)
    return stats


def _play_trial(rig: Rig, motion: Motion, trial_id: str, cfg: PlayConfig, *,
                camera: bool = True, reconnect=None, before_send=None,
                after_resume_hello=None, world: SimWorld | None = None, config: dict | None = None,
                settle_ms: int = 0):
    w = world or rig.world()
    ctx = TrialContext(SESSION, trial_id, "REPLAY", "synthetic", link="virtual",
                       config=config or {"motion": motion.name})
    with open_trial(rig.out, ctx, with_input=False) as t:
        stats = play(motion, cfg, w, t, reconnect=(lambda: reconnect(w)) if reconnect else None,
                     before_send=(lambda i, when: before_send(w, i, when)) if before_send else None,
                     after_resume_hello=after_resume_hello)
    if settle_ms:
        w.sleep_until_ns(w.now_ns() + settle_ms * NS_MS)   # let the device's own lease run out
    if camera:
        rig.add_camera(trial_id, "REPLAY", w)
    return stats, w


# ---------------------------------------------------------------------------
# pacing, disconnect/resume, failure cases


def _long_motion(seconds: float = 30.0) -> Motion:
    t = np.arange(0, seconds * 1000 + 1, 20.0)
    return Motion("long_sine", t, 1500 + 30 * np.sin(2 * np.pi * 0.3 * t / 1000), "synthetic")


def timing_comparison(rig: Rig) -> tuple[dict, dict]:
    """Same 30 s motion, deadline pacing vs sleep-after-send pacing, on the same wake-up model."""
    out, series = {}, {}
    for pacing in ("deadline", "relative"):
        w = SimWorld(seed=rig.seed + 99)           # no plant: timing only
        cfg = PlayConfig(period_ms=20, pacing=pacing, hold_ms=0, drop_if_late_ms=5000,
                         host_slew_us_per_s=None)
        ctx = TrialContext(SESSION, f"timing-{pacing}", "REPLAY", "synthetic", link="virtual")
        with open_trial(rig.out, ctx, with_input=False) as t:
            out[pacing] = play(_long_motion(), cfg, w, t)["timing"]
        tr = load_trial(rig.out / f"timing-{pacing}")
        sets = [r for r in tr.stream("command").of("set") if r["sent_monotonic_ns"]]
        s0 = sets[0]["scheduled_monotonic_ns"]
        series[pacing] = (np.array([(r["scheduled_monotonic_ns"] - s0) / 1e9 for r in sets]),
                          np.array([(r["sent_monotonic_ns"] - r["scheduled_monotonic_ns"]) / 1e6
                                    for r in sets]))
    return out, series


def sweep_motion(span_us: float = 40.0) -> Motion:
    from .motions import sample_motion

    t, x = sample_motion("sweep", 50.0)
    return Motion("sweep", t, 1500.0 + span_us * x, "synthetic")


RESUME_UNPLUG_MS = 1000.0
RESUME_GAP_MS = 600


def resume_cases(rig: Rig) -> dict:
    """Cable out at t = 1.0 s of the sweep for 600 ms, with three policies.

    The device's own lease (120 ms) runs out during the gap, so it stops and latches. A human
    decision (allow_clear) is needed to carry on. While reconnecting, stale copies of the old
    session's ARM and SET lines are fired at the device; all of them must be rejected.
    """
    res: dict = {}
    motion = sweep_motion()
    for policy in (None, "continue", "restart"):
        tid = f"sweep-resume-{policy or 'none'}"
        cfg = PlayConfig(resume=policy, allow_clear=True)
        idx = int(RESUME_UNPLUG_MS / cfg.period_ms)
        state = {"fired": False}
        info: dict = {}

        def before_send(w, i, when, _s=state, _idx=idx):
            if not _s["fired"] and i >= _idx:
                _s["fired"] = True
                w.unplug()

        def reconnect(w):
            w.sleep_until_ns(w.now_ns() + RESUME_GAP_MS * NS_MS)
            w.replug(reboot=False)
            return w

        def attack(client, _info=info):
            old = client.sent_lines[:1] + client.sent_lines[-3:]
            before = dict(client.stats.rejected)
            for ln in old:
                client.link.write_line(ln)
            client.link.sleep_until_ns(client.link.now_ns() + 5 * NS_MS)
            client.pump()
            _info["stale_lines_replayed"] = len(old)
            _info["stale_lines_rejected"] = {k: v - before.get(k, 0) for k, v in
                                             client.stats.rejected.items() if v > before.get(k, 0)}

        stats, w = _play_trial(
            rig, motion, tid, cfg, reconnect=reconnect if policy else None,
            before_send=before_send, after_resume_hello=attack if policy else None,
            config={"motion": "sweep", "resume": policy, "unplug_ms": RESUME_UNPLUG_MS,
                    "gap_ms": RESUME_GAP_MS}, settle_ms=500)
        res[tid] = {"status": stats["status"], "resumed": stats["resumed"],
                    "interruptions": stats["interruptions"], "device": stats["device"],
                    "sim_truth_device_latched_at_end": bool(w.device.fault),
                    **info}
    return res


def resume_metrics(root: Path, cal, nominal_applied: np.ndarray, grid: np.ndarray) -> dict:
    out = {}
    for policy in ("none", "continue", "restart"):
        tid = f"sweep-resume-{policy}"
        tr = load_trial(root / tid)
        ts = an.extract(tr)
        cv = an.to_deg(ts, cal, grid)
        events = {r["event"]: r for r in tr.stream("command").of("event")
                  if r["event"] in ("interrupted", "resume_begin", "clear")}
        arms = [r for r in tr.stream("command").of("reply") if r["reply_cmd"] == "ARM"
                and r["reply_status"] == "ACCEPTED_NOT_EXECUTED"]
        rows = {"applied_samples": int(len(ts.applied[0]))}
        if policy != "none":
            t_int = (events["interrupted"]["received_monotonic_ns"] - ts.t0_ns) / 1e6
            t_arm = (arms[-1]["received_monotonic_ns"] - ts.t0_ns) / 1e6
            rows["outage_ms_interrupt_to_rearm"] = t_arm - t_int
            ap_t, ap_v = ts.applied
            rate = np.abs(np.diff(ap_v)) * cal.deg_per_us / (np.diff(ap_t) / 1000.0)
            after = ap_t[1:] > t_arm
            rows["max_applied_rate_deg_per_s_after_rearm"] = float(rate[after].max()) if after.any() else None
            rows["device_slew_limit_deg_per_s"] = 500 * cal.deg_per_us
            dev = np.abs(cv["applied_deg"] - nominal_applied)
            ok = (grid > t_arm) & np.isfinite(dev) & (dev <= 0.3)
            rows["rejoin_nominal_within_0p3deg_ms_after_rearm"] = (
                float(grid[ok][0] - t_arm) if ok.any() and policy == "continue" else None)
            rows["final_applied_deg"] = float(cal.pulse_to_deg(ap_v[-1]))   # the sweep ends at 0
        out[policy] = rows
    return out


def _raw(world: SimWorld, lines: list[str], dt_ms: float = 5.0) -> list[str]:
    got = []
    for ln in lines:
        world.write_line(ln)
        world.sleep_until_ns(world.now_ns() + int(dt_ms * NS_MS))
        got += [l for _, l in world.read_lines()]
    return got


def failure_cases(seed: int) -> list[dict]:
    """Each case: what was sent, what the device said, whether it latched, whether the plant moved."""
    cases: list[dict] = []

    def fresh():
        plant = ServoPlant(deg_per_us=DEG_PER_US, seed=seed, deadband_spread=0.0)
        w = SimWorld(seed=seed, plant=plant, wake_jitter_ms=None)
        c = DeviceClient(w)
        start_device(c, "virtual")
        return w, c, plant

    def snap(w):
        c = DeviceClient(w)
        c.link.write_line("STATUS")
        w.sleep_until_ns(w.now_ns() + 3 * NS_MS)
        c.pump()
        f = c.last_status.fields
        return {"fault": int(f["fault"]), "armed": int(f["armed"])}

    # 1. replayed ARM+SET after a re-plug: (a) quickly, while the device is still armed and its
    #    lease has not run out; (b) after the lease ran out (the device had already stopped and
    #    latched by itself, so this case alone cannot show what the epoch check does);
    #    (c) after the board rebooted
    for variant in ("quick", "after_lease_expiry", "device_reboot"):
        reboot = variant == "device_reboot"
        w, c, plant = fresh()
        for i in range(5):
            c.send_set(1500 + 6 * (i + 1), 120)
            w.sleep_until_ns(w.now_ns() + 40 * NS_MS)
            c.pump()
        old = c.sent_lines[:1] + c.sent_lines[-3:]
        w.unplug()
        w.sleep_until_ns(w.now_ns() + (30 if variant == "quick" else 400) * NS_MS)
        w.replug(reboot=reboot)
        w.sleep_until_ns(w.now_ns() + 10 * NS_MS)
        before = snap(w)                     # the device as the new connection finds it
        w.sleep_until_ns(w.now_ns() + 5 * NS_MS)
        c2 = DeviceClient(w)
        c2.link.write_line("HELLO")
        w.sleep_until_ns(w.now_ns() + 5 * NS_MS)
        c2.pump()
        angle0 = plant.angle_deg
        got = _raw(w, old)
        cases.append({
            "case": {"quick": "replayed_arm_and_set_after_quick_reconnect",
                     "after_lease_expiry": "replayed_arm_and_set_after_reconnect",
                     "device_reboot": "replayed_arm_and_set_after_reconnect_device_reboot"}[variant],
            "sent": old, "device_replies": got, "all_rejected": all(g.startswith("REJECTED") for g in got),
            "device_before_hello": before,
            "plant_moved_deg": round(abs(plant.angle_deg - angle0), 4), **snap(w)})
    # 2. duplicate seq
    w, c, plant = fresh()
    seq = c.send_set(1520, 500)
    w.sleep_until_ns(w.now_ns() + 30 * NS_MS)
    c.pump()
    dup = f"SET {c.epoch} {seq} 1540 500"          # same seq, different value
    got = _raw(w, [dup])
    w.sleep_until_ns(w.now_ns() + 50 * NS_MS)
    cases.append({"case": "duplicate_seq_with_different_value", "sent": [dup], "device_replies": got,
                  "sim_truth_applied_us_after": int(w.device.applied), **snap(w)})
    # 3. out of range, then a valid command still works (no latch)
    w, c, plant = fresh()
    got = _raw(w, [f"SET {c.epoch} 1 1560 120", f"SET {c.epoch} 2 1520 120"])
    cases.append({"case": "pulse_out_of_range_then_valid", "sent": ["SET .. 1 1560 120", "SET .. 2 1520 120"],
                  "device_replies": got, **snap(w)})
    # 4. negative / signed number (a plain sscanf("%lu") would read -1 as 4294967295)
    w, c, plant = fresh()
    got = _raw(w, [f"SET {c.epoch} -1 1500 120", f"SET {c.epoch} 1 1500 120"])
    cases.append({"case": "negative_sequence_number", "sent": ["SET .. -1 1500 120", "SET .. 1 1500 120"],
                  "device_replies": got, **snap(w)})
    # 5. lease expiry
    w, c, plant = fresh()
    c.send_set(1520, 40)
    w.sleep_until_ns(w.now_ns() + 150 * NS_MS)
    c.pump()
    late = f"SET {c.epoch} 2 1520 40"
    got = _raw(w, [late])
    cases.append({"case": "lease_expired_then_set", "sent": [late],
                  "device_replies": [r.raw for _, r in c.inbox if r.kind == "STOP"] + got,
                  **snap(w)})
    return cases


def hard_step_case(rig: Rig, cal) -> dict:
    """A 50 us step: with the host slew limit on, the host shapes the ramp; with it off, the
    device's own 500 us/s limit does. Either way the applied value arrives later than commanded."""
    t = np.array([0.0, 40.0, 41.0, 1000.0, 1001.0, 1400.0])
    motion = Motion("hard_step", t, np.array([1500.0, 1500.0, 1550.0, 1550.0, 1500.0, 1500.0]), "synthetic")
    out = {}
    for label, slew in (("host_slew_400", 400.0), ("device_slew_only", None)):
        tid = f"hardstep-{label}"
        _play_trial(rig, motion, tid, PlayConfig(host_slew_us_per_s=slew, period_ms=20), camera=False,
                    config={"motion": "hard_step", "host_slew_us_per_s": slew})
        ts = an.extract(load_trial(rig.out / tid))
        g = np.arange(0.0, 1500.0, 5.0)
        cv = an.to_deg(ts, cal, g)
        ap = cv["applied_us"]
        reached = g[np.isfinite(ap) & (ap >= 1500 + 0.9 * 50) & (g > 40)]
        cmd_at = g[np.isfinite(cv["commanded_us"]) & (cv["commanded_us"] >= 1549)]
        out[label] = {
            "max_commanded_minus_applied_us": float(np.nanmax(np.abs(cv["commanded_us"] - ap))),
            "applied_reaches_90pct_ms_after_first_command_of_step": float(reached[0] - 40.0) if len(reached) else None,
            "commanded_reaches_full_ms_after_first_command_of_step": float(cmd_at[0] - 40.0) if len(cmd_at) else None,
            "device_slew_limit_us_per_s": 500, "host_slew_us_per_s": slew}
    return out


def run_motions(rig: Rig, repeats: int, cal, noise_floor_sd_deg: float | None,
                names=MOTION_NAMES) -> tuple[dict, dict]:
    """Record each motion once, replay it ``repeats`` times, and analyse (the core of the demo)."""
    motions: dict = {}
    all_curves: dict = {}
    for name in names:
        _record_trial(rig, name, f"{name}-rec")
        motion = motion_from_trial(load_trial(rig.out / f"{name}-rec"))
        for r in range(1, repeats + 1):
            _play_trial(rig, motion, f"{name}-rep{r:02d}", PlayConfig(),
                        config={"motion": name, "replay_of": f"{name}-rec"})
        rep, curves = analyze_motion(rig.out, name, repeats, cal, plant_truth=rig.plant_truth,
                                     camera_latency_ms=CAMERA_LATENCY_S * 1000.0,
                                     noise_floor_sd_deg=noise_floor_sd_deg)
        motions[name], all_curves[name] = rep, curves
    return motions, all_curves


def run_demo(out: Path, seed: int = 7, repeats: int = 5, keep_video: bool = False) -> dict:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; choose a new --out (logs are never overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    cal = synthetic_calibration()
    (out / "calibration_synthetic.json").write_text(cal.text, encoding="utf-8")
    rig = Rig(out, seed, keep_video)
    summary: dict = {"schema_version": "motion-io-lab/demo-summary-v1", "source": "synthetic",
                     "seed": seed, "repeats": repeats, "calibration_version": cal.version}
    summary["synthetic_assumptions"] = {
        "plant": rig.plant.params, "plant_gain_jitter_per_trial": rig.gain_sigma,
        "plant_dead_band_note": "about 1 us at 0.1 deg/us; real hobby servos usually have a larger "
                                "one (not measured here; scripts/ablation.py tries 5 us)",
        "start_state": "every trial starts with the horn at angle 0 ('homed'); "
                       "scripts/ablation.py shows what carrying the old position over does",
        "camera_latency_s": CAMERA_LATENCY_S, "camera_fps": rig.spec.fps,
        "camera_dropout_p": rig.dropout_p, "camera_distractor_p": rig.distractor_p,
        "camera_dropouts": "independent per frame, never in runs",
        "camera_frame_phase": "frame k is at the same time k/30 s after the first command in every trial",
        "marker_position_jitter_px": rig.marker_noise_px,
        "marker": "an ideal disc: no wobble, no blur beyond the mp4 coding; image noise sigma 3 grey levels",
        "analysis_calibration": "the plant's own gain (0.1 deg/us) is used as the calibration, so the "
                                "calibration is exact by construction",
        "video_time_zero": "known exactly: the video starts at the first command (media offset 0)",
        "link_delay_ms": "0.4 + U(0, 0.3) each way", "host_wake_jitter": "exp(mean 0.12 ms), 1 % spikes to 4 ms",
        "human_input": "scripted motion + gaussian tremor sigma 0.01 (normalised units)",
        "note": "none of these were measured on hardware"}
    floor = rig.noise_floor(cal)
    summary["observation_noise_floor"] = floor
    motions, all_curves = run_motions(rig, repeats, cal, floor["pooled_sd_deg"])
    summary["motions"] = motions
    summary["timing"], timing_series = timing_comparison(rig)
    summary["resume"] = resume_cases(rig)
    sweep_grid = all_curves["sweep"]["grid"]
    nominal = all_curves["sweep"]["mean_applied_deg"]
    summary["resume_metrics"] = resume_metrics(out, cal, nominal, sweep_grid)
    summary["failure_cases"] = failure_cases(seed)
    summary["hard_step_case"] = hard_step_case(rig, cal)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    np.savez_compressed(out / "curves.npz", **{
        f"{m}:{k}": (np.vstack(v) if k == "replays_stack" else v)
        for m, c in all_curves.items() for k, v in _flatten(c).items()})
    np.savez_compressed(out / "timing_series.npz", **{f"{k}_t": v[0] for k, v in timing_series.items()},
                        **{f"{k}_late": v[1] for k, v in timing_series.items()})
    write_report_md(out, summary)
    write_run_manifest(out, "demo", {"out": out, "seed": seed, "repeats": repeats},
                       seed=seed, source="synthetic")
    return summary


def _flatten(c: dict) -> dict:
    out = {"grid": c["grid"]}
    for k in ("observed_deg", "applied_deg", "commanded_deg"):
        out[f"rec_{k}"] = c["recording"][k]
        out[f"replays_{k}"] = np.vstack([r[k] for r in c["replays"]])
        out[f"mean_{k}"] = c[f"mean_{k}"]
        out[f"sd_{k}"] = c[f"sd_{k}"]
    return out


def _f(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{nd}f}"


def write_report_md(out: Path, s: dict) -> None:
    fl = s["observation_noise_floor"]["pooled_sd_deg"]
    L = ["# motion-io-lab demo report", "",
         "**SYNTHETIC.** Virtual device, synthetic plant, rendered video. Not a hardware result.", "",
         f"Detector noise floor (a marker that does not move): SD {fl:.3f} deg, so two independent "
         f"readings differ by about {math.sqrt(2) * fl:.3f} deg RMS.", "",
         "## Per motion (5 replays of one recording)", "",
         "| motion | lag cmd->obs ms | cmd vs applied RMS deg | applied vs observed RMS deg (lag aligned) |"
         " replay vs recording RMS deg: commanded / applied / observed | repeat SD deg (observed) | "
         "repeat SD minus noise floor | invalid frames | interpolated share |",
         "|---|---|---|---|---|---|---|---|---|"]
    for m, r in s["motions"].items():
        L.append(f"| {m} | {_f(r['lag_ms_commanded_to_observed']['mean'], 0)} | "
                 f"{_f(r['rms_deg_commanded_vs_applied']['mean'], 3)} | "
                 f"{_f(r['rms_deg_applied_vs_observed_lag_aligned']['mean'], 3)} | "
                 f"{_f(r['rms_deg_replay_vs_recording_commanded']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_applied']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_observed']['mean'], 3)} | "
                 f"{_f(r['repeat_sd_deg']['observed_deg']['pooled_sd'], 3)} | "
                 f"{_f(r['repeat_sd_deg']['observed_deg']['pooled_sd_minus_floor'], 3)} | "
                 f"{r['observation_invalid']}/{r['observation_frames']} | "
                 f"{_f(100 * r['observation_bridged_fraction']['mean'], 0)} % |")
    L += ["", "## Reading the three stages", "",
          "| motion | replay vs recording: commanded / applied / observed | same, after the first 0.5 s | "
          "replay vs replay (observed) | commanded, sample by sample | send lateness p95 ms |",
          "|---|---|---|---|---|---|"]
    for m, r in s["motions"].items():
        L.append(f"| {m} | "
                 f"{_f(r['rms_deg_replay_vs_recording_commanded']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_applied']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_observed']['mean'], 3)} | "
                 f"{_f(r['rms_deg_replay_vs_recording_commanded_after_500ms']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_applied_after_500ms']['mean'], 3)} / "
                 f"{_f(r['rms_deg_replay_vs_recording_observed_after_500ms']['mean'], 3)} | "
                 f"{_f(r['rms_deg_replay_vs_replay_observed']['mean'], 3)} | "
                 f"{_f(r['rms_deg_commanded_replay_vs_recording_by_sample']['mean'], 3)} | "
                 f"{_f(r['send_lateness_ms_p95']['mean'], 2)} |")
    L += ["", "The commanded stage is measured on a 10 ms grid, so it reflects how far the send times "
              "wander (about 1 ms) against the grid step; compared sample by sample the replay sends "
              "the same values. Observed-stage differences close to the noise floor say nothing about the "
              "servo.", "",
          "## Lag estimate against the lag a noiseless servo would give", "",
          "| motion | commanded->observed estimated / expected ms | applied->observed estimated / expected ms |",
          "|---|---|---|"]
    for m, r in s["motions"].items():
        L.append(f"| {m} | {_f(r['lag_ms_commanded_to_observed']['mean'], 1)} / "
                 f"{_f(r['lag_ms_commanded_to_observed_expected']['mean'], 1)} | "
                 f"{_f(r['lag_ms_applied_to_observed']['mean'], 1)} / "
                 f"{_f(r['lag_ms_applied_to_observed_expected']['mean'], 1)} |")
    L += ["", "## Pacing (30 s, 20 ms period)", "",
          "| pacing | p95 lateness ms | max ms | drift ppm |", "|---|---|---|---|"]
    for k, t in s["timing"].items():
        drift = _f(t["drift_ppm"], 1)
        L.append(f"| {k} | {_f(t['lateness_ms_p95'], 3)} | {_f(t['lateness_ms_max'], 3)} | "
                 f"{'0.0' if drift == '-0.0' else drift} |")
    L += ["", "## Failure cases", ""]
    L.append("(`fault` and `armed` are read at the end of each case. For the re-plug cases the device "
             "state when the new connection found it is listed too: after the lease ran out the device "
             "had already stopped and latched by itself, so only the quick case shows the epoch check "
             "refusing old lines on an armed device.)")
    L.append("")
    for c in s["failure_cases"]:
        pre = c.get("device_before_hello")
        pre_txt = f" (before HELLO: fault={pre['fault']} armed={pre['armed']})" if pre else ""
        L.append(f"- `{c['case']}`: replies {c['device_replies']}; fault={c['fault']} "
                 f"armed={c['armed']}{pre_txt}")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
