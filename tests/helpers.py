"""Shared test helpers."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS_SRC = REPO / "tests" / "firmware_host" / "harness.cpp"


def compiler() -> str | None:
    for c in ("clang++", "g++", "c++"):
        if shutil.which(c):
            return c
    return None


_cache: dict[str, Path] = {}


def build_harness() -> Path | None:
    """Compile the real firmware core for the host. None if no C++ compiler exists."""
    if "bin" in _cache:
        return _cache["bin"]
    cxx = compiler()
    if cxx is None:
        return None
    out = Path(tempfile.mkdtemp(prefix="gs_harness_")) / "harness"
    r = subprocess.run([cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-o", str(out),
                        str(HARNESS_SRC)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("firmware core does not compile for the host:\n" + r.stderr)
    _cache["bin"] = out
    return out


def run_harness(script: list[str], verified: bool = True) -> tuple[list[str], list[str]]:
    """Run a script through the compiled core. Returns (device lines, trace lines)."""
    binary = build_harness()
    assert binary is not None
    args = [str(binary)] + (["--verified"] if verified else [])
    r = subprocess.run(args, input="\n".join(script) + "\n", capture_output=True, text=True,
                       timeout=60)
    lines, trace = [], []
    for ln in r.stdout.splitlines():
        if ln.startswith("> "):
            lines.append(ln[2:])
        elif ln.startswith("# "):
            trace.append(ln[2:])
    return lines, trace


def run_python(script: list[str], verified: bool = True) -> tuple[list[str], list[str]]:
    """Run the same script through the Python VirtualDevice."""
    from motion_io_lab.vdevice import VirtualDevice

    dev = VirtualDevice(hardware_verified=verified, board="HOST", now_ms=0)
    rnd: list[int] = []
    dev._rnd = rnd
    lines: list[str] = []
    trace: list[str] = []

    def flush():
        lines.extend(dev.take())
        for t in dev.take_trace():
            if t[0] == "oe":
                trace.append(f"oe {t[1]}")
            else:
                trace.append("i2c %02x " % t[1] + " ".join("%02x" % b for b in t[2]))

    flush()
    for ln in script:
        op, arg = ln[0], ln[2:]
        if op == "W":
            dev.advance_to(dev.now + int(arg))
        elif op == "L":
            dev.feed_line(arg)
        elif op == "R":
            for i in range(0, len(arg) - 1, 2):
                dev.feed_byte(chr(int(arg[i:i + 2], 16)))
        elif op == "E":
            rnd.append(int(arg))
        elif op == "T":
            dev.now = int(arg) & 0xFFFFFFFF
        elif op == "F":
            dev.fail_i2c = arg == "1"
        elif op == "G":
            dev.fail_i2c_next = int(arg)
        flush()
    return lines, trace
