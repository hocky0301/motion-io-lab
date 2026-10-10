"""Human input sources. Each one yields InputEvent(value in [-1, 1]) with the host
receive time; the browser source also carries the browser's own clock reading
(``performance.now()``), stored separately because the two clocks are not the same.

* ScriptedSource    a motion function plus a little hand tremor (tests / synthetic demo)
* KeyboardSource    raw terminal: a/d or left/right arrows step the axis, space = centre, q = quit
* TouchPadServer    stdlib HTTP server with a touch pad page for a phone browser.
                    127.0.0.1 by default; ``lan=True`` binds all interfaces. A random token is
                    required on every request in both cases.
"""
from __future__ import annotations

import hmac
import json
import math
import queue
import random
import secrets
import select
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse


@dataclass
class InputEvent:
    rx_ns: int                         # host monotonic receive time
    value: float | None                # normalised axis in [-1, 1]; None = invalid sample
    kind: str                          # scripted / keyboard / touch
    client_time_ms: float | None = None  # browser performance.now(); None for local sources
    event_id: int = 0


class InputSource:
    kind = "abstract"
    client_clock_domain: str | None = None

    def start(self, now_ns: int) -> None: ...

    def poll(self, now_ns: int) -> list[InputEvent]:
        raise NotImplementedError

    def done(self, now_ns: int) -> bool:
        return False

    def sync_records(self) -> list[dict]:
        return []

    def close(self) -> None: ...


class ScriptedSource(InputSource):
    kind = "scripted"

    def __init__(self, fn: Callable[[float], float], duration_s: float, rate_hz: float = 100.0,
                 tremor_sigma: float = 0.01, seed: int = 0):
        self.fn, self.duration_s, self.rate_hz = fn, duration_s, rate_hz
        self.sigma = tremor_sigma
        self.rng = random.Random(seed)
        self._t0: int | None = None
        self._next = 0
        self._n = int(round(duration_s * rate_hz)) + 1

    def start(self, now_ns: int) -> None:
        self._t0 = now_ns

    def poll(self, now_ns: int) -> list[InputEvent]:
        assert self._t0 is not None, "start() first"
        out = []
        while self._next < self._n:
            t_rel = self._next / self.rate_hz
            rx = self._t0 + int(t_rel * 1e9)
            if rx > now_ns:
                break
            x = self.fn(t_rel) + self.rng.gauss(0.0, self.sigma)
            out.append(InputEvent(rx, max(-1.0, min(1.0, x)), self.kind, None, self._next))
            self._next += 1
        return out

    def done(self, now_ns: int) -> bool:
        return self._next >= self._n


def _escape_sequence(buf: str, i: int) -> tuple[int, str | None]:
    """Length of the escape sequence that starts at ``buf[i]`` (an ESC) and its final character.

    ``(0, None)`` means the sequence is not complete yet. Plain arrows are ``ESC [ D`` / ``ESC [ C``
    (or ``ESC O D`` / ``ESC O C``); anything with parameters (``ESC [ 1 ; 2 D``) has the same
    final letter but is not a plain arrow, so it reports ``final=None``.
    """
    rest = buf[i + 1:]
    if not rest:
        return 0, None
    if rest[0] == "[":
        j = 1
        while j < len(rest) and not (0x40 <= ord(rest[j]) <= 0x7E):
            j += 1
        if j >= len(rest):
            return 0, None
        n = j + 2                                  # ESC + '[' + params + final
        return n, (rest[j] if j == 1 else None)
    if rest[0] == "O":
        if len(rest) < 2:
            return 0, None
        return 3, rest[1]
    return 1, None                                 # ESC followed by something else: drop the ESC


class KeyboardSource(InputSource):
    """Raw-mode keyboard. Use as a context manager so the terminal is always restored."""

    kind = "keyboard"

    def __init__(self, step: float = 0.05, stream=None):
        self.stream = stream or sys.stdin
        if not self.stream.isatty():
            raise RuntimeError("keyboard input needs an interactive terminal")
        self.step = step
        self.x = 0.0
        self.quit = False
        self._n = 0
        self._saved = None
        self._buf = ""

    def __enter__(self) -> "KeyboardSource":
        import termios
        import tty

        fd = self.stream.fileno()
        self._saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._saved is not None:
            import termios

            termios.tcsetattr(self.stream.fileno(), termios.TCSADRAIN, self._saved)
            self._saved = None

    def poll(self, now_ns: int) -> list[InputEvent]:
        import os

        out = []
        fd = self.stream.fileno()
        while select.select([fd], [], [], 0)[0]:
            data = os.read(fd, 64)
            if not data:
                break
            self._buf += data.decode("ascii", errors="ignore")
        buf, self._buf = self._buf, ""
        i = 0
        while i < len(buf):
            ch = buf[i]
            move = None
            if ch == "\x1b":
                n, final = _escape_sequence(buf, i)
                if n == 0:                       # an unfinished sequence: wait for the rest
                    self._buf = buf[i:]
                    break
                i += n - 1
                if final == "D":
                    move = -self.step
                elif final == "C":
                    move = self.step
                elif n == 1:                     # a lone ESC: no key follows it
                    pass
                # any other sequence (up/down, Shift+arrow, Home, Delete, ...) is dropped whole,
                # so a letter inside it is never read as an a/d key
            elif ch in "aA":
                move = -self.step
            elif ch in "dD":
                move = self.step
            elif ch in " sS":
                self.x = 0.0
                move = 0.0
            elif ch in "qQ":
                self.quit = True
            i += 1
            if move is not None:
                self.x = max(-1.0, min(1.0, self.x + move))
                out.append(InputEvent(time.monotonic_ns(), self.x, self.kind, None, self._n))
                self._n += 1
        return out

    def done(self, now_ns: int) -> bool:
        return self.quit


class JoyConStdinSource(InputSource):
    """Reads the JSON lines printed by examples/joycon_probe.swift on a pipe.

        ./joycon_probe --seconds 60 | python -m motion_io_lab record --source joycon:"Left Thumbstick" ...

    One element of the controller (named as the probe reports it) becomes the axis. The probe
    prints the host time in the same clock as Python's time.monotonic_ns() (checked on macOS
    with ``--clock-check``); it is stored as ``client_time_ms`` next to the receive time here.
    Nothing here has been run with a real Joy-Con yet; the tests feed it synthetic lines.
    """

    kind = "joycon"
    client_clock_domain = "host_monotonic"

    def __init__(self, element: str, stream=None, field: str = "x"):
        self.element, self.field = element, field
        self.stream = stream or sys.stdin
        self._q: queue.Queue = queue.Queue()
        self._eof = False
        self._n = 0
        self.skipped = 0
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()

    def _read(self) -> None:
        try:
            for line in self.stream:
                rx = time.monotonic_ns()
                self._q.put((rx, line))
        finally:
            self._eof = True

    def poll(self, now_ns: int) -> list[InputEvent]:
        out = []
        while True:
            try:
                rx, line = self._q.get_nowait()
            except queue.Empty:
                return out
            try:
                rec = json.loads(line, parse_constant=_no_const)
            except ValueError:
                self.skipped += 1
                continue
            if not isinstance(rec, dict) or rec.get("event") != "input" or rec.get("element") != self.element:
                continue
            raw = rec.get(self.field if rec.get("kind") == "dpad" else "value")
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                self.skipped += 1
                continue
            host = rec.get("host_monotonic_ns")
            out.append(InputEvent(rx, max(-1.0, min(1.0, float(raw))), self.kind,
                                  host / 1e6 if isinstance(host, int) else None, self._n))
            self._n += 1

    def done(self, now_ns: int) -> bool:
        return self._eof and self._q.empty()


# ---------------------------------------------------------------------------
# touch pad server

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>motion-io-lab touch pad</title>
<style>
html,body{margin:0;height:100%;background:#111;color:#ddd;font:16px system-ui;touch-action:none}
#pad{position:fixed;inset:64px 0 0 0;background:#1d2733}
#bar{position:fixed;left:0;right:0;top:0;height:64px;padding:8px 16px;box-sizing:border-box}
#dot{position:absolute;top:50%;width:28px;height:28px;margin:-14px 0 0 -14px;border-radius:50%;background:#3ddc84;left:50%}
</style></head><body>
<div id="bar">Slide left/right. <span id="st">syncing clock...</span></div>
<div id="pad"><div id="dot"></div></div>
<script>
const token=new URLSearchParams(location.search).get('token')||'';
const q=p=>p+(p.includes('?')?'&':'?')+'token='+encodeURIComponent(token);
let n=0,buf=[],lastX=0;
async function sync(){
  let best=null;
  for(let i=0;i<8;i++){
    const t0=performance.now();
    const r=await fetch(q('/ping'),{cache:'no-store'}); const j=await r.json();
    const t1=performance.now(); const rtt=t1-t0;
    const off=Number(j.host_ns)/1e6-(t0+t1)/2;
    if(!best||rtt<best.rtt_ms)best={rtt_ms:rtt,client_offset_ms:off};
  }
  best.samples=8;
  await fetch(q('/sync'),{method:'POST',body:JSON.stringify(best)});
  document.getElementById('st').textContent='clock synced (rtt '+best.rtt_ms.toFixed(1)+' ms)';
}
function push(x){
  lastX=x; document.getElementById('dot').style.left=((x+1)/2*100)+'%';
  buf.push({id:n++,t:performance.now(),x:x});
}
function pos(e){const t=e.touches?e.touches[0]:e;const w=window.innerWidth;
  return Math.max(-1,Math.min(1,(t.clientX/w)*2-1));}
const pad=document.getElementById('pad');
['touchstart','touchmove','mousemove'].forEach(ev=>pad.addEventListener(ev,e=>{
  if(ev==='mousemove'&&e.buttons===0)return; e.preventDefault(); push(pos(e));},{passive:false}));
setInterval(()=>{ if(!buf.length)return; const b=buf; buf=[];
  fetch(q('/input'),{method:'POST',body:JSON.stringify({events:b}),keepalive:true}).catch(()=>{});},16);
sync();
</script></body></html>"""

MAX_BODY = 64 * 1024
MAX_EVENTS = 500


def bind_address(lan: bool) -> str:
    return "0.0.0.0" if lan else "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    server_version = "motion-io-lab"

    def log_message(self, *a) -> None:  # keep the terminal quiet
        pass

    def _deny(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self, n: int, cap: int = 16 * 1024 * 1024) -> None:
        left = min(n, cap)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        if n > cap:
            self.close_connection = True

    def _auth(self) -> bool:
        tok = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if not hmac.compare_digest(tok.encode(), self.server.token.encode()):  # type: ignore[attr-defined]
            self._deny(403, "bad token")
            return False
        return True

    def do_GET(self) -> None:
        if not self._auth():
            return
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/ping":
            body = json.dumps({"host_ns": str(time.monotonic_ns())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._deny(404, "not found")

    def do_POST(self) -> None:
        if not self._auth():
            return
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._deny(400, "bad length")
        if n <= 0 or n > MAX_BODY:
            if n > MAX_BODY:
                self._drain(n)       # read the body first, or the client sees a reset, not a 413
            return self._deny(413 if n > MAX_BODY else 400, "body size")
        try:
            data = json.loads(self.rfile.read(n), parse_constant=_no_const)
        except (ValueError, json.JSONDecodeError):
            return self._deny(400, "bad json")
        if path == "/input":
            evs = data.get("events") if isinstance(data, dict) else None
            if not isinstance(evs, list) or len(evs) > MAX_EVENTS:
                return self._deny(400, "events")
            parsed = []
            for e in evs:
                try:
                    x, t, i = float(e["x"]), float(e["t"]), int(e["id"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    return self._deny(400, "event fields")
                if not (math.isfinite(x) and math.isfinite(t)) or not -1.0 <= x <= 1.0:
                    return self._deny(400, "event range")
                if parsed and (i <= parsed[-1].event_id or t < parsed[-1].client_time_ms):
                    return self._deny(400, "event order")      # ids rise and the clock does not run back
                parsed.append(InputEvent(0, x, "touch", t, i))
            self.server.push("event", parsed)  # type: ignore[attr-defined]
            self._ok()
        elif path == "/sync":
            try:
                rec = {"client_offset_ms": float(data["client_offset_ms"]),
                       "rtt_ms": float(data["rtt_ms"]), "samples": int(data["samples"]),
                       "received_monotonic_ns": 0}
            except (KeyError, TypeError, ValueError, OverflowError):
                return self._deny(400, "sync fields")
            if not all(math.isfinite(rec[k]) for k in ("client_offset_ms", "rtt_ms")):
                return self._deny(400, "sync range")
            self.server.push("sync", rec)  # type: ignore[attr-defined]
            self._ok()
        else:
            self._deny(404, "not found")

    def _ok(self) -> None:
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(body)


def _no_const(name: str):
    raise ValueError(name)


class _Hub(ThreadingHTTPServer):
    """HTTP server that keeps one ordered inbox. The host receive time is stamped under the lock,
    when the request has been read and parsed, so the inbox is strictly in receive-time order."""

    token: str

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lock = threading.Lock()
        self.inbox: list = []

    def push(self, kind: str, obj) -> None:
        with self.lock:
            rx = time.monotonic_ns()
            if kind == "event":
                for e in obj:
                    e.rx_ns = rx
            else:
                obj["received_monotonic_ns"] = rx
            self.inbox.append((kind, obj))

    def drain(self) -> tuple[list, list]:
        with self.lock:
            items, self.inbox = self.inbox, []
        events, sync = [], []
        for kind, obj in items:
            (events.extend(obj) if kind == "event" else sync.append(obj))
        return events, sync


class TouchPadServer(InputSource):
    kind = "touch"
    client_clock_domain = "browser_performance"

    def __init__(self, port: int = 8765, lan: bool = False, token: str | None = None):
        self.lan = lan
        self.token = token or secrets.token_urlsafe(16)
        self.httpd = _Hub((bind_address(lan), port), _Handler)
        self.httpd.token = self.token
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self._stop = False
        self._sync_out: list[dict] = []

    def url(self, host: str | None = None) -> str:
        h = host or ("<this-computer-LAN-address>" if self.lan else "127.0.0.1")
        return f"http://{h}:{self.port}/?token={self.token}"

    def poll(self, now_ns: int) -> list[InputEvent]:
        """Events and sync records are drained together, so a later poll never holds an item that
        was received before something the earlier poll already returned."""
        events, sync = self.httpd.drain()
        self._sync_out.extend(sync)
        return events

    def sync_records(self) -> list[dict]:
        out, self._sync_out = self._sync_out, []
        return out

    def stop(self) -> None:
        self._stop = True

    def done(self, now_ns: int) -> bool:
        return self._stop

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=2)
