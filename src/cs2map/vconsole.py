"""VConsole2 protocol client (TCP 29000), the channel CS2 Workshop Tools open for
the VConsole window.  Lets us send console commands to a running tools session
(whoever started it) and read the printed output.

Packet: 4-byte type ('CMND', 'PRNT', ...), uint32 version (0x00D40000),
uint16 total length (including the 12-byte header), uint16 handle; big-endian.

Output is framed with unique `echo` markers: the console runs commands in order,
so everything our commands print lies between the begin and end markers.  This
needs no "wait for quiet" heuristics, ignores the backlog CS2 sends on connect
and stays fast while bots or scripts print continuously.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
import uuid
from contextlib import contextmanager

PORT = 29000
VERSION = 0x00D40000
# CS2 serves one console client at a time and refuses the rest, so a process must not
# keep the connection between tool calls: it is closed after this many idle seconds
# (never while a listener is attached, e.g. during script_test / route_test).
IDLE_CLOSE = 5.0
# 'VFCS' + one byte tells the engine "the VConsole window is in the foreground". The engine then keeps rendering,
# finishes map loads and runs its client (setang, +forward) although another program has the focus
# (engine2: r_vconsole_foregroundforcerender, engine_no_focus_sleep_vconsole_suppress; both on by default).
# Without it a CS2 that is not in front shows its last frame for ever and a map load stops at the loading screen
# (measured 09.11, also with engine_no_focus_sleep 0). The state lasts as long as the socket: every new
# connection says it again. The user's keyboard and mouse still reach only the program in front.
AWAKE = True


class VConsoleError(RuntimeError):
    pass


def _packet(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack('>IHH', VERSION, 12 + len(payload), 0) + payload


def _text(payload: bytes) -> list[str]:
    text = payload[28:].split(b'\0')[0].decode('utf-8', 'replace').rstrip()
    return [ln for ln in text.splitlines() if ln.strip()] if text else []


def _prints(data: bytes) -> list[str]:
    """Extract text of PRNT packets from a byte stream (skips packets with other framing)."""
    reader = _Reader()
    return reader.feed(data)


def _stamp(payload: bytes) -> int:
    """Milliseconds since the game started at which CS2 printed the line (the 5th header word, verified 03.11)."""
    return struct.unpack('>I', payload[16:20])[0] if len(payload) >= 20 else 0


# Callables tap(conn, [(ms, line), ...]) that see every printed line of every connection, the backlog CS2
# replays on connect included (marks.py reads player marks from it). They run on the reader thread: keep them fast.
TAPS: list = []


class _Reader:
    """Incremental PRNT parser: each byte is scanned once, partial packets wait for more data.
    With `stamped` set, (ms, line) pairs are also collected there for TAPS."""

    def __init__(self, stamped: list | None = None):
        self.buf = b''
        self.pos = 0
        self.stamped = stamped

    def feed(self, chunk: bytes) -> list[str]:
        self.buf += chunk
        out: list[str] = []
        while True:
            i = self.buf.find(b'PRNT', self.pos)
            if i < 0:
                self.pos = max(self.pos, len(self.buf) - 3)
                break
            if i + 12 > len(self.buf):
                self.pos = i
                break
            ver, length, _ = struct.unpack('>IHH', self.buf[i + 4:i + 12])
            if ver != VERSION or length < 40:
                self.pos = i + 4
                continue
            if i + length > len(self.buf):
                self.pos = i
                break
            payload = self.buf[i + 12:i + length]
            text = _text(payload)
            out.extend(text)
            if self.stamped is not None and text:
                ms = _stamp(payload)
                self.stamped.extend((ms, ln) for ln in text)
            self.pos = i + length
        if self.pos > (1 << 20):
            self.buf, self.pos = self.buf[self.pos:], 0
        return out


def available(port: int = PORT) -> bool:
    if _CONN is not None and _CONN.alive and _CONN.port == port:
        return True
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return bool(holders(port))


def holders(port: int = PORT) -> list[str]:
    """Other local processes connected to the console port ("pid name: command line")."""
    try:
        import psutil
        out = []
        for c in psutil.net_connections(kind='tcp'):
            if (c.raddr and c.raddr.port == port and c.status == psutil.CONN_ESTABLISHED
                    and c.pid and c.pid != os.getpid()):
                try:
                    proc = psutil.Process(c.pid)
                    out.append(f'{c.pid} {proc.name()}: {" ".join(proc.cmdline())[:160]}')
                except psutil.Error:
                    out.append(str(c.pid))
        return out
    except Exception:
        return []


# Valve's own VConsole2 window, opened by CS2 itself (the console key in Workshop Tools)
# silently takes the only console slot. User decision 05.10: close it and go on.
# Only a polite WM_CLOSE (the window's X button): CS2 hung once right after the
# process was terminated, so a window that does not close is reported, not killed.
EVICTED: list[str] = []


def evict_vconsole(port: int = PORT) -> list[str]:
    """Close VConsole2 windows (children of cs2.exe) connected to the console port; return what closed."""
    closed = []
    try:
        import psutil
        from . import system
        for c in psutil.net_connections(kind='tcp'):
            if not (c.raddr and c.raddr.port == port and c.status == psutil.CONN_ESTABLISHED and c.pid):
                continue
            try:
                proc = psutil.Process(c.pid)
                parent = proc.parent()
                if proc.name().lower() == 'vconsole2.exe' and parent and parent.name().lower() == 'cs2.exe':
                    if system.close_windows(c.pid):
                        proc.wait(5)
                        closed.append(f'{c.pid} vconsole2.exe window (opened by cs2.exe {parent.pid})')
            except psutil.Error:
                continue
    except Exception:
        return closed
    if closed:
        EVICTED.extend(closed)
        time.sleep(0.5)           # CS2 frees the slot a moment after the window exits
    return closed


class _Connection:
    """One persistent VConsole connection per process with a background reader.

    CS2 sends a large backlog (several MB) to every new client, which costs ~2 s;
    reusing the connection makes each later command a round trip of one frame."""

    def __init__(self, port: int):
        # CS2 accepts a second TCP client but never answers it: free the slot first.
        evict_vconsole(port)
        try:
            self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        except OSError as e:
            held = holders(port)
            if held:
                raise VConsoleError(f'CS2 console (VConsole port {port}) is busy: CS2 accepts one console '
                                    f'client and it is held by {"; ".join(held)}. That process must '
                                    'disconnect or exit (a stale MCP server from another chat).') from None
            raise VConsoleError(f'CS2 console (VConsole port {port}) is not reachable: {e}. '
                                'Is CS2 running in Workshop Tools mode?') from None
        self.sock.settimeout(0.5)
        self.port = port
        self.alive = True
        self.listeners: list[list[str]] = []
        self.cv = threading.Condition()
        self.thread = threading.Thread(target=self._read, name='vconsole-reader', daemon=True)
        self.thread.start()
        if AWAKE:
            self.wake(True)

    def wake(self, on: bool) -> None:
        """Tell the engine the console window is (not) in the foreground: see AWAKE."""
        try:
            self.sock.sendall(_packet(b'VFCS', b'\x01' if on else b'\x00'))
        except OSError:
            self.alive = False

    def _read(self) -> None:
        stamped: list = []
        reader = _Reader(stamped)
        try:
            while self.alive:
                try:
                    chunk = self.sock.recv(1 << 20)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                lines = reader.feed(chunk)
                if stamped:
                    batch, stamped[:] = list(stamped), []
                    for tap in list(TAPS):
                        try:
                            tap(self, batch)
                        except Exception:          # a tap must never break the console
                            pass
                if lines and self.listeners:
                    with self.cv:
                        for listener in self.listeners:
                            listener.extend(lines)
                        self.cv.notify_all()
        finally:
            self.alive = False
            with self.cv:
                self.cv.notify_all()

    def send(self, command: str) -> None:
        try:
            self.sock.sendall(_packet(b'CMND', command.encode('utf-8') + bytes(1)))
        except OSError as e:
            self.alive = False
            raise VConsoleError(f'CS2 closed the console connection (did the game crash?): {e}') from None

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


_CONN: _Connection | None = None
_CONN_LOCK = threading.Lock()
_RUN_LOCK = threading.RLock()
_LAST_USE = 0.0
_ACTIVE = 0
_HOLDS = 0
_REAPER: threading.Thread | None = None


def _connection(port: int) -> _Connection:
    """The shared connection (opened on demand). Holding it with a listener attached keeps it open."""
    global _CONN, _LAST_USE, _REAPER
    with _CONN_LOCK:
        if _CONN is None or not _CONN.alive or _CONN.port != port:
            if _CONN is not None:
                _CONN.close()
            _CONN = _Connection(port)
        _LAST_USE = time.monotonic()
        if _REAPER is None or not _REAPER.is_alive():
            _REAPER = threading.Thread(target=_reap, name='vconsole-idle', daemon=True)
            _REAPER.start()
        return _CONN


def _reap() -> None:
    global _CONN
    while True:
        time.sleep(0.5)
        with _CONN_LOCK:
            conn = _CONN
            if conn is None or not conn.alive:
                _CONN = None
                return
            if _ACTIVE or _HOLDS or conn.listeners or time.monotonic() - _LAST_USE < IDLE_CLOSE:
                continue
            conn.close()
            _CONN = None
            return


def release() -> None:
    """Close the connection now unless a session, hold or listener still uses it (frees CS2 for other processes)."""
    global _CONN
    with _CONN_LOCK:
        if _CONN is not None and not _ACTIVE and not _HOLDS and not _CONN.listeners:
            _CONN.close()
            _CONN = None


@contextmanager
def hold(port: int = PORT, connect: bool = True):
    """Keep the console connection open for the block, so CS2 stays awake (AWAKE) between the commands of one
    operation: frames after a wait, a test that only listens. A connection that drops inside the block is opened
    again by the next command and says "foreground" again. Without a running CS2 the block still runs.
    connect=False opens nothing by itself: the connection is kept from the block's first command on."""
    global _HOLDS
    with _CONN_LOCK:
        _HOLDS += 1
    try:
        if connect:
            try:
                _connection(port)
            except VConsoleError:
                pass                # the commands of the block report it in their own words
        yield
    finally:
        with _CONN_LOCK:
            _HOLDS -= 1


def _session(commands: list[str], until, timeout: float, linger: float = 0.0,
             port: int = PORT) -> tuple[bool, list[str]]:
    """Send begin marker, commands, end marker; collect lines after the begin marker.

    `until(lines, ended)` decides when the wanted output is complete. After that, lines
    keep being collected for `linger` seconds (output printed on later frames)."""
    global _ACTIVE, _LAST_USE
    with _RUN_LOCK:
        _ACTIVE += 1
        try:
            return _session_once(commands, until, timeout, linger, port)
        finally:
            _ACTIVE -= 1
            _LAST_USE = time.monotonic()


def _session_once(commands, until, timeout, linger, port) -> tuple[bool, list[str]]:
    for attempt in range(2):
        conn = _connection(port)
        listener: list[str] = []
        with conn.cv:
            conn.listeners.append(listener)
        try:
            token = uuid.uuid4().hex[:12]
            begin, end = f'mcp_begin_{token}', f'mcp_end_{token}'
            try:
                conn.send(f'echo {begin}')
            except VConsoleError:
                if attempt == 0:
                    continue          # stale connection (CS2 restarted): reconnect once
                raise
            for c in commands:
                conn.send(c)
            conn.send(f'echo {end}')
            ok, lines, started = _collect(conn, listener, begin, end, until, timeout, linger)
            if not started:
                held = holders(port)
                if held:
                    # Our socket is connected, yet CS2 talks to the other client only.
                    conn.close()
                    if attempt == 0 and evict_vconsole(port):
                        continue
                    raise VConsoleError(f'CS2 console is held by {"; ".join(held)}: CS2 answers one console '
                                        'client only. Close that program (a stale MCP server of another chat).')
            return ok, lines
        finally:
            with conn.cv:
                # by identity: two listeners that saw the same lines compare equal as lists
                conn.listeners[:] = [x for x in conn.listeners if x is not listener]
    raise VConsoleError('CS2 console connection failed')


def _collect(conn: _Connection, listener: list[str], begin: str, end: str, until,
             timeout: float, linger: float) -> tuple[bool, list[str]]:
    pos, started, ended, lines = 0, False, False, []
    done_at = None
    deadline = time.monotonic() + timeout
    while True:
        with conn.cv:
            while pos >= len(listener) and conn.alive:
                limit = deadline if done_at is None else min(deadline, done_at)
                remaining = limit - time.monotonic()
                if remaining <= 0:
                    break
                conn.cv.wait(remaining)
            new, pos = listener[pos:], len(listener)
        for ln in new:
            text = ln.strip()
            if not started:
                started = text.endswith(begin) and 'echo' not in text
                continue
            if text.endswith(end) and 'echo' not in text:
                ended = True
                continue
            if begin in text or end in text:
                continue
            lines.append(ln)
        now = time.monotonic()
        if done_at is None and until(lines, ended):
            done_at = now + linger
        if done_at is not None and now >= done_at:
            return True, lines, started
        if now >= deadline:
            return done_at is not None, lines, started
        if not conn.alive and not new:
            if done_at is not None:
                return True, lines, started
            raise VConsoleError('CS2 closed the console connection (did the game crash?)')


def run_until(commands: list[str], pattern: str | tuple[str, ...], timeout: float = 90,
              port: int = PORT) -> tuple[bool, list[str]]:
    """Send commands, then read output until a line containing `pattern` (or any of several)
    appears, or the timeout passes. Server-side commands (status, script output) may print
    a frame after the client-side end marker, so wait for their own text, not the marker."""
    patterns = (pattern,) if isinstance(pattern, str) else tuple(pattern)
    return _session(commands, lambda lines, ended: any(p in ln for ln in lines for p in patterns),
                    timeout, port=port)


def run_when(commands: list[str], done, timeout: float = 5.0, port: int = PORT) -> tuple[bool, list[str]]:
    """Send commands, then read output until done(lines) says the answer is complete (an answer of several lines
    may arrive in pieces), or the timeout passes."""
    return _session(commands, lambda lines, ended: bool(done(lines)), timeout, port=port)


def run_ended(commands: list[str], wait: float = 1.5, port: int = PORT) -> tuple[bool, list[str]]:
    """run() plus whether the console answered at all (the end marker came back within 20 s + wait)."""
    return _session(commands, lambda lines, ended: ended, timeout=20 + wait, linger=max(0.0, wait), port=port)


def run(commands: list[str], wait: float = 1.5, port: int = PORT) -> list[str]:
    """Send commands (in order) and return what they printed.

    Synchronous output is complete when the end marker arrives; `wait` seconds after
    that are added for output printed on later frames (bounded, unlike quiet-waiting).
    While a map loads the console may stall; then the partial output is returned after
    the timeout, as callers (e.g. waiting for a player) simply poll again."""
    _, lines = _session(commands, lambda lines, ended: ended, timeout=20 + wait,
                        linger=max(0.0, wait), port=port)
    return lines
