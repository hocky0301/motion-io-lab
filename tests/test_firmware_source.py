"""Static and behavioural guards on the firmware sources."""
import re
from pathlib import Path

import pytest

from tests.helpers import build_harness, run_harness

FW = Path(__file__).resolve().parent.parent / "code" / "firmware" / "guarded_servo"
INO = (FW / "guarded_servo.ino").read_text()
CORE = (FW / "guarded_servo_core.h").read_text()


def test_hardware_verified_stays_false_in_the_published_sketch():
    assert re.search(r"constexpr\s+bool\s+HARDWARE_VERIFIED\s*=\s*false\s*;", INO)
    assert "HARDWARE_VERIFIED = true" not in INO + CORE


def test_boot_sequence_drives_oe_high_before_anything_else():
    setup = INO[INO.index("void setup()"):]
    assert setup.index("pinMode(OE_PIN, OUTPUT)") < setup.index("digitalWrite(OE_PIN, HIGH)") < setup.index("Wire.begin")
    assert "oe_write(true)" in CORE[CORE.index("void begin()"):CORE.index("void feed")]


def test_the_all_led_off_h_clear_write_is_gone_and_the_datasheet_is_cited():
    assert "reg(0xFD, 0x00)" not in CORE and "REG_ALL_LED_OFF_H, 0x00" not in CORE
    assert "Table 8" in CORE and "7.3.3" in CORE


def test_no_signed_scanf_and_no_unbounded_parse():
    assert "sscanf" not in INO + CORE and "strtoul" not in INO + CORE
    assert "parseU32" in CORE


def test_atoms3_is_a_compile_error_not_a_guess():
    assert re.search(r"#if defined\(BOARD_ATOMS3_LITE\)\s*\n#error", INO)
    assert "5, 6" not in INO.split("BOARD_ATOMS3_LITE")[1].split("#else")[0]


def test_xiao_pin_map_is_the_documented_one():
    assert re.search(r"SDA_PIN\s*=\s*5,\s*SCL_PIN\s*=\s*6,\s*OE_PIN\s*=\s*1", INO)
    assert "wiki.seeedstudio.com/xiao_esp32s3_getting_started" in INO


@pytest.mark.skipif(build_harness() is None, reason="no C++ compiler")
def test_compiled_core_boots_disarmed_with_oe_high_and_refuses_arm_unverified():
    lines, trace = run_harness(["L HELLO", "L ARM 4096", "L SET 4096 1 1500 100"], verified=False)
    assert trace[0] == "oe 1"
    assert lines[0].startswith("BOOT DISARMED")
    assert "REJECTED ARM reason=hardware_verified_false" in lines
    assert not any(l.startswith("ACCEPTED") for l in lines)
    assert "oe 0" not in trace                       # the output was never enabled


@pytest.mark.skipif(build_harness() is None, reason="no C++ compiler")
def test_compiled_core_only_ever_writes_channel_0_pulses_and_full_off():
    script = ["L HELLO", "L ARM 4096", "L SET 4096 1 1550 500", "W 150", "L SET 4096 2 1450 500", "W 250",
              "L DISARM"]
    _, trace = run_harness(script)
    regs = [t.split()[1] for t in trace if t.startswith("i2c")]
    assert set(regs) <= {"00", "fe", "fd", "06"}
    assert all(t.startswith("i2c 06 00 00") for t in trace if t.startswith("i2c 06"))
    assert all(t.split()[2:] == ["10"] for t in trace if t.startswith("i2c fd"))
