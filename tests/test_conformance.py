"""The Python virtual device must behave exactly like the compiled firmware core."""
import random

import pytest

from tests.helpers import build_harness, run_harness, run_python

pytestmark = pytest.mark.skipif(build_harness() is None, reason="no C++ compiler on this machine")

HAND_SCRIPT = [
    "L STATUS", "L HELLO", "L ARM 4096", "W 5", "L SET 4096 1 1510 100", "W 30",
    "L SET 4096 1 1555 100", "L SET 4096 2 1555 100", "L SET 4096 2 -1 100",
    "L SET 4096 3 1520 5", "L SET 4096 0 1520 100", "L SET 4096 4 1520 100", "W 200",
    "L STATUS", "L ARM 4096", "L HELLO", "E 77", "L HELLO", "L ARM 77", "L SET 77 1 1500 50",
    "W 10", "L HEARTBEAT 77", "W 40", "L SET 77 2 1540 50", "L DISARM", "L SET 77 3 1500 50",
    "L CLEAR", "L FOO", "L SET 1 2 3", "L  SET", "L SET 77 9 1500 50 x", "R 00", "L ESTOP",
    "L CLEAR", "L HELLO", "L ARM 4097",
    "L SET 4097 5- 1500 100", "L SET 4097 -5 1500 100", "L SET 4097 + 1500 100", "L SET 4097 1 1500- 100",
    "L SET 4097 1 15e2 100", "L SET 4097 1 0000001500 100", "L SET 4097 1 4294967296 100",
    "L ARM -", "L ARM 4097-", "L HEARTBEAT 4097x",
]


def _assert_same(script, verified=True):
    fw_lines, fw_trace = run_harness(script, verified)
    py_lines, py_trace = run_python(script, verified)
    assert py_lines == fw_lines
    assert py_trace == fw_trace


def test_hand_written_script_matches_firmware_line_for_line():
    _assert_same(HAND_SCRIPT)


def test_not_verified_firmware_refuses_arm_in_both():
    script = ["L CLEAR", "L HELLO", "L ARM 4096", "L SET 4096 1 1500 100"]
    fw, _ = run_harness(script, verified=False)
    assert any("reason=hardware_verified_false" in l for l in fw)
    _assert_same(script, verified=False)


def test_overflow_and_bad_byte_produce_one_stop_and_one_rejection():
    script = ["L HELLO", "L ARM 4096", "L SET 4096 1 1500 100",
              "R " + "41" * 150 + "0a", "L STATUS"]
    fw, _ = run_harness(script)
    assert sum(l.startswith("STOP") for l in fw) == 1
    assert sum(l.startswith("REJECTED LINE reason=bad_line") for l in fw) == 1
    _assert_same(script)


def test_an_i2c_write_that_fails_stops_and_latches_in_both():
    # a write that fails while armed (the tick that moves the output)
    script = ["E 4096", "L CLEAR", "L HELLO", "L ARM 4096", "W 5", "L SET 4096 1 1510 100",
              "F 1", "W 60", "L STATUS", "F 0", "L CLEAR", "L HELLO", "L ARM 4096"]
    fw, _ = run_harness(script)
    assert any(l.startswith("STOP i2c") for l in fw)
    assert any(" fault=1 " in l for l in fw if l.startswith("STATUS"))
    _assert_same(script)
    # only ONE write fails (the bus works again at once): the stop must still latch a fault by itself
    one = ["E 4096", "L CLEAR", "L HELLO", "L ARM 4096", "W 5", "L SET 4096 1 1510 100", "G 1", "W 60",
           "L STATUS"]
    fw1, _ = run_harness(one)
    assert any(l.startswith("STOP i2c") for l in fw1)
    assert [l for l in fw1 if l.startswith("STATUS")][-1].split()[2] == "fault=1"
    _assert_same(one)
    # a write that fails while the PWM chip is being set up (CLEAR re-initialises it): the
    # device latches a fault, and a later CLEAR with a working bus recovers
    boot = ["F 1", "L CLEAR", "L STATUS", "F 0", "L CLEAR", "L STATUS"]
    fw2, _ = run_harness(boot)
    assert "STOP i2c" in fw2 and "EXECUTED CLEAR" in fw2
    _assert_same(boot)


def test_the_32_bit_millisecond_clock_wraps_the_same_way_in_both():
    # the lease must run out 100 ms after the last SET even when the counter wraps in between
    script = ["T 4294967246", "E 4096", "L CLEAR", "L HELLO", "L ARM 4096", "W 5",
              "L SET 4096 1 1510 100", "W 150", "L STATUS", "L SET 4096 2 1520 100"]
    fw, _ = run_harness(script)
    assert any(l.startswith("STOP lease_expired") for l in fw)
    _assert_same(script)
    # and a lease that is still valid across the wrap is not cut short
    ok = ["T 4294967286", "E 4096", "L CLEAR", "L HELLO", "L ARM 4096", "W 5",
          "L SET 4096 1 1510 100", "W 40", "L SET 4096 2 1520 100", "W 40", "L SET 4096 3 1530 100",
          "W 40", "L STATUS"]
    fw_ok, _ = run_harness(ok)
    assert not any(l.startswith("STOP") for l in fw_ok)
    _assert_same(ok)


def _random_script(rng: random.Random, n: int) -> list[str]:
    """Mostly well-formed traffic with occasional corruption, so the armed paths are covered."""
    epoch = 4096
    seq = 0
    s = ["E 4096", "L CLEAR", "L HELLO", "L ARM 4096"]
    odd = ["STATUS", "DISARM", "ESTOP", "CLEAR", "FOO", "SET 1 2 3", "ARM 1", "SET -1 2 1500 100",
           "HEARTBEAT 1", "SET 4096 1 1500 100 x", "X" * 120]
    for _ in range(n):
        r = rng.random()
        if r < 0.05:
            epoch = rng.getrandbits(32) | 1
            s += [f"E {epoch}", "L HELLO"]
            seq = 0
        elif r < 0.12:
            epoch = rng.getrandbits(32) | 1   # human recovery: CLEAR, new HELLO, ARM
            s += ["L CLEAR", f"E {epoch}", "L HELLO", f"L ARM {epoch}"]
            seq = 0
        elif r < 0.17:
            s.append(f"L HEARTBEAT {epoch}")
        elif r < 0.30:
            s.append("L " + rng.choice(odd))
        else:
            seq = seq + rng.choice([1, 1, 1, 1, 0, 2, -1]) if rng.random() < 0.95 else rng.randrange(0, 2**32)
            seq = max(0, seq)
            e = epoch if rng.random() < 0.95 else rng.getrandbits(32)
            pulse = rng.choice([1500, 1510, 1490, 1450, 1550, 1449, 1551, rng.randrange(1440, 1560)])
            valid = rng.choice([100, 100, 100, 40, 20, 19, 500, 501, 0])
            s.append(f"L SET {e} {seq} {pulse} {valid}")
        s.append(f"W {rng.choice([1, 3, 10, 12, 25, 60, 150, 320, 700])}")
    return s


@pytest.mark.parametrize("seed", range(60))
def test_random_scripts_agree_with_firmware(seed):
    _assert_same(_random_script(random.Random(seed), 60))
