"""Properties of protocol v2 on the virtual device (the firmware core is held to the same
behaviour by test_conformance.py)."""
import random

import pytest

from motion_io_lab.link import SimWorld
from motion_io_lab.devclient import DeviceClient, DeviceRefused
from motion_io_lab.vdevice import (MAX_STEP_US, PULSE_CENTER, TICK_MS, VirtualDevice, pulse_to_ticks)


def new(verified=True, rnd=None):
    d = VirtualDevice(hardware_verified=verified, board="T", random_values=rnd or [1111, 2222, 3333])
    d.take()
    return d


def say(d, line, wait=0):
    d.feed_line(line)
    if wait:
        d.advance_to(d.now + wait)
    return d.take()


def armed_device():
    d = new()
    hello = say(d, "HELLO")[0]
    epoch = int(hello.split("epoch=")[1].split()[0])
    assert say(d, f"ARM {epoch}") == [f"ACCEPTED_NOT_EXECUTED ARM epoch={epoch}"]
    return d, epoch


def test_boots_disarmed_with_outputs_disabled():
    d = VirtualDevice(hardware_verified=False)
    assert not d.armed and not d.output_enabled and d.pwm.oe_high
    assert d.trace[0] == ("oe", 1)                  # OE driven high before anything else
    assert d.pwm.pulse_us() is None


def test_arm_refused_while_hardware_verified_is_false():
    d = new(verified=False)
    epoch = int(say(d, "HELLO")[0].split("epoch=")[1].split()[0])
    assert say(d, f"ARM {epoch}") == ["REJECTED ARM reason=hardware_verified_false"]
    assert not d.armed
    assert say(d, f"SET {epoch} 1 1500 100")[0].startswith("REJECTED SET reason=disarmed")


def test_hello_reports_version_limits_and_a_fresh_epoch_each_time():
    d = new()
    h1 = say(d, "HELLO")[0]
    h2 = say(d, "HELLO")[0]
    for key in ("proto=2", "fw=", "min_us=1450", "max_us=1550", "step_us=5", "epoch=", "hw_verified=1"):
        assert key in h1
    assert h1.split("epoch=")[1].split()[0] != h2.split("epoch=")[1].split()[0]


def test_replayed_arm_and_set_after_reconnect_are_rejected_and_do_not_move_anything():
    d, e1 = armed_device()
    sent = [f"ARM {e1}", f"SET {e1} 1 1510 100", f"SET {e1} 2 1520 100"]
    for ln in sent[1:]:
        say(d, ln, 12)
    d.advance_to(d.now + 400)                      # the link is gone; the lease runs out
    assert d.take()[-1] == "STOP lease_expired"
    # reconnect: a new session. Nothing from the old one may work
    e2 = int(say(d, "HELLO")[0].split("epoch=")[1].split()[0])
    assert e2 != e1
    before = (d.armed, d.output_enabled, d.target, d.applied, d.pwm.oe_high)
    out = [say(d, ln)[0] for ln in sent]
    assert all(o.startswith("REJECTED") for o in out)
    assert any("reason=epoch" in o for o in out)
    assert (d.armed, d.output_enabled, d.target, d.applied, d.pwm.oe_high) == before
    # even a healthy device (never latched) refuses the old epoch after a fresh HELLO
    d2, f1 = armed_device()
    say(d2, "DISARM")
    f2 = int(say(d2, "HELLO")[0].split("epoch=")[1].split()[0])
    assert f2 != f1
    assert say(d2, f"ARM {f1}") == ["REJECTED ARM reason=epoch"]
    assert say(d2, f"SET {f1} 1 1500 100")[0].startswith("REJECTED SET reason=epoch")
    assert not d2.armed


def test_replay_within_the_same_connection_after_disarm_is_rejected():
    d, e = armed_device()
    say(d, f"SET {e} 1 1510 100")
    say(d, "DISARM")
    assert say(d, f"ARM {e}") == ["REJECTED ARM reason=epoch"]       # DISARM ends the session
    assert say(d, f"SET {e} 2 1510 100")[0].startswith("REJECTED SET reason=epoch")


def test_duplicate_seq_is_a_harmless_acknowledgement():
    d, e = armed_device()
    assert say(d, f"SET {e} 1 1520 500")[0].startswith("ACCEPTED_NOT_EXECUTED SET seq=1")
    target, lease = d.target, d.lease_start
    d.advance_to(d.now + 50)
    d.take()
    out = say(d, f"SET {e} 1 1540 500")
    assert out == ["ACCEPTED_NOT_EXECUTED SET seq=1 dup=1"]
    assert d.target == target                       # the second value was not taken
    assert d.lease_start == lease                   # and a replay cannot extend the lease
    assert not d.fault and d.armed


def test_old_sequence_number_is_rejected_without_latching():
    d, e = armed_device()
    say(d, f"SET {e} 5 1520 500")
    assert say(d, f"SET {e} 4 1530 500") == ["REJECTED SET reason=seq_old seq=4"]
    assert not d.fault and d.armed
    assert say(d, f"SET {e} 6 1530 500")[0].startswith("ACCEPTED_NOT_EXECUTED SET seq=6")


def test_signed_or_malformed_numbers_never_become_huge_unsigned_values():
    d, e = armed_device()
    for bad in (f"SET {e} -1 1500 100", f"SET {e} 1 -1500 100", f"SET {e} +1 1500 100",
                f"SET {e} 1 1500.5 100", f"SET {e} 1 0x5DC 100", f"SET {e} 99999999999 1500 100",
                f"SET {e} 1 1500 100 x", f"SET {e} 1 1500", "SET", f"SET {e} 1  1500 100", f"SET {e} 5- 1500 100", f"SET {e} 1 1500- 100", f"SET {e} 1 15e2 100"):
        out = say(d, bad)
        assert out and out[0].startswith("REJECTED"), bad
    assert not d.fault and d.armed and d.last_seq == 0


@pytest.mark.parametrize("pulse", [1449, 1551, 0, 4294967295])
def test_out_of_range_pulse_is_rejected_without_latching(pulse):
    d, e = armed_device()
    assert say(d, f"SET {e} 1 {pulse} 100") == [f"REJECTED SET reason=range seq=1"]
    assert not d.fault and d.armed and not d.output_enabled
    assert say(d, f"SET {e} 2 1500 100")[0].startswith("ACCEPTED_NOT_EXECUTED")


@pytest.mark.parametrize("valid", [0, 19, 501, 100000])
def test_lease_length_out_of_range_is_rejected(valid):
    d, e = armed_device()
    assert say(d, f"SET {e} 1 1500 {valid}")[0].startswith("REJECTED SET reason=valid_for")


def test_rejections_never_extend_the_lease():
    d, e = armed_device()
    d.advance_to(d.now + 250)
    seen = []
    for _ in range(8):
        seen += say(d, f"SET {e} 1 9999 100")      # rejected: range
        d.advance_to(d.now + 10)
        seen += d.take()
    assert "STOP lease_expired" in seen            # the 300 ms grace ran out regardless
    assert d.fault and not d.armed


def test_lease_expiry_stops_and_latches_and_later_commands_are_rejected():
    d, e = armed_device()
    say(d, f"SET {e} 1 1520 50", 20)
    out = d.take()
    d.advance_to(d.now + 60)
    out += d.take()
    assert "STOP lease_expired" in out
    assert d.fault and not d.armed and d.pwm.oe_high and d.pwm.pulse_us() is None
    assert say(d, f"SET {e} 2 1520 50")[0].startswith("REJECTED SET reason=epoch")
    assert say(d, "CLEAR") == ["EXECUTED CLEAR"]
    assert not d.fault


def test_set_arriving_after_expiry_but_before_the_next_tick_is_rejected_as_lease_expired():
    d, e = armed_device()
    say(d, f"SET {e} 1 1520 50")
    d.now = (d.now + 80) & 0xFFFFFFFF                     # time passes, no tick ran yet
    out = say(d, f"SET {e} 2 1520 50")
    assert out[0] == "STOP lease_expired"
    assert out[1] == "REJECTED SET reason=lease_expired seq=2"


def test_grace_period_after_arm_is_short():
    d, e = armed_device()
    d.advance_to(d.now + 350)
    assert "STOP lease_expired" in d.take() and d.fault


def test_heartbeat_extends_the_lease_but_only_for_the_right_epoch():
    d, e = armed_device()
    say(d, f"SET {e} 1 1520 100")
    for _ in range(5):
        d.advance_to(d.now + 60)
        d.take()
        assert say(d, f"HEARTBEAT {e}") == ["ACCEPTED_NOT_EXECUTED HEARTBEAT"]
    assert d.armed
    assert say(d, f"HEARTBEAT {e + 1}") == ["REJECTED HEARTBEAT reason=epoch"]


def test_slew_limit_is_respected_at_every_tick():
    d, e = armed_device()
    say(d, f"SET {e} 1 1550 500")
    last = d.applied
    steps = []
    for _ in range(120):
        d.advance_to(d.now + 1)
        if d.applied != last:
            steps.append(abs(d.applied - last))
            last = d.applied
    assert steps and max(steps) <= MAX_STEP_US
    assert d.applied == 1550
    assert len(steps) == 10                         # 50 us in 5 us steps, one per 10 ms tick


def test_slew_limit_when_commands_jump_back_and_forth():
    d, e = armed_device()
    rng = random.Random(3)
    seq, last, t_last = 0, d.applied, d.now
    for _ in range(200):
        seq += 1
        say(d, f"SET {e} {seq} {rng.choice([1450, 1550])} 500")
        for _ in range(rng.choice([1, 5, 12, 30])):
            d.advance_to(d.now + 1)
            assert abs(d.applied - last) <= MAX_STEP_US
            last = d.applied
    assert not d.fault


def test_applied_value_is_reported_separately_from_commanded():
    d, e = armed_device()
    say(d, f"SET {e} 1 1550 500")
    d.advance_to(d.now + 25)
    d.take()
    st = say(d, "STATUS")[0]
    assert "commanded_us=1550" in st and "applied_us=1510" in st   # commanded is NOT applied


def test_executed_reports_the_pwm_write_not_a_position():
    d, e = armed_device()
    say(d, f"SET {e} 1 1510 500")
    d.advance_to(d.now + 15)
    out = d.take()
    ex = [o for o in out if o.startswith("EXECUTED SET")]
    assert len(ex) == 1 and "seq=1" in ex[0] and "applied_us=" in ex[0] and "mcu_ms=" in ex[0]


def test_output_stays_disabled_until_the_first_valid_set_and_starts_at_the_old_position():
    d, e = armed_device()
    assert d.pwm.oe_high and not d.output_enabled
    say(d, f"SET {e} 1 1530 500")
    assert not d.pwm.oe_high and d.output_enabled
    assert d.pwm.off_ticks == pulse_to_ticks(PULSE_CENTER)   # first pulse = where it already was


def test_rearm_starts_from_the_last_applied_value_not_from_centre():
    d, e = armed_device()
    say(d, f"SET {e} 1 1550 500")
    d.advance_to(d.now + 120)
    say(d, "DISARM")
    e2 = int(say(d, "HELLO")[0].split("epoch=")[1].split()[0])
    say(d, f"ARM {e2}")
    assert d.applied == 1550 and d.target == 1550


def test_overflow_and_nonprintable_bytes_stop_once():
    d, e = armed_device()
    say(d, f"SET {e} 1 1500 500")
    d.feed_text("A" * 200)
    out = d.take()
    assert out == ["STOP bad_line"]
    d.feed_text("B" * 50)
    d.feed_byte("\n")
    assert d.take() == ["REJECTED LINE reason=bad_line"]
    d.feed_line("STATUS")
    assert d.take()[0].startswith("STATUS")
    d2, e2 = armed_device()
    d2.feed_byte("\x01")
    assert d2.take() == ["STOP bad_line"] and d2.fault


def test_unknown_command_is_rejected_without_latching():
    d, e = armed_device()
    assert say(d, "FOO") == ["REJECTED UNKNOWN reason=unknown_command"]
    assert say(d, "SETT 1 1500 100") == ["REJECTED UNKNOWN reason=unknown_command"]
    assert not d.fault and d.armed


def test_estop_latches_and_clear_is_refused_while_armed():
    d, e = armed_device()
    assert say(d, f"CLEAR") == ["REJECTED CLEAR reason=armed"]
    assert say(d, "ESTOP") == ["STOP estop"]
    assert d.fault
    e2 = int(say(d, "HELLO")[0].split("epoch=")[1].split()[0])
    assert say(d, f"ARM {e2}") == ["REJECTED ARM reason=fault"]


def test_i2c_failure_stops_and_latches():
    d, e = armed_device()
    d.fail_i2c = True
    out = say(d, f"SET {e} 1 1520 100")
    assert "STOP i2c" in out and d.fault


def test_unused_channels_stay_full_off_the_old_0xfd_write_is_gone():
    d, e = armed_device()
    say(d, f"SET {e} 1 1520 500", 30)
    regs = [t for t in d.trace if t[0] == "i2c"]
    assert not any(t[1] == 0xFD and t[2] == (0x00,) for t in regs)   # would clear every channel's full-off bit
    assert any(t[1] == 0x06 and len(t[2]) == 4 for t in regs)         # one 4-byte write per pulse change


def test_pwm_is_written_only_when_the_count_changes():
    d, e = armed_device()
    d.take_trace()
    say(d, f"SET {e} 1 1500 500", 100)
    writes = [t for t in d.take_trace() if t[0] == "i2c" and t[1] == 0x06]
    assert len(writes) == 1                          # the initial write; no rewrites at 100 Hz


def test_random_traffic_never_breaks_invariants():
    rng = random.Random(11)
    d = new(rnd=[rng.getrandbits(32) | 1 for _ in range(500)])
    for _ in range(3000):
        r = rng.random()
        e = d.epoch or rng.getrandbits(32)
        if r < 0.1:
            say(d, "HELLO")
        elif r < 0.2:
            say(d, f"ARM {e}")
        elif r < 0.7:
            say(d, f"SET {e} {rng.randrange(0, 50)} {rng.randrange(1400, 1600)} {rng.choice([20, 100, 500])}")
        else:
            say(d, rng.choice(["STATUS", "DISARM", "ESTOP", "CLEAR", "JUNK"]))
        d.advance_to(d.now + rng.choice([1, 5, 10, 40]))
        assert 1450 <= d.applied <= 1550 and 1450 <= d.target <= 1550
        assert not d.output_enabled or d.armed       # outputs only while armed
        assert d.armed or d.pwm.oe_high              # disarmed => OE high


def test_simworld_end_to_end_property_stale_lines_from_a_previous_session_do_nothing():
    w = SimWorld(seed=5, wake_jitter_ms=None)
    c = DeviceClient(w)
    c.hello()
    c.arm()
    for i in range(4):
        c.send_set(1500 + 5 * i, 100)
        w.sleep_until_ns(w.now_ns() + 30_000_000)
        c.pump()
    old = list(c.sent_lines)
    w.unplug()
    w.sleep_until_ns(w.now_ns() + 300_000_000)
    w.replug()
    c2 = DeviceClient(w)
    c2.hello()
    for ln in old:
        w.write_line(ln)
    w.sleep_until_ns(w.now_ns() + 10_000_000)
    c2.pump()
    assert sum(c2.stats.rejected.values()) == len(old)
    assert not w.device.armed
    with pytest.raises(DeviceRefused):
        c2.arm()                                     # latched after the lease ran out: ARM refused
