"""Edge cases: unplugging, Ctrl+C, odd keys, odd input."""
import json
import os
import pty
import signal
import subprocess
import sys
import time

import numpy as np
import pytest

from motion_io_lab.devclient import DeviceClient
from motion_io_lab.inputs import KeyboardSource, ScriptedSource, TouchPadServer
from motion_io_lab.link import LinkDown, SimWorld
from motion_io_lab.motions import MOTIONS, Motion
from motion_io_lab.player import PlayConfig, play
from motion_io_lab.recorder import RecordConfig, record
from motion_io_lab.triallog import TrialContext, load_trial
from motion_io_lab.trialio import open_trial, resolve

from tests.test_inputs import FakeTTY, base, post


def _replay(tmp_path, motion, tid, link, cfg, **kw):
    mode, lk, src = resolve("play", "loopback")
    ctx = TrialContext("s", tid, mode, src, link=lk)
    with open_trial(tmp_path, ctx, with_input=False) as t:
        stats = play(motion, cfg, link, t, **kw)
    return load_trial(tmp_path / tid), stats


M = Motion("m", np.array([0., 1000.]), np.array([1500., 1530.]), "t")


def test_pulled_cable_that_makes_reads_fail_ends_the_run_as_interrupted(tmp_path):
    """A real port raises on read as well as on write; the run must end as 'interrupted'."""
    w = SimWorld(seed=1, wake_jitter_ms=None)
    tr, stats = _replay(tmp_path, M, "cut", w, PlayConfig(),
                        before_send=lambda i, when: w.unplug(read_raises=True) if i == 10 else None)
    assert stats["status"] == "interrupted"
    assert stats["interruptions"][0]["reason"] == "link_down"
    assert "interrupted" in [r["event"] for r in tr.stream("command").of("event")]


def test_pulled_cable_with_failing_reads_can_be_resumed_by_asking(tmp_path):
    w = SimWorld(seed=1, wake_jitter_ms=None)
    fired = []

    def cut(i, when):
        if i == 10 and not fired:
            fired.append(1)
            w.unplug(read_raises=True)

    def reconnect():
        w.sleep_until_ns(w.now_ns() + 500_000_000)
        w.replug()
        return w
    tr, stats = _replay(tmp_path, M, "cut-resume", w, PlayConfig(resume="continue", allow_clear=True),
                        reconnect=reconnect, before_send=cut)
    assert stats["status"] == "completed" and stats["resumed"] == 1
    assert len({r["session_epoch"] for r in tr.stream("command").of("set")}) == 2


def test_recording_survives_a_read_error_after_the_cable_is_pulled(tmp_path):
    w = SimWorld(seed=1, wake_jitter_ms=None)
    fn, dur = MOTIONS["sweep"]

    class Cutting(ScriptedSource):
        n = 0

        def poll(self, now_ns):
            Cutting.n += 1
            if Cutting.n == 30:
                w.unplug(read_raises=True)
            return super().poll(now_ns)

    mode, lk, src = resolve("record", "loopback")
    ctx = TrialContext("s", "rec-cut", mode, src, link=lk)
    with open_trial(tmp_path, ctx, with_input=True) as t:
        stats = record(Cutting(fn, dur, seed=1), RecordConfig(), w, t)
    assert stats["interrupted"] == "link_down" and stats["stopped_by"] == "interrupted"
    load_trial(tmp_path / "rec-cut")                   # a valid, published log


def test_ctrl_c_ends_a_touch_recording_and_keeps_the_log(tmp_path):
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, "-m", "motion_io_lab", "record", "--source", "touch",
                          "--target", "dry", "--http-port", "0", "--max-seconds", "30",
                          "--out", str(tmp_path), "--session-id", "s", "--trial-id", "ctrlc"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                         # a parent started in the background has SIGINT ignored, and Python then
                         # does not turn it into KeyboardInterrupt; undo that for the child
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        assert "Ctrl+C" in "".join(p.stdout.readline() for _ in range(2))
        # the message is printed before the log is opened: wait until recording really runs, so
        # that the signal does not depend on how fast this machine starts the process
        started = tmp_path / "ctrlc" / "command.jsonl.part"
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert started.exists(), "recording did not start"
        time.sleep(1.0)
        p.send_signal(signal.SIGINT)
        out, err = p.communicate(timeout=20)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, err
    assert json.loads(out)["stopped_by"] == "ctrl_c"
    d = tmp_path / "ctrlc"
    assert (d / "command.jsonl").exists() and not list(d.glob("*.part"))
    load_trial(d)


@pytest.mark.parametrize("keys, expected", [
    (b"\x1b[A", []),                       # up arrow: nothing
    (b"\x1b[B", []),
    (b"\x1b[1;2D", []),                    # Shift+left: not a plain left arrow
    (b"\x1b[1;2C", []),
    (b"\x1b[3~", []),                      # Delete
    (b"\x1b[H", []),                       # Home
    (b"\x1b[Aa", [-0.1]),                  # up arrow, then a real 'a'
    (b"\x1bOD", [-0.1]),                   # application-mode left arrow
    (b"\x1b[C\x1b[D", [0.1, 0.0]),
])
def test_only_plain_arrows_and_letters_move_the_axis(keys, expected):
    master, slave = pty.openpty()
    try:
        with KeyboardSource(step=0.1, stream=FakeTTY(slave)) as src:
            os.write(master, keys)
            time.sleep(0.05)
            assert [round(e.value, 3) for e in src.poll(0)] == expected
    finally:
        os.close(master)
        os.close(slave)


def test_an_escape_sequence_split_across_reads_is_not_misread():
    master, slave = pty.openpty()
    try:
        with KeyboardSource(step=0.1, stream=FakeTTY(slave)) as src:
            os.write(master, b"\x1b[1;")
            time.sleep(0.05)
            assert src.poll(0) == []
            os.write(master, b"2Dd")
            time.sleep(0.05)
            assert [round(e.value, 3) for e in src.poll(0)] == [0.1]
    finally:
        os.close(master)
        os.close(slave)


def test_touchpad_survives_overflowing_numbers_and_oversized_bodies():
    pad = TouchPadServer(port=0)
    try:
        url = base(pad, "/input")
        assert post(url, None, raw=b'{"events":[{"id":1e999,"t":1,"x":0}]}') == 400
        assert post(base(pad, "/sync"), None,
                    raw=b'{"client_offset_ms":1,"rtt_ms":1,"samples":1e999}') == 400
        # ids must rise and the browser clock must not run back inside one batch
        assert post(url, {"events": [{"id": 2, "t": 5, "x": 0}, {"id": 2, "t": 6, "x": 0}]}) == 400
        assert post(url, {"events": [{"id": 1, "t": 5, "x": 0}, {"id": 2, "t": 4, "x": 0}]}) == 400
        assert post(url, {"events": [{"id": 1, "t": 5, "x": 0}, {"id": 2, "t": 5, "x": 0.5}]}) == 200
        # a 1 MB body gets a clean 413, not a reset connection (30 tries: the reset was flaky)
        for _ in range(30):
            assert post(url, None, raw=b"x" * 1_000_000) == 413
        assert [e.event_id for e in pad.poll(0)] == [1, 2]
    finally:
        pad.close()


def test_keyboard_recording_without_a_terminal_is_a_clean_error(tmp_path):
    r = subprocess.run([sys.executable, "-m", "motion_io_lab", "record", "--source", "keyboard",
                        "--target", "dry", "--out", str(tmp_path), "--session-id", "s",
                        "--trial-id", "nokb"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "interactive terminal" in r.stderr and "Traceback" not in r.stderr
    assert not (tmp_path / "nokb").exists()
