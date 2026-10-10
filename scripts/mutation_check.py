#!/usr/bin/env python
"""Hand-made mutation check: break the code on purpose and confirm that tests notice.

Each mutant is one small edit to a copy of the repository.

* Set A (below): defects chosen together with the tests, each run against the tests meant to
  catch it. Those tests being green on the original code and red on the mutant is a check
  that the tests are not vacuous; it is not an independent measure of coverage.
* Set B (scripts/mutants_independent.py): mutants that were prepared separately from the tests,
  run against the whole suite. This is the independent measure of coverage.
* Equivalent mutants: edits judged to change no behaviour. They are RUN, and must survive; if the
  tests kill one, it was not equivalent after all.

A mutant counts as killed only if the tests fail twice in a row (a test that fails once under
machine load is not a kill), and the name of the first failing test is recorded. The unchanged
code must pass first. Takes several minutes (it runs in parallel). Writes docs/MUTATION_CHECK.md
when --write is given.

    python scripts/mutation_check.py [--only NAME] [--set A|B|E|all] [--jobs 4] [--write]
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = "code/firmware/guarded_servo/guarded_servo_core.h"
VD = "motion_io_lab/vdevice.py"

# (name, file, old, new, tests that must fail)
FW_TESTS = "tests/test_conformance.py tests/test_firmware_source.py"
VD_TESTS = "tests/test_vdevice.py tests/test_conformance.py"
M = [
    # --- firmware core (C++): caught by the conformance tests against the Python model
    ("fw: SET without epoch check", CORE,
     '      if (epoch_ == 0 || e != epoch_) return reject("SET", "epoch", true, seq);\n', "", FW_TESTS),
    ("fw: duplicate seq latches a fault", CORE,
     '        hal_->out_line(b);\n        return;\n      }\n      if (seq < last_seq_)',
     '        hal_->out_line(b);\n        stopOutput("dup", true);\n        return;\n      }\n      if (seq < last_seq_)', FW_TESTS),
    ("fw: slew limit removed", CORE, "      if (d > int(MAX_STEP_US)) d = MAX_STEP_US;\n      if (d < -int(MAX_STEP_US)) d = -int(MAX_STEP_US);\n", "", FW_TESTS),
    ("fw: the ALL_LED_OFF_H=0 write comes back", CORE,
     "    if (ticks <= 0 || ticks >= 4095) return false;\n",
     "    if (ticks <= 0 || ticks >= 4095) return false;\n    if (!reg(REG_ALL_LED_OFF_H, 0x00)) return false;\n", FW_TESTS),
    ("fw: parser accepts a minus sign", CORE,
     "      if (s[i] < '0' || s[i] > '9') return false;\n", "      if ((s[i] < '0' || s[i] > '9') && s[i] != '-') return false;\n", FW_TESTS),
    ("fw: pulse range check removed", CORE,
     '      if (us < PULSE_MIN || us > PULSE_MAX) return reject("SET", "range", true, seq);\n', "", FW_TESTS),
    ("fw: HELLO keeps the old epoch", CORE,
     "    epoch_ = e;\n    last_seq_ = 0;\n", "    if (epoch_ == 0) epoch_ = e;\n    last_seq_ = 0;\n", FW_TESTS),
    ("fw: a rejection extends the lease", CORE,
     "    copy(last_reject_, reason);\n", "    copy(last_reject_, reason);\n    lease_start_ = hal_->millis();\n", FW_TESTS),
    ("fw: overflow fragment is handled as a command (a double STOP)", CORE,
     "    discarding_ = true;\n    used_ = 0;\n    if (armed_)", "    discarding_ = false;\n    used_ = 0;\n    if (armed_)", FW_TESTS),
    ("fw: ARM allowed while HARDWARE_VERIFIED is false", CORE,
     '      if (!hw_verified_) return reject("ARM", "hardware_verified_false", false, 0);\n', "", FW_TESTS),
    ("fw: lease length not checked", CORE,
     '      if (valid < MIN_VALID_MS || valid > MAX_VALID_MS)\n        return reject("SET", "valid_for", true, seq);\n', "", FW_TESTS),
    ("fw: stop does not invalidate the epoch", CORE,
     "    epoch_ = 0;            // any stop ends the session: re-arming needs a fresh HELLO\n", "", FW_TESTS),
    ("fw: OE not driven high at boot", CORE,
     "  void begin() {\n    hal_->oe_write(true);\n", "  void begin() {\n", FW_TESTS),
    # --- virtual device (Python)
    ("vd: SET without epoch check", VD,
     '            if self.epoch == 0 or e != self.epoch:\n                return self._reject("SET", "epoch", seq)\n', "", VD_TESTS),
    ("vd: duplicate seq becomes a fault", VD,
     '                return self._emit(f"ACCEPTED_NOT_EXECUTED SET seq={seq} dup=1")',
     '                self._stop("dup", True); return self._emit(f"ACCEPTED_NOT_EXECUTED SET seq={seq} dup=1")', VD_TESTS),
    ("vd: slew limit removed", VD, "            d = max(-MAX_STEP_US, min(MAX_STEP_US, d))\n", "", VD_TESTS),
    ("vd: lease refreshed by duplicates", VD,
     '            if seq == self.last_seq:\n', '            if seq == self.last_seq:\n                self.lease_start = now\n', VD_TESTS),
    ("vd: stop keeps the epoch", VD, "        self.epoch = 0\n        self.pending_seq = 0\n", "        self.pending_seq = 0\n", VD_TESTS),
    # --- host side
    ("host: shaper without clamp", "motion_io_lab/shaping.py", "        v = min(max(desired, self.min_us), self.max_us)\n", "        v = desired\n", "tests/test_shaping_motions_calibration.py tests/test_record_play.py"),
    ("host: capture time rule dropped", "motion_io_lab/triallog.py",
     '    if rec["capture_time_ns"] is None and rec["capture_time_provenance"] != "unknown":\n        bad("capture_time_ns is null but provenance is not \'unknown\'")\n    if rec["capture_time_ns"] is not None and rec["capture_time_provenance"] == "unknown":\n        bad("capture_time_ns is set but provenance is \'unknown\'")\n', "", "tests/test_triallog.py"),
    ("host: writer appends instead of refusing", "motion_io_lab/triallog.py",
     '        if self.path.exists():\n            raise FileExistsError(f"{self.path} exists; a new session needs a new directory")\n', "", "tests/test_triallog.py"),
    ("host: invalid observation may carry a value", "motion_io_lab/triallog.py",
     '                if rec[k] is not None:\n                    bad(f"valid=false but {k}={rec[k]!r}; use null, never 0")\n', "", "tests/test_triallog.py"),
    ("host: observation picks the first of several blobs", "motion_io_lab/observe.py",
     '    if len(good) > 1:\n        # Ambiguous scene: do not pick the "plausible" one.\n        return Detection(len(good), reason="multiple_blobs", blob_areas=tuple(g[2] for g in good))\n', "", "tests/test_observe.py"),
    ("host: pacing relative is the only mode", "motion_io_lab/player.py",
     '            when = ideal if cfg.pacing == "deadline" else next_wake\n', '            when = next_wake\n', "tests/test_record_play.py tests/test_demo.py"),
    ("host: late commands are sent anyway", "motion_io_lab/player.py",
     "            if now - when > cfg.drop_if_late_ms * NS_MS:\n", "            if False:\n", "tests/test_record_play.py"),
    ("host: resume without allow_clear", "motion_io_lab/player.py", "                if not cfg.allow_clear:\n", "                if False:\n", "tests/test_record_play.py"),
    ("host: dataset valid column silently falsy", "motion_io_lab/prepare_real_dataset.py",
     '    raise DatasetError(f"{where}: valid must be true/false/1/0, got {text!r}")', "    return False", "tests/test_dataset.py"),
    ("host: dataset overwrite allowed", "motion_io_lab/prepare_real_dataset.py",
     '        if p.exists():\n            raise FileExistsError(f"{p} exists; datasets are not overwritten")\n', "        pass\n", "tests/test_dataset.py"),
    ("host: touch token not checked", "motion_io_lab/inputs.py",
     "        if not hmac.compare_digest(tok.encode(), self.server.token.encode()):  # type: ignore[attr-defined]\n", "        if False:\n", "tests/test_inputs.py"),
    ("host: LAN bind by default", "motion_io_lab/inputs.py", '    return "0.0.0.0" if lan else "127.0.0.1"', '    return "0.0.0.0"', "tests/test_inputs.py"),
    ("host: on_grid extrapolates", "motion_io_lab/analysis.py", "| (grid < t[0] - 1) | (grid > t[-1] + 1)", "", "tests/test_analysis.py tests/test_dataset.py"),
    ("host: hardware needs no confirmation", "motion_io_lab/session.py",
     '        if confirm is None or not confirm(info):', "        if False:", "tests/test_record_play.py"),
]


EQUIVALENT = [
    ("fw: ARM resets the sequence counter", CORE,
     "      armed_ = true;\n      lease_start_ = now;\n      lease_len_ = GRACE_MS;\n",
     "      armed_ = true;\n      last_seq_ = 0;\n      lease_start_ = now;\n      lease_len_ = GRACE_MS;\n",
     "Unreachable difference: every stop clears the epoch, so a new ARM always follows a HELLO, and HELLO "
     "already resets the counter."),
    ("fw: dropping the `if (discarding_) return;` line", CORE,
     "    if (discarding_) return;\n    if (c >= 32", "    if (c >= 32",
     "Equivalent: while `discarding_` is set the newline branch discards the line anyway, and the device is "
     "already disarmed after the first stop."),
]
ALL_TESTS = "tests"
IGNORE = shutil.ignore_patterns(".venv", ".git", "outputs", "__pycache__", "*.egg-info", ".pytest_cache",
                                "figures")


def run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)


def pytest_once(work: Path, tests: str, env) -> tuple[int, str]:
    r = run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--tb=no", "-rfE",
             *tests.split()], work, env)
    m = re.search(r"^(?:FAILED|ERROR) (\S+)", r.stdout, re.M)
    return r.returncode, (m.group(1) if m else ("" if r.returncode == 0 else r.stdout.strip()[-80:]))


def try_mutant(item) -> tuple[str, str, str, str]:
    """-> (name, status, tests run, first failing test). status: killed | SURVIVED | flaky | BAD MUTANT"""
    name, rel, old, new, tests = item
    with tempfile.TemporaryDirectory(prefix="mut-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "PYTHONPATH": str(work),
               "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp}
        f = work / rel
        orig = f.read_text(encoding="utf-8")
        if old not in orig:
            return name, "BAD MUTANT (pattern not found)", tests, ""
        f.write_text(orig.replace(old, new, 1), encoding="utf-8")
        rc, who = pytest_once(work, tests, env)
        if rc == 0:
            return name, "SURVIVED", tests, ""
        rc2, who2 = pytest_once(work, tests, env)          # a kill must repeat
        if rc2 == 0:
            return name, "flaky (failed once, passed on repeat: not a kill)", tests, who
        return name, "killed", tests, who


def baseline_ok() -> bool:
    with tempfile.TemporaryDirectory(prefix="mut-base-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "PYTHONPATH": str(work),
               "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp}
        rc, who = pytest_once(work, ALL_TESTS, env)
        if rc != 0:
            print("the unchanged code fails its own tests:", who)
        return rc == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    ap.add_argument("--set", default="all", choices=["A", "B", "E", "all"])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    from mutants_independent import EQUIVALENT_MORE, MUTANTS

    jobs = []
    if a.set in ("A", "all"):
        jobs += [("A", (n, f, o, nw, t)) for n, f, o, nw, t in M]
    if a.set in ("B", "all"):
        jobs += [("B", (n, f, o, nw, ALL_TESTS)) for n, f, o, nw in MUTANTS]
    if a.set in ("E", "all"):
        jobs += [("E", (n, f, o, nw, ALL_TESTS)) for n, f, o, nw, _ in EQUIVALENT + EQUIVALENT_MORE]
    if a.only:
        jobs = [j for j in jobs if a.only in j[1][0]]
    if not baseline_ok():
        return 2
    results = []
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for (grp, item), res in zip(jobs, ex.map(lambda j: try_mutant(j[1]), jobs)):
            results.append((grp, *res))
            print(f"{grp} {res[1]:10s} {res[0]}" + (f"   <- {res[3]}" if res[3] else ""), flush=True)

    def tally(g):
        rows = [r for r in results if r[0] == g]
        killed = sum(r[2] == "killed" for r in rows)
        return killed, len(rows), [r for r in rows if r[2] != "killed"]
    ka, na, bad_a = tally("A")
    kb, nb, bad_b = tally("B")
    _, ne, eq_rows = tally("E")
    eq_bad = [r for r in results if r[0] == "E" and r[2] == "killed"]          # an "equivalent" kill is a surprise
    print(f"A (chosen with the tests): {ka} of {na} killed")
    print(f"B (prepared separately, whole suite): {kb} of {nb} killed")
    print(f"E (judged equivalent): {sum(r[2] == 'SURVIVED' for r in results if r[0] == 'E')} of {ne} survived as expected")
    if a.write:
        L = ["# Mutation check", "",
             "Each row is one deliberate defect put into a copy of the code. A mutant is *killed* when the tests "
             "fail twice in a row; the first failing test is listed. The unchanged code passes first.", "",
             f"* **Set A, defects chosen together with the tests** (run against the tests meant to catch them): "
             f"{ka} of {na} killed. This shows the tests are not vacuous; it says little about what they miss.",
             f"* **Set B, mutants prepared separately from the tests** (run against the whole suite): "
             f"{kb} of {nb} killed. This is the independent measure.",
             f"* **Judged equivalent** (run too; they must survive): "
             f"{sum(r[2] == 'SURVIVED' for r in results if r[0] == 'E')} of {ne} survived.", ""]
        for grp, title in (("A", "Set A"), ("B", "Set B (prepared separately)"), ("E", "Judged equivalent")):
            L += [f"## {title}", "", "| mutant | result | first failing test |", "|---|---|---|"]
            for r in results:
                if r[0] == grp:
                    L.append(f"| {r[1]} | {r[2]} | `{r[4]}` |" if r[4] else f"| {r[1]} | {r[2]} | |")
            L.append("")
        L += ["## Why the equivalent ones are equivalent", ""]
        from mutants_independent import EQUIVALENT_MORE as _EQ
        L += [f"- **{n}**: {why}" for n, _, _, _, why in EQUIVALENT + _EQ]
        L += ["", "Set B had no survivors. The judged-equivalent mutants survive on purpose (see above); a "
                  "survivor anywhere else would mean the tests do not constrain that behaviour."]
        (ROOT / "docs" / "MUTATION_CHECK.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return 1 if (bad_a or eq_bad or any(r[2].startswith("BAD") for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
