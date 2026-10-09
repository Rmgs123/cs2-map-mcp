"""Windows helpers: CS2/Hammer processes, window titles, window screenshots."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsHungAppWindow.argtypes = [wt.HWND]


@dataclass
class Window:
    hwnd: int
    pid: int
    process: str
    title: str
    rect: tuple[int, int, int, int]
    minimized: bool


def windows(process_names: tuple[str, ...] = ('cs2.exe',)) -> list[Window]:
    names = {n.lower() for n in process_names}
    pids: dict[int, str] = {}
    for p in psutil.process_iter(['name']):
        n = (p.info['name'] or '').lower()
        if n in names:
            pids[p.pid] = n
    found: list[Window] = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if buf.value:
            found.append(Window(int(hwnd), pid.value, pids[pid.value], buf.value, (r.left, r.top, r.right, r.bottom),
                                bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def close_windows(pid: int) -> int:
    """Ask every visible window of a process to close (WM_CLOSE, as the X button does); returns the count."""
    count = 0
    for w in windows(('vconsole2.exe',)):
        if w.pid == pid:
            user32.PostMessageW(w.hwnd, 0x0010, 0, 0)     # WM_CLOSE
            count += 1
    return count


def cs2_processes() -> list[dict]:
    out = []
    for p in psutil.process_iter(['name', 'cmdline', 'create_time']):
        if (p.info['name'] or '').lower() != 'cs2.exe':
            continue
        cmd = p.info['cmdline'] or []
        low = [c.lower() for c in cmd]
        mode = 'tools' if '-tools' in low else ('insecure' if '-insecure' in low else 'game')
        addon = cmd[low.index('-addon') + 1] if '-addon' in low and low.index('-addon') + 1 < len(cmd) else None
        out.append({'pid': p.pid, 'mode': mode, 'addon': addon, 'cmdline': ' '.join(cmd[1:])[:300],
                    'started': time.strftime('%H:%M:%S', time.localtime(p.info['create_time']))})
    return out


def hammer_state(map_file: Path) -> dict:
    """Is this map open in Hammer (and does it have unsaved changes)?

    Hammer runs inside cs2.exe (-tools); its window title shows the open map
    and a '*' when there are unsaved edits.
    """
    name = Path(map_file).name.lower()
    stem = Path(map_file).stem.lower()
    hits = []
    for w in windows(('cs2.exe',)):
        t = w.title.lower()
        if 'hammer' in t and (name in t or f'{stem}.vmap' in t or f'/{stem} ' in t or t.endswith(stem)):
            hits.append(w.title)
    return {'open': bool(hits), 'dirty': any('*' in t for t in hits), 'titles': hits}


def focus(hwnd: int) -> bool:
    """Bring a window to the foreground (CS2 only finishes loading / renders properly when focused).
    Uses the Alt-key trick so Windows allows a background process to switch focus."""
    user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)             # Alt down
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.keybd_event(0x12, 0, 2, 0)             # Alt up
    return ok


def is_hung(hwnd: int) -> bool:
    """Windows' own verdict: the window's thread has not pumped messages for ~5 s (\"Not responding\")."""
    return bool(user32.IsWindow(hwnd)) and bool(user32.IsHungAppWindow(hwnd))


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class _Input(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('mi', _MouseInput)]


def mouse_move(dx: int, dy: int, step: int = 20, pause: float = 0.01) -> int:
    """Real relative mouse motion (SendInput MOUSEEVENTF_MOVE) of dx,dy pixels in steps of at most `step`.

    The game window must be in front (it is while a scenario holds focus). Returns the number of events sent."""
    n = max(1, math.ceil(max(abs(dx), abs(dy)) / step))
    sent, done_x, done_y = 0, 0, 0
    for i in range(1, n + 1):
        tx, ty = round(dx * i / n), round(dy * i / n)
        event = _Input(0, _MouseInput(tx - done_x, ty - done_y, 0, 0x0001, 0, None))
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        done_x, done_y, sent = tx, ty, sent + 1
        time.sleep(pause)
    return sent


def click_window(hwnd: int, fx: float, fy: float, pause: float = 0.06) -> tuple[int, int]:
    """A real left click at a point of a window's client area given in fractions (0..1) of its width and height
    (HUD buttons in cursor mode). The cursor is placed with SetCursorPos, then SendInput down/up. Returns the pixel."""
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wt.POINT(int(rect.right * fx), int(rect.bottom * fy))
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    user32.SetCursorPos(point.x, point.y)
    time.sleep(pause)
    for flag in (0x0002, 0x0004):                  # MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        event = _Input(0, _MouseInput(0, 0, 0, flag, 0, None))
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        time.sleep(pause)
    return point.x, point.y


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def capture(hwnd: int, path: Path) -> dict:
    """Save a screenshot of a window (works for DirectX windows via PrintWindow)."""
    from PIL import Image

    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError('window has no client area (minimized?)')
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        ok = user32.PrintWindow(hwnd, mem, 1 | 2)
        if not ok:
            raise RuntimeError('PrintWindow failed')

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG), ('biPlanes', wt.WORD),
                        ('biBitCount', wt.WORD), ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                        ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                        ('biClrImportant', wt.DWORD)]

        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
    extrema = img.getextrema()
    blank = all(lo == hi for lo, hi in extrema)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return {'path': str(path), 'size': [w, h], 'blank': blank}


def capture_screen_region(rect: tuple[int, int, int, int], path: Path) -> dict:
    """Fallback: grab the screen area of a (visible, unobstructed) window."""
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=rect, all_screens=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return {'path': str(path), 'size': list(img.size), 'blank': all(lo == hi for lo, hi in img.getextrema())}
