"""The repo's central claim is that commanded, applied and observed values are never mixed up.
These tests pin that down at the places where a mix-up would be one wrong word in the code."""
import io
import time

import numpy as np
import pytest

import cv2

from motion_io_lab import analysis as an
from motion_io_lab import observe as obs
from motion_io_lab.devclient import CommandLog, DeviceClient
from motion_io_lab.inputs import InputEvent, InputSource, JoyConStdinSource, ScriptedSource
from motion_io_lab.link import Link, NullLink, SimWorld
from motion_io_lab.motions import MOTIONS, Motion, motion_from_trial
from motion_io_lab.player import FATAL_REJECTS, PlayConfig, Player, build_plan, play
from motion_io_lab.protocol import parse_line
from motion_io_lab.recorder import RecordConfig, record
from motion_io_lab.synth import ServoPlant, VideoSpec
from motion_io_lab.triallog import (StreamWriter, TrialContext, TrialLogError, load_trial,
                                    read_stream, validate_stream)
from motion_io_lab.trialio import open_trial, resolve

HELLO = ("HELLO proto=2 fw=0.1.0 board=T min_us=1450 max_us=1550 center_us=1500 step_us=5 tick_ms=10 "
         "grace_ms=300 max_valid_ms=500 epoch={e} hw_verified=1 fault=0")


class ScriptLink(Link):
    """A link that hands back exactly the lines it is given (no device behind it)."""
    link_type = "virtual"

    def __init__(self):
        self.t = 1_000_000_000
        self.inbound: list[str] = []
        self.sent: list[str] = []

    def now_ns(self):
        return self.t

    def sleep_until_ns(self, t_ns):
        self.t = max(self.t, t_ns)

    def write_line(self, text):
        self.sent.append(text)
        return self.t

    def read_lines(self):
        out, self.inbound = [(self.t, ln) for ln in self.inbound], []
        return out


def command_log(tmp_path, name="c"):
    ctx = TrialContext("s", name, "LOOPBACK", "synthetic", link="virtual")
    w = StreamWriter(tmp_path / name / "command.jsonl", "command", ctx, units="pulse_us",
                     coordinate_frame="servo_axis_0")
    return w, CommandLog(w)


# --- DeviceClient -------------------------------------------------------------------------------

def test_status_keeps_commanded_and_applied_in_their_own_columns(tmp_path):
    w, log = command_log(tmp_path)
    link = ScriptLink()
    c = DeviceClient(link, log)
    link.inbound = ["STATUS armed=1 fault=0 enabled=1 epoch=5 commanded_us=1540 applied_us=1510 seq=3 "
                    "lease_left_ms=80 last_reject=none last_stop=none mcu_ms=1234"]
    c.pump()
    w.close()
    row = read_stream(tmp_path / "c" / "command.jsonl").of("status")[0]
    assert row["device_commanded_pulse_us"] == 1540 and row["device_applied_pulse_us"] == 1510
    assert row["armed"] is True and row["fault"] is False and row["device_epoch"] == 5


def test_an_acceptance_is_not_logged_as_an_applied_value(tmp_path):
    w, log = command_log(tmp_path)
    link = ScriptLink()
    c = DeviceClient(link, log)
    link.inbound = ["ACCEPTED_NOT_EXECUTED SET seq=2 target_us=1520", "EXECUTED SET seq=2 applied_us=1515 mcu_ms=9"]
    c.pump()
    w.close()
    rows = read_stream(tmp_path / "c" / "command.jsonl").of("reply")
    assert rows[0]["reply_status"] == "ACCEPTED_NOT_EXECUTED" and rows[0]["device_applied_pulse_us"] is None
    assert rows[1]["device_applied_pulse_us"] == 1515
    assert list(c.executed) == [2] and c.executed[2][0] == 1515        # only EXECUTED counts as applied


def test_duplicates_are_counted_apart_from_acceptances():
    link = ScriptLink()
    c = DeviceClient(link)
    link.inbound = ["ACCEPTED_NOT_EXECUTED SET seq=1 target_us=1510",
                    "ACCEPTED_NOT_EXECUTED SET seq=1 dup=1", "REJECTED SET reason=epoch seq=2"]
    c.pump()
    assert (c.stats.accepted, c.stats.duplicates) == (1, 1)
    assert c.stats.rejected == {"SET:epoch": 1}


def test_hello_restarts_the_sequence_and_disarm_forgets_the_epoch():
    link = ScriptLink()
    c = DeviceClient(link)
    link.inbound = [HELLO.format(e=77)]
    assert c.hello().epoch == 77
    assert [c.send_set(1500, 100) for _ in range(3)] == [1, 2, 3]
    link.inbound = [HELLO.format(e=78)]
    c.hello()
    assert c.send_set(1500, 100) == 1                       # a new epoch starts again at 1
    link.inbound = ["STOP disarm"]
    c.disarm()
    assert c.epoch is None
    with pytest.raises(RuntimeError, match="HELLO first"):
        c.send_set(1500, 100)


# --- recorder ------------------------------------------------------------------------------------

def _step_source(t_step=0.2, duration=1.0):
    return ScriptedSource(lambda t: 1.0 if t > t_step else 0.0, duration, tremor_sigma=0.0)


def test_a_dry_recording_keeps_the_wish_and_the_shaped_command_apart(tmp_path):
    ctx = TrialContext("s", "dry", "DRY_RUN", "synthetic", link="none")
    with open_trial(tmp_path, ctx, with_input=True) as t:
        record(_step_source(), RecordConfig(), NullLink(), t)
    sets = load_trial(tmp_path / "dry").stream("command").of("set")
    assert any(r["desired_pulse_us"] != r["commanded_pulse_us"] for r in sets)      # the slew limit bit
    assert max(r["desired_pulse_us"] for r in sets) == 1540 and max(r["commanded_pulse_us"] for r in sets) <= 1540
    assert [r["planned_index"] for r in sets] == list(range(len(sets)))
    inputs = load_trial(tmp_path / "dry").stream("input").of("input_event")
    assert all(r["valid"] and r["input_value"] is not None for r in inputs)


class _GapSource(InputSource):
    """Yields one invalid sample (no value) and then a good one."""
    kind = "scripted"

    def __init__(self):
        self.n = 0

    def start(self, now_ns):
        self.t0 = now_ns

    def poll(self, now_ns):
        out = []
        if self.n == 0 and now_ns >= self.t0 + 10_000_000:
            out = [InputEvent(now_ns, None, "scripted", None, 0), InputEvent(now_ns + 1, 0.5, "scripted", None, 1)]
            self.n = 1
        return out

    def done(self, now_ns):
        return now_ns > self.t0 + 200_000_000


def test_an_input_sample_without_a_value_is_logged_invalid_with_null(tmp_path):
    ctx = TrialContext("s", "gap", "DRY_RUN", "recorded", link="none")
    with open_trial(tmp_path, ctx, with_input=True) as t:
        record(_GapSource(), RecordConfig(), NullLink(), t)
    ev = load_trial(tmp_path / "gap").stream("input").of("input_event")
    assert [(e["valid"], e["input_value"]) for e in ev] == [(False, None), (True, 0.5)]


def test_recording_disarms_the_device_when_it_ends(tmp_path):
    w = SimWorld(seed=1, plant=ServoPlant(seed=1), wake_jitter_ms=None)
    ctx = TrialContext("s", "end", "LOOPBACK", "synthetic", link="virtual")
    with open_trial(tmp_path, ctx, with_input=True) as t:
        stats = record(_step_source(duration=0.6), RecordConfig(), w, t)
    assert stats["stopped_by"] == "source_done"
    assert not w.device.armed                              # nobody had to wait for the lease to run out
    ev = load_trial(tmp_path / "end").stream("command").of("event")
    assert any(e["event"] == "device_stop" and e["detail"] == "disarm" for e in ev)


def test_a_stop_from_the_device_ends_a_recording_and_the_log_says_so(tmp_path):
    w = SimWorld(seed=1, plant=ServoPlant(seed=1), wake_jitter_ms=None)

    class Cut(ScriptedSource):
        n = 0

        def poll(self, now_ns):
            Cut.n += 1
            if Cut.n == 15:
                w.write_line("ESTOP")                     # the device stops itself and says so
            return super().poll(now_ns)
    ctx = TrialContext("s", "stop", "LOOPBACK", "synthetic", link="virtual")
    fn, dur = MOTIONS["sweep"]
    with open_trial(tmp_path, ctx, with_input=True) as t:
        stats = record(Cut(fn, dur, seed=1), RecordConfig(), w, t)
    assert stats["interrupted"] == "device_stop:estop"
    ev = load_trial(tmp_path / "stop").stream("command").of("event")
    assert [e["detail"] for e in ev if e["event"] == "record_end"] == ["device_stop:estop"]


# --- player --------------------------------------------------------------------------------------

def _play(tmp_path, tid, motion, w, cfg=None, **kw):
    ctx = TrialContext("s", tid, "REPLAY", "synthetic", link="virtual")
    with open_trial(tmp_path, ctx, with_input=False) as t:
        stats = play(motion, cfg or PlayConfig(), w, t, **kw)
    return load_trial(tmp_path / tid), stats


def test_a_stop_from_the_device_ends_a_replay(tmp_path):
    w = SimWorld(seed=1, wake_jitter_ms=None)
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1530.]), "t")
    tr, stats = _play(tmp_path, "est", m, w,
                      before_send=lambda i, when: w.write_line("ESTOP") if i == 8 else None)
    assert stats["status"] == "interrupted" and stats["interruptions"][0]["reason"] == "device_stop:estop"
    assert stats["sent"] < stats["samples_planned"]


def test_every_rejection_that_means_you_are_no_longer_in_a_session_ends_the_replay():
    assert set(FATAL_REJECTS) == {"epoch", "disarmed", "lease_expired", "fault"}

    class StubClient:
        def __init__(self, reason):
            self.rejections = [(1, parse_line(f"REJECTED SET reason={reason} seq=4"))]
    for reason in FATAL_REJECTS:
        p = Player.__new__(Player)
        p.client, p._rej_seen = StubClient(reason), 0
        assert p._new_fatal_rejection() == reason
    for reason in ("range", "parse", "seq", "valid_for"):         # a bad value is not the end of the session
        p = Player.__new__(Player)
        p.client, p._rej_seen = StubClient(reason), 0
        assert p._new_fatal_rejection() is None


def test_a_replay_starts_from_where_the_device_actually_is(tmp_path):
    w = SimWorld(seed=1, wake_jitter_ms=None)
    first = Motion("a", np.array([0., 600.]), np.array([1530., 1530.]), "t")
    _play(tmp_path, "one", first, w)
    assert w.device.applied == 1530
    second = Motion("b", np.array([0., 600.]), np.array([1500., 1500.]), "t")
    tr, _ = _play(tmp_path, "two", second, w)
    sets = [r for r in tr.stream("command").of("set") if r["valid"]]
    assert sets[0]["desired_pulse_us"] == 1500 and sets[0]["commanded_pulse_us"] == 1514   # 400 us/s x 40 ms = 16
    assert sets[0]["commanded_pulse_us"] != sets[0]["desired_pulse_us"]


def test_late_drops_are_not_counted_as_sent(tmp_path):
    m = Motion("m", np.array([0., 1000.]), np.array([1500., 1520.]), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)
    tr, stats = _play(tmp_path, "late2", m, w, PlayConfig(drop_if_late_ms=60, valid_for_ms=200),
                      before_send=lambda i, when: w.advance_to(w.now_ns() + 100_000_000) if i == 5 else None)
    assert stats["dropped_late"] >= 1
    assert stats["sent"] + stats["dropped_late"] == stats["samples_planned"]


def test_the_lease_length_limits_are_20_to_500_ms():
    for ok in (20, 120, 500):
        PlayConfig(valid_for_ms=ok)
    for bad in (19, 501):
        with pytest.raises(ValueError, match="valid_for_ms"):
            PlayConfig(valid_for_ms=bad)


def test_a_replay_gives_up_after_two_resumes(tmp_path):
    m = Motion("m", np.arange(0, 3001, 20.0), 1500 + 30 * np.sin(np.arange(0, 3001, 20.0) / 300), "t")
    w = SimWorld(seed=1, wake_jitter_ms=None)
    cuts = {"n": 0}

    def cut(i, when):
        if w.connected and i % 30 == 29 and cuts["n"] < 3:
            cuts["n"] += 1
            w.unplug()

    def reconnect():
        w.sleep_until_ns(w.now_ns() + 600_000_000)
        w.replug()
        return w
    tr, stats = _play(tmp_path, "three", m, w, PlayConfig(resume="restart", allow_clear=True),
                      reconnect=reconnect, before_send=cut)
    assert cuts["n"] == 3 and stats["resumed"] == 2 and stats["status"] == "interrupted"


def test_the_cli_passes_resume_and_allow_clear_to_the_player(tmp_path, monkeypatch):
    from motion_io_lab import player
    from motion_io_lab.cli import main
    seen = {}
    real = player.play

    def spy(motion, cfg, link, trial, **kw):
        seen["cfg"] = cfg
        return real(motion, cfg, link, trial, **kw)
    monkeypatch.setattr(player, "play", spy)
    assert main(["record", "--out", str(tmp_path), "--session-id", "s", "--trial-id", "r", "--source",
                 "script:sweep"]) == 0
    assert main(["play", "--motion", str(tmp_path / "r"), "--out", str(tmp_path), "--session-id", "s",
                 "--trial-id", "p", "--resume", "continue", "--allow-clear", "--allow-long"]) == 0
    cfg = seen["cfg"]
    assert cfg.resume == "continue" and cfg.allow_clear is True and cfg.max_duration_ms is None
    assert main(["play", "--motion", str(tmp_path / "r"), "--out", str(tmp_path), "--session-id", "s",
                 "--trial-id", "p2"]) == 0
    assert seen["cfg"].resume is None and seen["cfg"].allow_clear is False and seen["cfg"].max_duration_ms == 120_000.0


# --- motions, analysis, logs, inputs, observation -------------------------------------------------

def test_a_recorded_trial_replays_on_its_scheduled_times_not_its_send_jitter(tmp_path):
    w = SimWorld(seed=1, plant=ServoPlant(seed=1))                    # default wake-up jitter on
    ctx = TrialContext("s", "jit", "LOOPBACK", "synthetic", link="virtual")
    fn, dur = MOTIONS["sweep"]
    with open_trial(tmp_path, ctx, with_input=True) as t:
        record(ScriptedSource(fn, dur, seed=1), RecordConfig(), w, t)
    tr = load_trial(tmp_path / "jit")
    sets = tr.stream("command").of("set")
    sent = np.diff([r["sent_monotonic_ns"] for r in sets])
    assert sent.std() > 0                                             # the sends really wobbled
    m = motion_from_trial(tr)
    assert np.allclose(np.diff(m.t_ms), 40.0, atol=1e-9)              # ... and the replay does not inherit it


def _trial_with_observation(tmp_path, rows):
    """A command stream with two sends and an observation stream with the given marker rows."""
    d = tmp_path / "t"
    ctx = TrialContext("s", "t", "LOOPBACK", "synthetic", link="virtual")
    with StreamWriter(d / "command.jsonl", "command", ctx, units="pulse_us", coordinate_frame="f") as w:
        for seq, ns in ((1, 1_000_000_000), (2, 1_040_000_000)):
            w.write("set", seq=seq, desired_pulse_us=1510.0, commanded_pulse_us=1510, valid_for_ms=100,
                    scheduled_monotonic_ns=ns, sent_monotonic_ns=ns, processed_monotonic_ns=ns, shaping="none")
    with StreamWriter(d / "observation.jsonl", "observation", ctx, units="norm_angle",
                      coordinate_frame="camera_image") as w:
        for r in rows:
            w.write("marker", **r)
    return load_trial(d)


def _marker(frame, valid, value, rx, before):
    return dict(frame_id=frame, media_pos_ms=None, source_kind="live", observed_value=value if valid else None,
                cx_px=1.0 if valid else None, cy_px=1.0 if valid else None, area_px=50.0 if valid else None,
                n_blobs=1 if valid else 0, invalid_reason=None if valid else "none_found", valid=valid,
                received_monotonic_ns=rx, host_before_read_ns=before, processed_monotonic_ns=rx)


def test_live_observation_times_come_from_the_receive_time_and_invalid_frames_are_left_out(tmp_path):
    tr = _trial_with_observation(tmp_path, [
        _marker(0, True, 0.10, 1_200_000_000, 1_150_000_000),
        _marker(1, False, None, 1_233_000_000, 1_230_000_000),
        _marker(2, True, 0.30, 1_266_000_000, 1_260_000_000)])
    ts = an.extract(tr)
    assert list(ts.observed[0]) == [200.0, 266.0]                     # (received - first send) in ms
    assert list(ts.observed[1]) == [0.10, 0.30]
    assert ts.obs_total == 3 and ts.obs_invalid == {"none_found": 1}


def test_a_set_row_on_a_link_needs_its_send_time_and_a_reject_needs_its_reason(tmp_path):
    ctx = TrialContext("s", "x", "LOOPBACK", "synthetic", link="virtual")
    p = tmp_path / "command.jsonl"
    with StreamWriter(p, "command", ctx, units="pulse_us", coordinate_frame="f") as w:
        w.write("set", seq=1, desired_pulse_us=1500.0, commanded_pulse_us=1500, valid_for_ms=100,
                scheduled_monotonic_ns=5, sent_monotonic_ns=None, processed_monotonic_ns=5, shaping="none")
    assert any("needs sent_monotonic_ns" in e for e in validate_stream(p)[2])
    q = tmp_path / "q.jsonl"
    with pytest.raises(TrialLogError, match="REJECTED needs reply_reason"):
        with StreamWriter(q, "command", ctx, units="pulse_us", coordinate_frame="f") as w:
            w.write("reply", reply_status="REJECTED", reply_reason=None, reply_to_seq=1, device_millis=1,
                    device_applied_pulse_us=None, raw="REJECTED SET", received_monotonic_ns=1,
                    processed_monotonic_ns=1)


@pytest.mark.parametrize("field", ["cx_px", "cy_px", "observed_value"])
def test_an_invalid_observation_may_carry_no_coordinate(tmp_path, field):
    ctx = TrialContext("s", "x", "DRY_RUN", "recorded", link="none")
    row = _marker(0, False, None, 5, 4)
    row[field] = 0.0
    with pytest.raises(TrialLogError, match="use null, never 0"):
        with StreamWriter(tmp_path / "o.jsonl", "observation", ctx, units="norm_angle",
                          coordinate_frame="camera_image") as w:
            w.write("marker", **row)


def test_a_joycon_boolean_is_not_a_number():
    lines = ['{"event":"input","element":"A","value":true}\n', '{"event":"input","element":"A","value":0.5}\n']
    src = JoyConStdinSource("A", stream=io.StringIO("".join(lines)))
    time.sleep(0.1)
    ev = src.poll(0)
    assert [e.value for e in ev] == [0.5] and src.skipped == 1


def test_a_green_area_bigger_than_max_area_is_not_a_marker():
    cfg = obs.parse_config({**VideoSpec().camera_config("x.mp4"), "max_area_px": 500})
    frame = np.full((120, 160, 3), 90, np.uint8)
    cv2.circle(frame, (80, 60), 30, (0, 200, 0), -1)                  # area ~ 2800 px
    assert obs.detect_marker(frame, cfg).reason == "none_found"
    cv2.circle(frame, (80, 60), 30, (90, 90, 90), -1)
    cv2.circle(frame, (80, 60), 10, (0, 200, 0), -1)                  # area ~ 310 px
    assert obs.detect_marker(frame, cfg).reason is None


def test_the_console_blocks_arm_in_any_letter_case_unless_allowed():
    from motion_io_lab.serial_console import run_console
    link = ScriptLink()

    class NoLog:
        def event(self, *a, **k):
            pass
    said = []
    run_console(link, ["ARM 4096", "arm 4096", " Arm 4096", "STATUS"], NoLog(), allow_arm=False,
                out=said.append, idle_ms=1)
    assert link.sent == ["STATUS"]
    assert sum("blocked" in s for s in said) == 3
    link2 = ScriptLink()
    run_console(link2, ["ARM 4096"], NoLog(), allow_arm=True, out=said.append, idle_ms=1)
    assert link2.sent == ["ARM 4096"]
