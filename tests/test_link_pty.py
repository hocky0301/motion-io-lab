"""The same protocol over a pseudo-terminal, with pyserial, on the real clock."""
import json
import time

import pytest

from motion_io_lab.bounded_probe import main as probe_main
from motion_io_lab.devclient import CommandLog, DeviceClient, DeviceRefused
from motion_io_lab.link import LinkDown, NullLink, PtyDeviceServer, RealtimeLoopback, SerialLink, SimWorld
from motion_io_lab.protocol import check_line, parse_line
from motion_io_lab.serial_console import run_console
from motion_io_lab.session import start_device
from motion_io_lab.triallog import StreamWriter, TrialContext, load_trial


def test_serial_link_talks_to_the_virtual_device_through_a_pty():
    with PtyDeviceServer(seed=4) as srv:
        link = SerialLink(srv.path, settle_s=0.0)
        c = DeviceClient(link)
        info = c.hello()
        assert info.board == "VIRTUAL_PTY" and info.proto == 2 and info.hw_verified
        c.arm()
        for i in range(5):
            c.send_set(1500 + 4 * i, 500)
            link.sleep_until_ns(link.now_ns() + 25_000_000)
            c.pump()
        st = c.status()
        assert st.fields["armed"] == 1 and st.fields["commanded_us"] == 1516
        assert c.stats.accepted == 5 and c.stats.executed >= 4
        c.disarm()
        link.close()


def test_unplugged_port_is_a_clean_error_not_a_traceback():
    with pytest.raises(LinkDown, match="cannot open"):
        SerialLink("/dev/does-not-exist", settle_s=0.0)


def test_realtime_loopback_uses_the_real_clock():
    link = RealtimeLoopback(seed=1)
    c = DeviceClient(link)
    t0 = time.monotonic_ns()
    c.hello()
    c.arm()
    c.send_set(1510, 500)
    link.sleep_until_ns(link.now_ns() + 30_000_000)
    c.pump()
    assert c.stats.accepted == 1 and 1 in c.executed
    assert time.monotonic_ns() - t0 >= 30_000_000


def test_protocol_line_checks():
    for bad in ("x" * 96, "café", "tab\there", "new\nline"):
        with pytest.raises(ValueError):
            check_line(bad)
    assert check_line("SET 1 2 3 4") == "SET 1 2 3 4"


def test_parse_line_shapes():
    r = parse_line("REJECTED SET reason=epoch seq=7")
    assert (r.kind, r.cmd, r.reason, r.seq) == ("REJECTED", "SET", "epoch", 7)
    r = parse_line("EXECUTED SET seq=3 applied_us=1505 mcu_ms=123")
    assert r.kind == "EXECUTED" and r.fields["applied_us"] == 1505
    assert parse_line("STOP lease_expired").reason == "lease_expired"
    assert parse_line("ACCEPTED_NOT_EXECUTED SET seq=1 dup=1").fields["dup"] == 1
    assert parse_line("garbage").kind == "OTHER"
    assert parse_line("").kind == "OTHER"


def test_start_device_refuses_a_latched_device_without_clearing_it():
    w = SimWorld(seed=1, wake_jitter_ms=None)
    w.device.fault = True
    c = DeviceClient(w)
    with pytest.raises(DeviceRefused, match="latched fault"):
        start_device(c, "virtual")
    assert w.device.fault                    # nothing cleared it on the way


def test_console_blocks_arm_without_the_flag_and_logs_both_directions(tmp_path):
    w = SimWorld(seed=1, wake_jitter_ms=None)
    ctx = TrialContext("s", "con", "HARDWARE_ARMED", "hardware", link="serial")
    ctx = TrialContext("s", "con", "REPLAY", "synthetic", link="virtual")
    wr = StreamWriter(tmp_path / "con" / "command.jsonl", "command", ctx, units="pulse_us",
                      coordinate_frame="servo_axis_0")
    out = []
    with wr:
        n = run_console(w, ["HELLO", "ARM 1", "STATUS", "bad\tline", "QUIT", "STATUS"], CommandLog(wr),
                        allow_arm=False, out=out.append)
    assert n == 2                                         # HELLO and STATUS; ARM and the tab line never sent
    assert any("ARM blocked" in o for o in out) and any("invalid line" in o for o in out)
    tr = load_trial(tmp_path / "con")
    ev = tr.stream("command").of("event")
    assert [e["event"] for e in ev] == ["tx", "rx", "tx", "rx"]
    assert not w.device.armed


def test_bounded_probe_dry_run_opens_nothing(capsys):
    assert probe_main(["--pulse-us", "1500", "--seconds", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "DRY_RUN_NO_SERIAL_OPEN"
    with pytest.raises(SystemExit):
        probe_main(["--pulse-us", "1600"])
    with pytest.raises(SystemExit):
        probe_main(["--seconds", "3.1"])
    with pytest.raises(SystemExit):
        probe_main(["--execute", "--trial-id", "x"])        # no port, no flag
    with pytest.raises(SystemExit):
        probe_main(["--execute", "--port", "/dev/null", "--trial-id", "x"])  # flag missing


def test_bounded_probe_on_the_virtual_device_logs_sets_and_the_final_disarm(tmp_path, capsys):
    assert probe_main(["--execute", "--loopback", "--out", str(tmp_path), "--trial-id", "p1",
                       "--pulse-us", "1520", "--seconds", "0.5"]) == 0
    tr = load_trial(tmp_path / "p1")
    sets = tr.stream("command").of("set")
    assert len(sets) >= 10 and all(s["commanded_pulse_us"] == 1520 for s in sets)
    ev = [e["event"] for e in tr.stream("command").of("event")]
    assert "device_stop" in ev                            # the DISARM is acknowledged and logged
    assert (tmp_path / "p1" / "run_manifest.json").exists()
    assert probe_main(["--execute", "--loopback", "--out", str(tmp_path), "--trial-id", "p1"]) == 2
