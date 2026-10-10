import json
import os
import pty
import time
import urllib.error
import urllib.request

import pytest

from motion_io_lab.inputs import (KeyboardSource, ScriptedSource, TouchPadServer, bind_address)
from motion_io_lab.motions import MOTIONS


def post(url, obj, raw=None):
    data = raw if raw is not None else json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


@pytest.fixture
def pad():
    s = TouchPadServer(port=0)
    yield s
    s.close()


def base(pad, path):
    return f"http://127.0.0.1:{pad.port}{path}?token={pad.token}"


def test_binds_to_loopback_unless_lan_is_asked_for():
    assert bind_address(False) == "127.0.0.1"
    assert bind_address(True) == "0.0.0.0"
    s = TouchPadServer(port=0)
    try:
        assert s.httpd.server_address[0] == "127.0.0.1"
        assert "127.0.0.1" in s.url()
    finally:
        s.close()


def test_token_is_required_on_every_endpoint_even_on_loopback(pad):
    root = f"http://127.0.0.1:{pad.port}"
    assert get(root + "/")[0] == 403
    assert get(root + "/?token=wrong")[0] == 403
    assert get(root + "/ping")[0] == 403
    assert post(root + "/input", {"events": []}) == 403
    assert post(root + "/sync?token=nope", {}) == 403
    assert get(base(pad, "/"))[0] == 200
    assert b"performance.now" in get(base(pad, "/"))[1]


def test_tokens_are_random_and_long():
    a, b = TouchPadServer(port=0), TouchPadServer(port=0)
    try:
        assert a.token != b.token and len(a.token) >= 16
    finally:
        a.close(); b.close()


def test_events_keep_browser_time_and_host_receive_time_separate(pad):
    t_before = time.monotonic_ns()
    st = post(base(pad, "/input"), {"events": [{"id": 1, "t": 1234.5, "x": -0.25},
                                               {"id": 2, "t": 1250.0, "x": 0.5}]})
    t_after = time.monotonic_ns()
    assert st == 200
    ev = pad.poll(0)
    assert [e.value for e in ev] == [-0.25, 0.5]
    assert [e.client_time_ms for e in ev] == [1234.5, 1250.0]
    assert all(t_before <= e.rx_ns <= t_after for e in ev)
    assert ev[0].kind == "touch" and pad.client_clock_domain == "browser_performance"
    assert pad.poll(0) == []


def test_bad_events_are_rejected_not_clipped(pad):
    url = base(pad, "/input")
    assert post(url, {"events": [{"id": 1, "t": 1, "x": 1.5}]}) == 400          # out of range
    assert post(url, {"events": [{"id": 1, "t": 1, "x": "a"}]}) == 400
    assert post(url, {"events": [{"id": 1, "t": 1}]}) == 400
    assert post(url, None, raw=b'{"events":[{"id":1,"t":1,"x":NaN}]}') == 400
    assert post(url, None, raw=b"not json") == 400
    assert post(url, {"events": [{"id": i, "t": 1, "x": 0} for i in range(501)]}) == 400
    assert post(url, None, raw=b"x" * 70000) == 413
    assert post(url, {"events": "x"}) == 400
    assert pad.poll(0) == []


def test_sync_endpoint_stores_the_clock_offset_estimate(pad):
    st, body = get(base(pad, "/ping"))
    assert st == 200 and int(json.loads(body)["host_ns"]) > 0
    assert post(base(pad, "/sync"), {"client_offset_ms": 12345.6, "rtt_ms": 3.2, "samples": 8}) == 200
    pad.poll(0)                                      # events and sync records are drained together
    rec = pad.sync_records()
    assert rec[0]["client_offset_ms"] == 12345.6 and rec[0]["rtt_ms"] == 3.2
    assert rec[0]["received_monotonic_ns"] > 0
    assert post(base(pad, "/sync"), {"client_offset_ms": "x", "rtt_ms": 1, "samples": 1}) == 400


def test_unknown_paths_are_404(pad):
    assert get(base(pad, "/etc/passwd"))[0] == 404
    assert post(base(pad, "/nope"), {}) == 404


def test_scripted_source_emits_events_on_schedule_with_tremor_and_clipping():
    fn, dur = MOTIONS["sweep"]
    s = ScriptedSource(fn, dur, rate_hz=100, tremor_sigma=0.0)
    s.start(1_000_000_000)
    assert s.poll(1_000_000_000 - 1) == []
    ev = s.poll(1_000_000_000 + 500_000_000)
    assert len(ev) == 51 and ev[0].rx_ns == 1_000_000_000
    assert ev[-1].rx_ns == 1_000_000_000 + 500_000_000
    assert abs(ev[-1].value - fn(0.5)) < 1e-12
    assert not s.done(1_000_000_000 + 500_000_000)
    s.poll(1_000_000_000 + 10 * 10**9)
    assert s.done(0)
    noisy = ScriptedSource(lambda t: 0.99, 1.0, tremor_sigma=0.5, seed=1)
    noisy.start(0)
    assert all(-1.0 <= e.value <= 1.0 for e in noisy.poll(10**10))


class FakeTTY:
    def __init__(self, fd):
        self._fd = fd

    def isatty(self):
        return True

    def fileno(self):
        return self._fd


def test_keyboard_source_maps_keys_and_restores_the_terminal():
    master, slave = pty.openpty()
    try:
        src = KeyboardSource(step=0.1, stream=FakeTTY(slave))
        import termios
        before = termios.tcgetattr(slave)
        with src:
            assert termios.tcgetattr(slave)[3] & termios.ICANON == 0      # raw (cbreak) while recording
            os.write(master, b"ddd\x1b[Da \x1b[Cq")
            time.sleep(0.05)
            ev = src.poll(0)
            vals = [round(e.value, 3) for e in ev]
            assert vals == [0.1, 0.2, 0.3, 0.2, 0.1, 0.0, 0.1]
            assert src.quit and src.done(0)
        flags = termios.ECHO | termios.ICANON
        assert termios.tcgetattr(slave)[3] & flags == before[3] & flags   # line mode and echo are back
        os.write(master, b"dddddddddddddddd")
        time.sleep(0.05)
    finally:
        os.close(master)
        os.close(slave)


def test_keyboard_needs_a_terminal():
    class NotATTY:
        def isatty(self):
            return False
    with pytest.raises(RuntimeError, match="terminal"):
        KeyboardSource(stream=NotATTY())


def test_joycon_source_reads_probe_lines_from_a_pipe_and_ignores_the_rest():
    from motion_io_lab.inputs import JoyConStdinSource

    r, w = os.pipe()
    lines = [
        {"event": "waiting", "host_monotonic_ns": 1},
        {"event": "input", "element": "Left Thumbstick", "kind": "dpad", "x": 0.5, "y": -1.0,
         "host_monotonic_ns": 2_000_000},
        {"event": "input", "element": "Button A", "kind": "button", "value": 1.0, "host_monotonic_ns": 3},
        {"event": "input", "element": "Left Thumbstick", "kind": "dpad", "x": 7.0, "y": 0,
         "host_monotonic_ns": 4_000_000},
        {"event": "motion", "rotation_rate_xyz": [0, 0, 1]},
    ]
    with os.fdopen(r) as rf:
        src = JoyConStdinSource("Left Thumbstick", stream=rf)
        with os.fdopen(w, "w") as wf:
            for ln in lines:
                wf.write(json.dumps(ln) + "\n")
            wf.write("not json\n")
        deadline = time.time() + 2
        ev = []
        while time.time() < deadline and not src.done(0):
            ev += src.poll(0)
            time.sleep(0.01)
        ev += src.poll(0)
    assert [e.value for e in ev] == [0.5, 1.0]                  # 7.0 is clipped, not dropped
    assert ev[0].client_time_ms == 2.0 and src.client_clock_domain == "host_monotonic"
    assert src.skipped == 1 and src.done(0)
