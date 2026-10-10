"""Create a trial directory and its stream writers; map CLI targets to log modes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .devclient import CommandLog
from .triallog import StreamWriter, TrialContext

TARGETS = ("dry", "loopback", "hardware")


def resolve(action: str, target: str, human_input: bool = False) -> tuple[str, str, str]:
    """(mode, link, source) for a CLI action ('record'|'play') and target.

    source: 'hardware' for a serial link; 'recorded' when a person's input is stored
    without any device; otherwise 'synthetic' (virtual device and/or scripted input).
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    link = {"dry": "none", "loopback": "virtual", "hardware": "serial"}[target]
    if action == "play":
        mode = "REPLAY"
    else:
        mode = {"dry": "DRY_RUN", "loopback": "LOOPBACK", "hardware": "HARDWARE_ARMED"}[target]
    if target == "hardware":
        source = "hardware"
    elif target == "dry" and human_input:
        source = "recorded"
    else:
        source = "synthetic"
    return mode, link, source


@dataclass
class OpenTrial:
    ctx: TrialContext
    dir: Path
    input: StreamWriter | None
    command: CommandLog

    def close(self) -> None:
        if self.input:
            self.input.close()
        self.command.w.close()

    def abort(self) -> None:
        if self.input:
            self.input.abort()
        self.command.w.abort()

    def __enter__(self) -> "OpenTrial":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def open_trial(out_root: str | Path, ctx: TrialContext, with_input: bool) -> OpenTrial:
    d = Path(out_root) / ctx.trial_id
    d.mkdir(parents=True, exist_ok=True)
    inp = (StreamWriter(d / "input.jsonl", "input", ctx, units="norm",
                        coordinate_frame="input_axis_0") if with_input else None)
    try:
        cmd = StreamWriter(d / "command.jsonl", "command", ctx, units="pulse_us",
                           coordinate_frame="servo_axis_0")
    except Exception:
        if inp:
            inp.abort()
        raise
    return OpenTrial(ctx, d, inp, CommandLog(cmd))
