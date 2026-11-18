#!/usr/bin/env python3
"""herdr-bgstatus: show a spinner next to Herdr agents that have background work.

Pure script, no inference, standard library only. It reads the visible text of
each agent pane, counts background shells, terminals, tasks, and agents in the
footer of Claude Code, Codex, and OpenCode, and reports pane metadata:

  token $bg       "<count> <spinner>" in front of the Herdr state icon:
                    "2 ⠹ · ✓ 🍋 citrus  42+77"
                  idle rows get blanks of the same width, so nothing moves:
                    "⠀ ⠀ · ✓ 🐝 apiary  👑 lead"
  display-agent   "claude · 2 shells · 1 agent"

Herdr keeps its own colored state icon and labels; only the slot is plain text.
All values have a TTL, so they go away by themselves when this script stops.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

SOURCE = "bgstatus"
TOKEN = "bg"
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BLANK = "\u2800"  # braille blank: Herdr does not trim it, so the row keeps its width
FOOTER_LINES = 8
TTL_MS = 15000          # one TTL for every row: after a crash all rows clear together
REFRESH = 5.0           # seconds between refreshes of unchanged rows
MAX_SCAN_FAILS = 10     # exit after this many failed scans in sequence (server gone)
STOP_TIMEOUT = 30.0     # seconds to wait for the daemon to clear rows and exit

KINDS = ("shell", "terminal", "agent", "task")

# The Claude agent switcher hint ("← 5 agents") and Codex ("← for agents") are not work.
_NOISE = re.compile(r"←\s*(?:\d+\s+)?(?:for\s+)?agents?")
_PATTERNS = (
    ("shell", re.compile(r"\b(\d+)\s+(?:background\s+)?shells?\b")),
    ("terminal", re.compile(r"\b(\d+)\s+background\s+terminals?\b")),
    ("agent", re.compile(r"\b(\d+)\s+(?:background\s+|local\s+)?agents?\s+running\b")),
    ("agent", re.compile(r"\b(\d+)\s+(?:background|local)\s+agents?\b")),
    ("task", re.compile(r"\b(\d+)\s+(?:background\s+)?tasks?\s+running\b")),
    ("task", re.compile(r"\b(\d+)\s+background\s+tasks?\b")),
)


# ---------------------------------------------------------------- pure logic

def parse_footer(text: str, lines: int = FOOTER_LINES) -> dict[str, int]:
    """Count background work in the last non-empty lines of a pane."""
    tail = [ln for ln in (text or "").splitlines() if ln.strip()][-lines:]
    counts: dict[str, int] = {}
    for line in tail:
        line = _NOISE.sub(" ", line)
        for kind, rx in _PATTERNS:
            for m in rx.finditer(line):
                counts[kind] = max(counts.get(kind, 0), int(m.group(1)))
    return {k: v for k, v in counts.items() if v > 0}


def total(counts: dict[str, int]) -> int:
    return sum(counts.values())


def describe(counts: dict[str, int]) -> str:
    parts = []
    for kind in KINDS:
        n = counts.get(kind, 0)
        if n > 0:
            parts.append(f"{n} {kind}{'' if n == 1 else 's'}")
    return " · ".join(parts)


def slot(n: int, frame: int) -> str:
    """'<count> <spinner>' — always 3 cells. Count is 1-9 or '+'; blank when n == 0."""
    if n <= 0:
        return f"{BLANK} {BLANK}"
    return f"{n if n <= 9 else '+'} {FRAMES[frame % len(FRAMES)]}"


@dataclass
class PaneState:
    agent: str
    counts: dict[str, int] = field(default_factory=dict)
    sent: tuple = ()          # last (slot, display) sent
    sent_at: float = -1e9


def build_display(st: PaneState) -> str | None:
    return f"{st.agent} · {describe(st.counts)}" if total(st.counts) else None


def metadata_args(pane: str, st: PaneState, frame: int, ttl: int = TTL_MS) -> list[str]:
    """Build the full `herdr pane report-metadata` argv for one pane."""
    argv = ["pane", "report-metadata", pane, "--source", SOURCE, "--agent", st.agent,
            "--token", f"{TOKEN}={slot(total(st.counts), frame)}"]
    disp = build_display(st)
    argv += ["--display-agent", disp] if disp else ["--clear-display-agent"]
    return argv + ["--ttl-ms", str(ttl)]


def clear_args(pane: str, agent: str) -> list[str]:
    return ["pane", "report-metadata", pane, "--source", SOURCE, "--agent", agent,
            "--clear-token", TOKEN, "--clear-display-agent"]


# ---------------------------------------------------------------- herdr I/O

class Herdr:
    def __init__(self, binary: str | None = None, timeout: float = 10):
        self.binary = binary or os.environ.get("HERDR_BIN_PATH") or "herdr"
        self.timeout = timeout

    def run(self, *args: str) -> str:
        r = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"herdr {args[:2]} failed")
        return r.stdout

    def agents(self) -> list[dict]:
        return json.loads(self.run("agent", "list"))["result"]["agents"]

    def read(self, pane: str) -> str:
        return self.run("pane", "read", pane, "--source", "visible")

    def report(self, argv: list[str]) -> None:
        self.run(*argv)


class Monitor:
    def __init__(self, herdr, only=None, ttl=TTL_MS, refresh=REFRESH,
                 log=None, clock=time.monotonic):
        self.h = herdr
        self.only = set(only or ())
        self.ttl = ttl
        self.refresh = refresh
        self.log = log or (lambda msg: None)
        self.clock = clock
        self.panes: dict[str, PaneState] = {}
        self.frame = 0

    def tick(self, read: bool) -> None:
        """One frame: list agents, read panes if asked, send changed or spinning slots."""
        self.frame += 1
        seen = set()
        for a in self.h.agents():
            pane, agent = a.get("pane_id"), a.get("agent")
            if not pane or not agent or (self.only and pane not in self.only):
                continue
            seen.add(pane)
            st = self.panes.get(pane)
            if st is None or st.agent != agent:
                st = self.panes[pane] = PaneState(agent)
                read_this = True
            else:
                read_this = read
            if read_this:
                try:
                    counts = parse_footer(self.h.read(pane))
                except Exception as e:  # pane closed or unreadable: do not keep old counts
                    self.log(f"read {pane}: {e}")
                    counts = {}
                if total(counts) != total(st.counts):
                    self.log(f"{pane} {agent}: {describe(counts) or 'no background work'}")
                st.counts = counts
            self._maybe_send(pane, st)
        for gone in set(self.panes) - seen:
            del self.panes[gone]

    def _maybe_send(self, pane: str, st: PaneState) -> None:
        cur = (slot(total(st.counts), self.frame), build_display(st))
        now = self.clock()
        if cur == st.sent and now - st.sent_at < self.refresh:
            return
        try:
            self.h.report(metadata_args(pane, st, self.frame, self.ttl))
            st.sent, st.sent_at = cur, now
        except Exception as e:
            self.log(f"report {pane}: {e}")

    def clear_all(self) -> None:
        """Remove the plugin row from every known pane in one pass (stop action)."""
        for pane, st in list(self.panes.items()):
            try:
                self.h.report(clear_args(pane, st.agent))
            except Exception as e:
                self.log(f"clear {pane}: {e}")


# ---------------------------------------------------------------- daemon

def state_dir() -> str:
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "herdr", "plugins", "elderberry.bgstatus")  # same dir Herdr gives the plugin
    os.makedirs(d, exist_ok=True)
    return d


def server_key(extra: str = "") -> str:
    """One daemon per Herdr server: key the lock by socket path (and test filter)."""
    sock = os.environ.get("HERDR_SOCKET_PATH", "default") + extra
    return hashlib.sha256(sock.encode()).hexdigest()[:12]


def pidfile(extra: str = "") -> str:
    return os.path.join(state_dir(), f"bgstatus-{server_key(extra)}.pid")


def acquire_lock(extra: str = "", wait: float = 1.0):
    """Return an open, locked pidfile, or None if another daemon holds it.

    Retries for `wait` seconds, because `running_pid()` holds the lock for a moment.
    The file is never unlinked: a stale file is safe, the lock is the truth.
    """
    f = open(pidfile(extra), "a+")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            time.sleep(0.05)
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def running_pid() -> int | None:
    """PID of the live daemon that holds the lock, else None. Never trusts a stale file."""
    path = pidfile()
    if not os.path.exists(path):
        return None
    with open(path, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # locked: a daemon is alive
            f.seek(0)
            try:
                return int(f.read().strip())
            except ValueError:
                return None
        fcntl.flock(f, fcntl.LOCK_UN)
        return None  # nobody holds the lock: stale file


def cmd_run(args) -> int:
    lock = acquire_lock(",".join(sorted(args.only or ())))
    if lock is None:
        print("another bgstatus daemon runs for this server", file=sys.stderr)
        return 0
    socket = os.environ.get("HERDR_SOCKET_PATH")
    log = (lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)) if not args.quiet else None
    mon = Monitor(Herdr(), only=args.only, log=log)
    stop = []
    old = {sig: signal.signal(sig, lambda *_: stop.append(1)) for sig in (signal.SIGTERM, signal.SIGINT)}
    next_scan, fails = 0.0, 0
    try:
        while not stop:
            if socket and not os.path.exists(socket):
                break  # Herdr server is gone
            t = time.monotonic()
            read = t >= next_scan
            if read:
                next_scan = t + args.scan
            try:
                mon.tick(read)
                fails = 0
            except Exception as e:
                fails += 1
                print("tick failed:", e, file=sys.stderr, flush=True)
                if fails >= MAX_SCAN_FAILS:
                    break
            if args.once:
                break
            time.sleep(args.spin)
        if stop:
            mon.clear_all()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        lock.close()
    return 0


def cmd_start(args) -> int:
    pid = running_pid()
    if pid:
        print(f"already running (pid {pid})")
        return 0
    log = open(os.path.join(state_dir(), "bgstatus.log"), "a")
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", "--quiet"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    print(f"started (pid {p.pid})")
    return 0


def cmd_stop(args) -> int:
    pid = running_pid()
    if not pid:
        print("not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"stopped (pid {pid})")
        return 0
    deadline = time.monotonic() + STOP_TIMEOUT
    while running_pid():
        if time.monotonic() >= deadline:
            print(f"pid {pid} did not stop within {STOP_TIMEOUT:.0f} s", file=sys.stderr)
            return 1
        time.sleep(0.1)
    print(f"stopped (pid {pid})")
    return 0


def cmd_restart(args) -> int:
    if cmd_stop(args) != 0:
        return 1
    return cmd_start(args)


def cmd_status(args) -> int:
    pid = running_pid()
    print(f"running (pid {pid})" if pid else "not running")
    return 0 if pid else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bgstatus", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run in the foreground")
    r.add_argument("--scan", type=float, default=2.0, help="seconds between pane scans")
    r.add_argument("--spin", type=float, default=1.0, help="seconds between spinner frames")
    r.add_argument("--only", action="append", metavar="PANE_ID", help="limit to these panes")
    r.add_argument("--once", action="store_true")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=cmd_run)
    for name, fn in (("start", cmd_start), ("stop", cmd_stop), ("restart", cmd_restart), ("status", cmd_status)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
