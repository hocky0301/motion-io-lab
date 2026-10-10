#!/usr/bin/env bash
# Clone this repository into an empty folder and prove, from nothing but the README steps, that
#   1. the pinned dependencies install,   2. the tests pass AND none was skipped,
#   3. the demo runs,   4. its summary matches reference_results/ (identical, or only the
#   video-derived numbers differ within the cross-build tolerance),   5. the firmware core compiles.
#
#   scripts/verify_fresh_clone.sh [path-or-url-of-this-repo]     (default: this checkout)
#
# Environment variables:
#   PYTHON=python3.12      interpreter to use (3.11 or newer)
#   LOCK=lowest            install requirements-lowest.txt (the oldest allowed versions) instead of
#                          the pinned requirements-lock.txt
#   ALLOW_SKIP=1           do not fail when some tests are skipped (e.g. no C++ compiler: then the
#                          claim "the Python model matches the firmware core line by line" is NOT checked)
#
# Needs: python3.11+ , git, a C++ compiler (for the firmware conformance tests).
# Never touches hardware. Takes a few minutes (dependency download + ~30 s demo).
# Only committed files are cloned: uncommitted edits in the source folder are not tested.
set -euo pipefail

SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="${PYTHON:-python3}"
LOCK="${LOCK:-pinned}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/motion-io-lab-verify.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

if [ -d "$SRC/.git" ] && [ -n "$(git -C "$SRC" status --porcelain 2>/dev/null)" ]; then
  echo "WARNING: $SRC has uncommitted changes. The clone below contains only what is committed."
fi

echo "== clone $SRC -> $WORK/repo"
git clone --quiet "$SRC" "$WORK/repo"
cd "$WORK/repo"

echo "== python: $($PY --version)"
"$PY" - <<'PYCHECK'
import sys
assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"
PYCHECK

echo "== venv + install ($LOCK)"
"$PY" -m venv .venv
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
if [ "$LOCK" = "lowest" ]; then
  python -m pip install --quiet -r requirements-lowest.txt
else
  python -m pip install --quiet -r requirements-lock.txt -r requirements-test-lock.txt
fi
python -m pip install --quiet --no-deps -e .
python -c "import numpy, cv2, matplotlib, serial; print('numpy', numpy.__version__, '| opencv', cv2.__version__, '| matplotlib', matplotlib.__version__, '| pyserial', serial.__version__)"

echo "== tests (a skipped test is a failure unless ALLOW_SKIP=1)"
# -o addopts= : the project default is -q, and a second -q would hide the summary line
python -m pytest -o addopts= -rs -p no:cacheprovider | tee "$WORK/pytest.log" | tail -n 15
if grep -Eq '^SKIPPED|[0-9]+ skipped' "$WORK/pytest.log"; then
  if [ "${ALLOW_SKIP:-0}" = "1" ]; then
    echo "NOTE: some tests were skipped (ALLOW_SKIP=1); the claims they cover were not checked here"
  else
    echo "FAIL: some tests were skipped, so some claims were not checked (see the SKIPPED lines above)."
    echo "      Install what they need (e.g. a C++ compiler) or set ALLOW_SKIP=1 to accept that."
    exit 1
  fi
fi

echo "== demo (SYNTHETIC)"
python -m motion_io_lab demo --out outputs/demo

echo "== compare with reference_results"
if [ "$LOCK" = "lowest" ]; then
  # the reference was made with the pinned set; the oldest set may differ in the video-derived numbers
  python -m motion_io_lab compare-reference --summary outputs/demo/summary.json
else
  # same versions as the reference: every number must match
  python -m motion_io_lab compare-reference --summary outputs/demo/summary.json --require-identical \
    || { echo "(the pinned versions should reproduce the reference exactly; on a different CPU or OS"; \
         echo " run 'compare-reference' without --require-identical to see how far it is off)"; exit 1; }
fi

echo "== every trial log validates"
python -m motion_io_lab validate outputs/demo > /dev/null

echo "== figures regenerate"
python scripts/make_figures.py --demo outputs/demo --out outputs/figures

if command -v arduino-cli >/dev/null 2>&1 && arduino-cli core list 2>/dev/null | grep -q '^esp32:esp32'; then
  echo "== firmware compile check (arduino-cli, XIAO_ESP32S3, no upload)"
  arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all \
      --build-path "$WORK/fw_build" code/firmware/guarded_servo | tail -3
else
  echo "== firmware compile check skipped (arduino-cli or the esp32 core is not installed);"
  echo "   the host build of the same core ran inside the test suite"
fi

echo "OK: fresh clone verified"
