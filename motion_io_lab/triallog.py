"""Trial log schema v1: three separate JSONL streams (input, command, observation).

One trial is one directory:

    <trial_id>/input.jsonl        what the person did (browser / keyboard / script)
    <trial_id>/command.jsonl      what the host sent, and what the device answered
    <trial_id>/observation.jsonl  what a camera saw (written by a different process)

Rules that the writer and validator enforce:

* line 1 of every file is a ``header`` record; every later line carries the common
  fields listed in ``docs/trial_schema_v1.json`` (``COMMON_FIELDS``), null when not
  applicable;
* a file is written to ``<name>.part`` and published atomically when closed; an
  existing file is never appended to or overwritten (new session -> new directory);
* a commanded value and an observed value never share a column name
  (``commanded_*`` / ``device_*`` / ``observed_*``);
* ``capture_time_ns`` is null unless a device really supplied an exposure time.
  The host receive time is never copied into it;
* ``valid=false`` means "no measurement": the value columns must be null, not 0.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__

SCHEMA_VERSION = "motion-io-lab/trial-v1"
STREAMS = ("input", "command", "observation")
MODES = ("DRY_RUN", "LOOPBACK", "HARDWARE_ARMED", "REPLAY")
LINKS = ("none", "virtual", "serial")
SOURCES = ("synthetic", "rendered_video", "recorded", "hardware")
PROVENANCE = ("device", "estimated", "unknown")
CLOCK_DOMAINS = ("host_monotonic", "device_millis", "browser_performance", "media_position")

COMMON_FIELDS = (
    "schema_version", "session_id", "trial_id", "stream", "record_type", "mode",
    "session_epoch", "source", "clock_domain", "capture_time_ns",
    "capture_time_provenance", "received_monotonic_ns", "processed_monotonic_ns",
    "units", "coordinate_frame", "calibration_version", "valid",
)

# Extra required columns per (stream, record_type). Value None is allowed when the
# record is marked valid=false (or the column is documented as nullable).
STREAM_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("input", "input_event"): ("event_id", "input_kind", "input_value", "client_time_ms",
                               "client_clock_domain"),
    ("input", "sync"): ("client_offset_ms", "rtt_ms", "samples"),
    ("command", "set"): ("seq", "desired_pulse_us", "commanded_pulse_us", "valid_for_ms",
                         "scheduled_monotonic_ns", "sent_monotonic_ns", "shaping"),
    ("command", "reply"): ("reply_status", "reply_reason", "reply_to_seq", "device_millis",
                           "device_applied_pulse_us", "raw"),
    ("command", "status"): ("device_commanded_pulse_us", "device_applied_pulse_us",
                            "device_millis", "armed", "fault", "last_reject", "last_stop",
                            "raw"),
    ("command", "event"): ("event", "detail"),
    ("observation", "frame"): ("frame_id", "source_kind", "media_pos_ms", "host_before_read_ns",
                               "host_after_read_ns"),
    ("observation", "marker"): ("frame_id", "media_pos_ms", "source_kind", "observed_value",
                                "cx_px", "cy_px", "area_px", "n_blobs", "invalid_reason"),
}
REPLY_STATUSES = ("REJECTED", "ACCEPTED_NOT_EXECUTED", "EXECUTED")
SOURCE_KINDS = ("live", "file")
INVALID_REASONS = (None, "none_found", "multiple_blobs", "no_calibration", "too_close_to_pivot",
                   "read_failed", "out_of_range")


class TrialLogError(ValueError):
    """Raised with every problem found, each prefixed by file and line number."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors[:20]) + (f"\n... {len(errors) - 20} more" if len(errors) > 20 else ""))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def check_id(name: str, value: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):   # fullmatch: "abc\n" is not an id
        raise ValueError(f"{name} must match {_ID_RE.pattern}: {value!r}")
    return value


@dataclass
class TrialContext:
    """Identity and defaults shared by the records of one trial."""

    session_id: str
    trial_id: str
    mode: str
    source: str
    link: str = "none"
    armed: bool = False
    session_epoch: int | None = None
    calibration_version: str | None = None
    firmware_version: str | None = None
    notes: str | None = None
    config: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        check_id("session_id", self.session_id)
        check_id("trial_id", self.trial_id)
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}: {self.mode!r}")
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}: {self.source!r}")
        if self.link not in LINKS:
            raise ValueError(f"link must be one of {LINKS}: {self.link!r}")
        if self.mode == "DRY_RUN" and self.link != "none":
            raise ValueError("DRY_RUN must not have a link")
        if self.mode == "LOOPBACK" and self.link != "virtual":
            raise ValueError("LOOPBACK uses the virtual device link")
        if self.mode == "HARDWARE_ARMED" and self.link != "serial":
            raise ValueError("HARDWARE_ARMED uses the serial link")
        if self.source == "hardware" and self.mode in ("DRY_RUN", "LOOPBACK"):
            raise ValueError("source=hardware needs a hardware mode")


def _finite(v: Any) -> bool:
    return not isinstance(v, float) or math.isfinite(v)


def _all_finite(v: Any) -> bool:
    """True when no number anywhere inside ``v`` is NaN or infinite (``1e999`` parses to inf)."""
    if isinstance(v, float):
        return math.isfinite(v)
    if isinstance(v, dict):
        return all(_all_finite(x) for x in v.values())
    if isinstance(v, list):
        return all(_all_finite(x) for x in v)
    return True


class StreamWriter:
    """Write one stream atomically. Use as a context manager."""

    def __init__(self, path: str | os.PathLike, stream: str, ctx: TrialContext, *,
                 units: str, coordinate_frame: str, clock_domain: str = "host_monotonic",
                 header_extra: dict | None = None):
        if stream not in STREAMS:
            raise ValueError(f"stream must be one of {STREAMS}")
        if clock_domain not in CLOCK_DOMAINS:
            raise ValueError(f"clock_domain must be one of {CLOCK_DOMAINS}")
        self.path = Path(path)
        self.stream = stream
        self.ctx = ctx
        self.units = units
        self.coordinate_frame = coordinate_frame
        self.clock_domain = clock_domain
        self.part = self.path.with_name(self.path.name + ".part")
        if self.path.exists():
            raise FileExistsError(f"{self.path} exists; a new session needs a new directory")
        if self.part.exists():
            raise FileExistsError(f"{self.part} is left over from an interrupted run; remove it first")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.part.open("x", encoding="utf-8")
        self.count = 0
        self._closed = False
        header = self._base("header")
        header.update({
            "created_utc": utc_now(),
            "link": ctx.link,
            "armed": ctx.armed,
            "firmware_version": ctx.firmware_version,
            "tool": {"name": "motion-io-lab", "version": __version__},
            "notes": ctx.notes,
            "config": ctx.config,
        })
        if header_extra:
            header.update(header_extra)
        try:
            self._write_line(header)
        except Exception:
            self._f.close()                 # a header that cannot be written leaves nothing behind
            self._closed = True
            self.part.unlink(missing_ok=True)
            raise

    def _base(self, record_type: str) -> dict:
        c = self.ctx
        return {
            "schema_version": SCHEMA_VERSION, "session_id": c.session_id,
            "trial_id": c.trial_id, "stream": self.stream, "record_type": record_type,
            "mode": c.mode, "session_epoch": c.session_epoch, "source": c.source,
            "clock_domain": self.clock_domain, "capture_time_ns": None,
            "capture_time_provenance": "unknown", "received_monotonic_ns": None,
            "processed_monotonic_ns": None, "units": self.units,
            "coordinate_frame": self.coordinate_frame,
            "calibration_version": c.calibration_version, "valid": True,
        }

    def _write_line(self, rec: dict) -> None:
        for k, v in rec.items():
            if not _finite(v):
                raise ValueError(f"non-finite value in {k!r}: write null and valid=false instead")
        try:
            text = json.dumps(rec, sort_keys=False, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)   # also catches nested NaN
        except ValueError as exc:
            raise TrialLogError([f"{self.path.name}: a value is not finite ({exc}); "
                                 "write null and valid=false instead"]) from exc
        self._f.write(text + "\n")
        self._f.flush()

    def write(self, record_type: str, **fields: Any) -> dict:
        if self._closed:
            raise ValueError("stream is closed")
        rec = self._base(record_type)
        rec["session_epoch"] = self.ctx.session_epoch
        rec.update(fields)
        errs = validate_record(rec, f"{self.path.name}:{self.count + 2}")
        if errs:
            raise TrialLogError(errs)
        self._write_line(rec)
        self.count += 1
        return rec

    def close(self) -> None:
        if self._closed:
            return
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        self._closed = True
        try:
            os.link(self.part, self.path)  # fails if the target appeared meanwhile
        except FileExistsError:
            raise
        except OSError:
            if self.path.exists():
                raise FileExistsError(str(self.path))
            os.replace(self.part, self.path)
            return
        os.unlink(self.part)

    def abort(self) -> None:
        """Leave the .part file for inspection; never publish a half-written stream."""
        if not self._closed:
            self._f.close()
            self._closed = True

    def __enter__(self) -> "StreamWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


# ---------------------------------------------------------------------------
# validation


def validate_record(rec: dict, where: str) -> list[str]:
    errs: list[str] = []

    def bad(msg: str) -> None:
        errs.append(f"{where}: {msg}")

    missing = [k for k in COMMON_FIELDS if k not in rec]
    if missing:
        bad(f"missing common fields {missing}")
        return errs
    if rec["schema_version"] != SCHEMA_VERSION:
        bad(f"schema_version {rec['schema_version']!r} != {SCHEMA_VERSION!r}")
    if rec["stream"] not in STREAMS:
        bad(f"stream {rec['stream']!r}")
    if rec["mode"] not in MODES:
        bad(f"mode {rec['mode']!r}")
    if rec["source"] not in SOURCES:
        bad(f"source {rec['source']!r}")
    if rec["clock_domain"] not in CLOCK_DOMAINS:
        bad(f"clock_domain {rec['clock_domain']!r}")
    if rec["capture_time_provenance"] not in PROVENANCE:
        bad(f"capture_time_provenance {rec['capture_time_provenance']!r}")
    if not isinstance(rec["valid"], bool):
        bad("valid must be a JSON boolean")
    if rec["session_epoch"] is not None and (
            not isinstance(rec["session_epoch"], int) or isinstance(rec["session_epoch"], bool)):
        bad("session_epoch must be an integer or null")
    # capture time rule: never invented
    if rec["capture_time_ns"] is None and rec["capture_time_provenance"] != "unknown":
        bad("capture_time_ns is null but provenance is not 'unknown'")
    if rec["capture_time_ns"] is not None and rec["capture_time_provenance"] == "unknown":
        bad("capture_time_ns is set but provenance is 'unknown'")
    for k in ("capture_time_ns", "received_monotonic_ns", "processed_monotonic_ns"):
        v = rec[k]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            bad(f"{k} must be a non-negative integer or null")
    if rec["record_type"] == "header":
        return errs
    key = (rec["stream"], rec["record_type"])
    need = STREAM_FIELDS.get(key)
    if need is None:
        bad(f"unknown record_type {rec['record_type']!r} for stream {rec['stream']!r}")
        return errs
    miss = [k for k in need if k not in rec]
    if miss:
        bad(f"{key} missing {miss}")
        return errs
    # value columns must be null when the row says "no measurement"
    if key == ("observation", "marker"):
        if rec["source_kind"] not in SOURCE_KINDS:
            bad(f"source_kind {rec['source_kind']!r}")
        if rec["invalid_reason"] not in INVALID_REASONS:
            bad(f"invalid_reason {rec['invalid_reason']!r}")
        if not rec["valid"]:
            for k in ("observed_value", "cx_px", "cy_px", "area_px"):
                if k == "area_px" and rec["invalid_reason"] == "multiple_blobs":
                    continue
                if rec[k] is not None:
                    bad(f"valid=false but {k}={rec[k]!r}; use null, never 0")
            if rec["invalid_reason"] is None:
                bad("valid=false needs an invalid_reason")
        else:
            if rec["cx_px"] is None or rec["cy_px"] is None:
                bad("valid=true needs cx_px and cy_px")
            if rec["invalid_reason"] is not None:
                bad("valid=true must have invalid_reason=null")
    if key == ("input", "input_event"):
        if rec["valid"] and rec["input_value"] is None:
            bad("valid=true needs input_value")
        if not rec["valid"] and rec["input_value"] is not None:
            bad("valid=false but input_value is set")
    if key == ("command", "set"):
        if rec["valid"]:
            for k in ("seq", "commanded_pulse_us", "valid_for_ms"):
                if not isinstance(rec[k], (int, float)) or isinstance(rec[k], bool):
                    bad(f"{k} must be a number when valid=true")
        elif rec["commanded_pulse_us"] is not None and rec.get("drop_reason") is None:
            bad("valid=false (not sent) needs drop_reason")
    if key == ("command", "reply"):
        if rec["reply_status"] not in REPLY_STATUSES:
            bad(f"reply_status {rec['reply_status']!r}")
        if rec["reply_status"] == "REJECTED" and not rec["reply_reason"]:
            bad("REJECTED needs reply_reason")
        if rec["received_monotonic_ns"] is None:
            bad("a reply needs received_monotonic_ns")
    if key == ("command", "status") and rec["received_monotonic_ns"] is None:
        bad("a status needs received_monotonic_ns")
    return errs


def validate_stream(path: str | os.PathLike) -> tuple[dict | None, list[dict], list[str]]:
    """Return (header, records, errors). Never raises for content problems."""
    path = Path(path)
    errs: list[str] = []
    header: dict | None = None
    records: list[dict] = []
    if path.name.endswith(".part"):
        return None, [], [f"{path.name}: unfinished stream (.part); the writer did not close it"]
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            lines = f.read().split("\n")
    except UnicodeDecodeError as exc:
        return None, [], [f"{path.name}: not valid UTF-8 (byte {exc.start})"]
    if lines and lines[-1] == "":
        lines.pop()
    last_rx: int | None = None
    last_seq: dict[Any, int] = {}
    last_sched: int | None = None
    last_sent: int | None = None
    stem = path.name[:-len(".jsonl")] if path.name.endswith(".jsonl") else None
    for i, text in enumerate(lines, start=1):
        where = f"{path.name}:{i}"
        if not text.strip():
            errs.append(f"{where}: blank line")
            continue
        try:
            rec = json.loads(text, parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            errs.append(f"{where}: not valid JSON ({exc})")
            continue
        if not isinstance(rec, dict):
            errs.append(f"{where}: record is not an object")
            continue
        if i == 1:
            if rec.get("record_type") != "header":
                errs.append(f"{where}: first line must be the header record")
                continue
            header = rec
            if stem in STREAMS and rec.get("stream") != stem:
                errs.append(f"{where}: header says stream {rec.get('stream')!r} but the file is "
                            f"{path.name} (a stream copied under another name?)")
        elif rec.get("record_type") == "header":
            errs.append(f"{where}: second header (appended session?)")
            continue
        errs.extend(validate_record(rec, where))
        if not _all_finite(rec):
            errs.append(f"{where}: a number is not finite (NaN or infinity, e.g. 1e999)")
        if header is not None and i > 1:
            for k in ("session_id", "trial_id", "stream", "mode"):
                if rec.get(k) != header.get(k):
                    errs.append(f"{where}: {k} {rec.get(k)!r} differs from header {header.get(k)!r}")
            rx = rec.get("received_monotonic_ns")
            if isinstance(rx, int) and rec.get("clock_domain") == "host_monotonic":
                if last_rx is not None and rx < last_rx:
                    errs.append(f"{where}: received_monotonic_ns goes backwards ({rx} < {last_rx})")
                last_rx = rx
            if (rec.get("stream"), rec.get("record_type")) == ("command", "set"):
                # set rows have no received time; their own two clocks must not run backwards
                sch, snt = rec.get("scheduled_monotonic_ns"), rec.get("sent_monotonic_ns")
                if isinstance(sch, int) and not isinstance(sch, bool):
                    if last_sched is not None and sch < last_sched:
                        errs.append(f"{where}: scheduled_monotonic_ns goes backwards "
                                    f"({sch} < {last_sched})")
                    last_sched = sch
                if isinstance(snt, int) and not isinstance(snt, bool):
                    if last_sent is not None and snt < last_sent:
                        errs.append(f"{where}: sent_monotonic_ns goes backwards ({snt} < {last_sent})")
                    last_sent = snt
            if (rec.get("stream"), rec.get("record_type")) == ("command", "set") and rec.get("valid"):
                if rec.get("sent_monotonic_ns") is None and header.get("link") != "none":
                    errs.append(f"{where}: a command on a {header.get('link')} link needs "
                                "sent_monotonic_ns (only a link-less dry run may leave it null)")
                ep = rec.get("session_epoch")
                s = rec.get("seq")
                if isinstance(s, int):
                    if ep in last_seq and s <= last_seq[ep]:
                        errs.append(f"{where}: seq {s} not increasing within epoch {ep}")
                    last_seq[ep] = s
            records.append(rec)
    if header is None and not any("first line" in e for e in errs):
        errs.append(f"{path.name}: empty file")
    return header, records, errs


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


@dataclass
class Stream:
    header: dict
    records: list[dict]

    def of(self, record_type: str) -> list[dict]:
        return [r for r in self.records if r["record_type"] == record_type]


def read_stream(path: str | os.PathLike) -> Stream:
    header, records, errs = validate_stream(path)
    if errs:
        raise TrialLogError(errs)
    assert header is not None
    return Stream(header, records)


@dataclass
class Trial:
    path: Path
    streams: dict[str, Stream]

    @property
    def trial_id(self) -> str:
        return next(iter(self.streams.values())).header["trial_id"]

    @property
    def session_id(self) -> str:
        return next(iter(self.streams.values())).header["session_id"]

    @property
    def mode(self) -> str:
        return next(iter(self.streams.values())).header["mode"]

    def stream(self, name: str) -> Stream:
        if name not in self.streams:
            raise KeyError(f"{self.path.name} has no {name} stream")
        return self.streams[name]


def load_trial(path: str | os.PathLike) -> Trial:
    """Load and validate every stream file present in a trial directory."""
    p = Path(path)
    if not p.is_dir():
        raise TrialLogError([f"{p}: not a trial directory"])
    streams: dict[str, Stream] = {}
    errs: list[str] = []
    for name in STREAMS:
        f = p / f"{name}.jsonl"
        if not f.exists():
            if (p / f"{name}.jsonl.part").exists():
                errs.append(f"{name}.jsonl.part: unfinished stream")
            continue
        header, records, e = validate_stream(f)
        errs.extend(e)
        if header is not None:
            streams[name] = Stream(header, records)
    if errs:
        raise TrialLogError(errs)
    if not streams:
        raise TrialLogError([f"{p}: no stream files"])
    ids = {(s.header["session_id"], s.header["trial_id"]) for s in streams.values()}
    if len(ids) != 1:
        raise TrialLogError([f"{p}: streams disagree on session/trial id: {sorted(ids)}"])
    return Trial(p, streams)


def iter_trials(root: str | os.PathLike) -> Iterable[Path]:
    root = Path(root)
    if any((root / f"{s}.jsonl").exists() for s in STREAMS):
        yield root
        return
    for d in sorted(root.iterdir()):
        if d.is_dir() and any((d / f"{s}.jsonl").exists() for s in STREAMS):
            yield d


def schema_dict() -> dict:
    """The schema as data. docs/trial_schema_v1.json is this, and a test keeps them equal."""
    return {
        "schema_version": SCHEMA_VERSION,
        "files": {s: f"<trial_id>/{s}.jsonl" for s in STREAMS},
        "modes": list(MODES), "links": list(LINKS), "sources": list(SOURCES),
        "capture_time_provenance": list(PROVENANCE), "clock_domains": list(CLOCK_DOMAINS),
        "common_fields": list(COMMON_FIELDS),
        "records": {f"{s}/{t}": list(fields) for (s, t), fields in sorted(STREAM_FIELDS.items())},
        "rules": [
            "line 1 of every file is a header record (record_type=header)",
            "capture_time_ns is null unless a device supplied an exposure time; then provenance is "
            "'device' or 'estimated', otherwise 'unknown'",
            "valid=false means no measurement: value columns are null, never 0",
            "commanded_* (host), device_* (firmware report) and observed_* (camera) never share a column",
            "within one session_epoch, command seq strictly increases",
            "a file is published atomically and never appended to or overwritten",
        ],
        "reply_statuses": list(REPLY_STATUSES),
        "source_kinds": list(SOURCE_KINDS),
        "invalid_reasons": [r for r in INVALID_REASONS if r],
    }
