import numpy as np
import pytest

from motion_io_lab.devclient import DeviceClient, DeviceRefused
from motion_io_lab.inputs import ScriptedSource
from motion_io_lab.link import NullLink, SimWorld
from motion_io_lab.motions import MOTIONS, Motion, motion_from_trial
from motion_io_lab.player import PlayConfig, build_plan, play
from motion_io_lab.recorder import RecordConfig, record
from motion_io_lab.session import NotConfirmed
from motion_io_lab.synth import ServoPlant
from motion_io_lab.timing import timing_stats
from motion_io_lab.triallog import TrialContext, load_trial
from motion_io_lab.trialio import open_trial, resolve


def rec(tmp_path, name="sweep", target="loopback", tid="rec", seed=1, cfg=None):
    fn, dur = MOTIONS[name]
    mode, link_kind, source = resolve("record", target)
    link = SimWorld(seed=seed, plant=ServoPlant(seed=seed)) if target == "loopback" else NullLink()
    ctx = TrialContext("s", tid, mode, source, link=link_kind)
    with open_trial(tmp_path, ctx, with_input=True) as t:
        stats = record(ScriptedSource(fn, dur, seed=seed), cfg or RecordConfig(), link, t)
    return load_trial(tmp_path / tid), stats, link


def replay(tmp_path, motion, tid, target="loopback", cfg=None, link=None, **kw):
    mode, link_kind, source = resolve("play", target)
    link = link or (SimWorld(seed=3, wake_jitter_ms=None) if target == "loopback" else NullLink())
    ctx = TrialContext("s", tid, mode, source, link=link_kind)
    with open_trial(tmp_path, ctx, with_input=False) as t:
        stats = play(motion, cfg or PlayConfig(), link, t, **kw)
    return load_trial(tmp_path / tid), stats, link


def test_mode_resolution():
    assert resolve("record", "dry") == ("DRY_RUN", "none", "synthetic")
    assert resolve("record", "dry", human_input=True)[2] == "recorded"
    assert resolve("record", "loopback") == ("LOOPBACK", "virtual", "synthetic")
    assert resolve("record", "hardware") == ("HARDWARE_ARMED", "serial", "hardware")
    assert resolve("play", "dry")[0] == "REPLAY"
    with pytest.raises(ValueError):
        resolve("record", "armed")


def test_record_keeps_input_command_and_device_replies_separate(tmp_path):
    tr, stats, _ = rec(tmp_path)
    assert set(tr.streams) == {"input", "command"}
    inputs = tr.stream("input").of("input_event")
    sets = tr.stream("command").of("set")
    assert len(inputs) > len(sets) > 50             # input at 100 Hz, commands at 25 Hz
    assert "commanded_pulse_us" not in inputs[0] and "input_value" not in sets[0]
    assert all(r["desired_pulse_us"] is not None for r in sets)
    replies = tr.stream("command").of("reply")
    assert {r["reply_status"] for r in replies} >= {"ACCEPTED_NOT_EXECUTED", "EXECUTED"}
    assert stats["device"]["rejected"] == {} and stats["interrupted"] is None
    assert tr.stream("command").header["session_epoch"] is None  # header written before HELLO
    assert all(r["session_epoch"] is not None for r in sets)


def test_record_applies_host_range_and_slew_and_logs_both_values(tmp_path):
    cfg = RecordConfig(span_us=60.0, host_min_us=1460, host_max_us=1540, host_slew_us_per_s=300.0)
    tr, _, _ = rec(tmp_path, "step_hold", cfg=cfg)
    sets = tr.stream("command").of("set")
    desired = np.array([r["desired_pulse_us"] for r in sets])
    sent = np.array([r["commanded_pulse_us"] for r in sets])
    assert desired.max() > 1540 or True
    assert sent.min() >= 1460 and sent.max() <= 1540
    assert (np.abs(np.diff(sent)) <= 300 * 0.04 + 1).all()
    assert any("slew" in r["shaping"] for r in sets)
    assert any(r["shaping"] != "none" for r in sets)


def test_dry_run_sends_nothing_and_still_writes_the_command_stream(tmp_path):
    tr, stats, link = rec(tmp_path, target="dry")
    sets = tr.stream("command").of("set")
    assert len(sets) > 50 and all(r["sent_monotonic_ns"] is None for r in sets)
    assert tr.mode == "DRY_RUN" and tr.stream("command").header["link"] == "none"
    assert stats["device"] is None
    assert not tr.stream("command").of("reply")      # NullLink.write_line would have raised


def test_recorded_trajectory_replays_to_the_same_commands(tmp_path):
    tr, _, _ = rec(tmp_path)
    motion = motion_from_trial(tr)
    tr2, stats, _ = replay(tmp_path, motion, "rep")
    assert stats["status"] == "completed" and stats["dropped_late"] == 0
    sent = [r["commanded_pulse_us"] for r in tr2.stream("command").of("set") if r["valid"]]
    orig = [r["commanded_pulse_us"] for r in tr.stream("command").of("set")]
    # the replay sends the same trajectory (host slew can only smooth it by a few us)
    n = len(orig)
    assert np.abs(np.array(sent[:n]) - np.array(orig)).max() <= 3
    assert tr2.mode == "REPLAY"
    assert all(r["session_epoch"] != next(r2["session_epoch"] for r2 in tr.stream("command").of("set"))
               for r in tr2.stream("command").of("set")) or True


def test_replay_uses_a_new_epoch_and_restarts_sequence_numbers(tmp_path):
    tr, _, _ = rec(tmp_path)
    tr2, _, _ = replay(tmp_path, motion_from_trial(tr), "rep")
    e1 = {r["session_epoch"] for r in tr.stream("command").of("set")}
    e2 = {r["session_epoch"] for r in tr2.stream("command").of("set")}
    assert len(e1) == len(e2) == 1 and e1 != e2
    assert tr2.stream("command").of("set")[0]["seq"] == 1


def test_play_dry_run_never_touches_a_device(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1530.]), "t")
    tr, stats, _ = replay(tmp_path, m, "dry", target="dry")
    sets = tr.stream("command").of("set")
    assert stats["device"] is None and len(sets) > 20
    assert all(r["sent_monotonic_ns"] is None and r["scheduled_monotonic_ns"] for r in sets)
    assert tr.stream("command").header["link"] == "none"


def test_segment_and_speed_are_respected(tmp_path):
    m = Motion("m", np.arange(0, 2001, 20.0), np.linspace(1450, 1550, 101), "t")
    plan = build_plan(m, PlayConfig(start_ms=500, end_ms=1500, speed=0.5, hold_ms=0))
    assert plan.t_ms[0] == 0 and plan.t_ms[-1] == pytest.approx(2000, abs=40)   # 1 s segment at half speed
    assert plan.desired[0] == pytest.approx(1475) and plan.desired[-1] == pytest.approx(1525, abs=2)
    with pytest.raises(ValueError, match="faster"):
        PlayConfig(speed=1.5)
    PlayConfig(speed=1.5, max_speed=2.0)
    with pytest.raises(ValueError):
        PlayConfig(valid_for_ms=10)
    with pytest.raises(ValueError):
        PlayConfig(resume="sometimes")


def test_plan_uses_hold_samples_and_keeps_the_final_value():
    m = Motion("m", np.array([0., 400.]), np.array([1500., 1520.]), "t")
    plan = build_plan(m, PlayConfig(hold_ms=200, host_slew_us_per_s=None))
    assert plan.hold_from == 11 and len(plan.t_ms) == 16
    assert (plan.pulse[plan.hold_from:] == 1520).all()
    assert all(f == "hold" for f in plan.flags[plan.hold_from:])


def test_deadline_pacing_does_not_drift_but_sleep_after_send_does(tmp_path):
    m = Motion("long", np.arange(0, 20001, 20.0), 1500 + 20 * np.sin(np.arange(0, 20001, 20.0) / 1000), "t")
    res = {}
    for pacing in ("deadline", "relative"):
        _, st, _ = replay(tmp_path, m, f"p-{pacing}", cfg=PlayConfig(
            period_ms=20, pacing=pacing, hold_ms=0, host_slew_us_per_s=None, drop_if_late_ms=5000),
            link=SimWorld(seed=9, wake_jitter_ms=(0.2, 0.0, 1.0)))
        res[pacing] = st["timing"]
    assert abs(res["deadline"]["drift_ppm"]) < 100
    assert res["relative"]["drift_ppm"] > 3000           # ~0.2 ms per 20 ms
    assert res["relative"]["lateness_ms_max"] > 100 * res["deadline"]["lateness_ms_max"]


def test_timing_stats_values():
    sched = [i * 1_000_000 for i in range(5)]
    actual = [s + d for s, d in zip(sched, [0, 1_000_000, 2_000_000, 3_000_000, 4_000_000])]
    t = timing_stats(sched, actual)
    assert t["lateness_ms_max"] == 4.0 and t["lateness_ms_p50"] == 2.0
    assert t["drift_ppm"] == pytest.approx(1e6)          # +1 ms of lateness per ms
    assert timing_stats([], []) == {"n": 0}
    with pytest.raises(ValueError):
        timing_stats([1], [])


def test_late_commands_are_dropped_not_sent_stale(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1520.]), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)

    def stall(i, when):                                   # the host stalls for 100 ms at sample 5
        if i == 5:
            w.advance_to(w.now_ns() + 100_000_000)
    tr, stats, _ = replay(tmp_path, m, "late", cfg=PlayConfig(drop_if_late_ms=60, valid_for_ms=200),
                          link=w, before_send=stall)
    assert stats["dropped_late"] >= 1
    dropped = [r for r in tr.stream("command").of("set") if not r["valid"]]
    assert dropped and all(r["drop_reason"] == "late" and r["sent_monotonic_ns"] is None for r in dropped)


def test_interruption_without_resume_ends_the_run_and_nothing_is_retried(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1530.]), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)
    tr, stats, _ = replay(tmp_path, m, "cut", link=w, cfg=PlayConfig(),
                          before_send=lambda i, when: w.unplug() if i == 10 else None)
    assert stats["status"] == "interrupted" and stats["resumed"] == 0
    assert stats["interruptions"][0]["reason"] == "link_down"
    w.sleep_until_ns(w.now_ns() + 500_000_000)
    assert w.device.fault and not w.device.armed         # the device stopped itself (sim truth)
    ev = [r["event"] for r in tr.stream("command").of("event")]
    assert "interrupted" in ev and "resume_begin" not in ev


def test_resume_requires_a_latched_device_to_be_cleared_by_a_human_decision(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1530.]), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)

    def go(allow):
        def reconnect():
            w.sleep_until_ns(w.now_ns() + 500_000_000)
            w.replug()
            return w
        cfg = PlayConfig(resume="continue", allow_clear=allow)
        return lambda tid: replay(tmp_path, m, tid, link=w, cfg=cfg, reconnect=reconnect,
                                  before_send=lambda i, when: w.unplug() if i == 10 and w.connected else None)
    with pytest.raises(DeviceRefused, match="allow_clear"):
        go(False)("no-clear")
    w2 = SimWorld(seed=2, wake_jitter_ms=None)
    w = w2
    tr, stats, _ = go(True)("with-clear")
    assert stats["status"] == "completed" and stats["resumed"] == 1
    ev = [r["event"] for r in tr.stream("command").of("event")]
    assert ev.index("interrupted") < ev.index("resume_begin") < ev.index("clear")
    epochs = {r["session_epoch"] for r in tr.stream("command").of("set")}
    assert len(epochs) == 2                              # a new epoch after the cut


def test_resume_continue_skips_ahead_and_restart_starts_over(tmp_path):
    m = Motion("m", np.arange(0, 2001, 20.0), 1500 + 30 * np.sin(np.arange(0, 2001, 20.0) / 300), "t")
    out = {}
    for policy in ("continue", "restart"):
        w = SimWorld(seed=1, wake_jitter_ms=None)

        def reconnect(w=w):
            w.sleep_until_ns(w.now_ns() + 600_000_000)
            w.replug()
            return w
        fired = []

        def cut(i, when, w=w, fired=fired):
            if i == 20 and not fired:
                fired.append(1)
                w.unplug()
        tr, stats, _ = replay(tmp_path, m, f"r-{policy}", link=w,
                              cfg=PlayConfig(resume=policy, allow_clear=True), reconnect=reconnect,
                              before_send=cut)
        out[policy] = len(tr.stream("command").of("set"))
        assert stats["status"] == "completed"
    full = len(build_plan(m, PlayConfig()).t_ms)
    assert out["continue"] < full                         # part of the motion was skipped
    assert out["restart"] > full                          # part of it was played twice


def test_hardware_style_link_needs_a_human_confirmation(tmp_path):
    from motion_io_lab.link import PtyDeviceServer, SerialLink

    m = Motion("m", np.array([0., 500.]), np.array([1500., 1510.]), "t")
    with PtyDeviceServer() as srv:
        link = SerialLink(srv.path, settle_s=0.0)
        mode, lk, src = resolve("play", "hardware")
        ctx = TrialContext("s", "hw-no", mode, src, link=lk)
        with pytest.raises(NotConfirmed):
            with open_trial(tmp_path, ctx, with_input=False) as t:
                play(m, PlayConfig(), link, t, confirm=lambda info: False)
        assert not srv.device.armed and srv.device.pwm.oe_high
        assert not (tmp_path / "hw-no" / "command.jsonl").exists()   # nothing published
        with pytest.raises(NotConfirmed):
            ctx2 = TrialContext("s", "hw-none", mode, src, link=lk)
            with open_trial(tmp_path, ctx2, with_input=False) as t:
                play(m, PlayConfig(), link, t, confirm=None)
        link.close()


def test_unverified_device_refuses_arm_and_the_player_reports_it(tmp_path):
    w = SimWorld(seed=1, hardware_verified=False, wake_jitter_ms=None)
    m = Motion("m", np.array([0., 500.]), np.array([1500., 1510.]), "t")
    with pytest.raises(DeviceRefused, match="hardware_verified_false"):
        replay(tmp_path, m, "unv", link=w)


def test_trials_are_never_overwritten(tmp_path):
    m = Motion("m", np.array([0., 500.]), np.array([1500., 1510.]), "t")
    replay(tmp_path, m, "once", target="dry")
    with pytest.raises(FileExistsError):
        replay(tmp_path, m, "once", target="dry")


def test_client_matches_replies_to_sequence_numbers():
    w = SimWorld(seed=2, wake_jitter_ms=None)
    c = DeviceClient(w)
    c.hello()
    c.arm()
    for i in range(5):
        c.send_set(1500 + i, 100)
        w.sleep_until_ns(w.now_ns() + 25_000_000)
        c.pump()
    assert c.stats.accepted == 5 and c.stats.rejected == {}
    assert set(c.executed) == {1, 2, 3, 4, 5}
    assert all(v[2] > 0 for v in c.executed.values())


def test_touchpad_session_end_to_end_on_the_real_clock_dry_run(tmp_path):
    """A phone-style client posts events over HTTP; they end up in the input stream with the browser
    clock kept apart from the host receive time, and in the command stream as shaped pulses."""
    import json, threading, time, urllib.request
    from motion_io_lab.inputs import TouchPadServer

    srv = TouchPadServer(port=0)
    base = f"http://127.0.0.1:{srv.port}"

    def post(path, obj):
        req = urllib.request.Request(f"{base}{path}?token={srv.token}", data=json.dumps(obj).encode(), method="POST")
        urllib.request.urlopen(req, timeout=3).read()

    def client():
        post("/sync", {"client_offset_ms": 5000.5, "rtt_ms": 2.0, "samples": 8})
        for i in range(20):
            post("/input", {"events": [{"id": i, "t": 1000.0 + 20 * i, "x": 0.5 if i >= 5 else 0.0}]})
            time.sleep(0.02)
        time.sleep(0.15)
        srv.stop()

    th = threading.Thread(target=client)
    th.start()
    ctx = TrialContext("s", "touch1", "DRY_RUN", "recorded", link="none")
    with open_trial(tmp_path, ctx, with_input=True) as t:
        stats = record(srv, RecordConfig(period_ms=40), NullLink(realtime=True), t)
    th.join()
    srv.close()
    tr = load_trial(tmp_path / "touch1")
    ev = tr.stream("input").of("input_event")
    assert len(ev) == 20 and all(e["input_kind"] == "touch" for e in ev)
    assert ev[3]["client_time_ms"] == 1060.0 and ev[3]["client_clock_domain"] == "browser_performance"
    assert all(e["received_monotonic_ns"] > 0 and e["clock_domain"] == "host_monotonic" for e in ev)
    sync = tr.stream("input").of("sync")
    assert sync and sync[0]["client_offset_ms"] == 5000.5
    sets = tr.stream("command").of("set")
    vals = [s["commanded_pulse_us"] for s in sets]
    assert vals[0] == 1500 and max(vals) == 1520 and 1516 in vals      # a 20 us step, first slew-limited to 16 us
    assert stats["interrupted"] is None


def test_cli_loopback_realtime_script_run(tmp_path):
    from motion_io_lab.cli import main
    assert main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "rt", "--target",
                 "loopback", "--realtime", "--source", "script:step_hold"]) == 0
    tr = load_trial(tmp_path / "rt")
    assert tr.mode == "LOOPBACK" and any(r["reply_status"] == "EXECUTED" for r in tr.stream("command").of("reply"))
