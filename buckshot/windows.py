"""Small Windows desktop driver scoped to the Buckshot Roulette game window.

Importing this module does not capture the screen or send input. Coordinates are
normalized to the client area. Input never changes the foreground window unless
the caller explicitly invokes ``focus``.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path


class GameWindowError(RuntimeError):
    """The intended game window cannot safely be read or controlled."""


GAME_EXE = "buckshot roulette.exe"
_game_path: str | None = None
_libraries = None
_VK = {
    "ENTER": 0x0D, "ESCAPE": 0x1B, "ESC": 0x1B, "SPACE": 0x20,
    "TAB": 0x09, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25,
    "RIGHT": 0x27, "E": 0x45, "W": 0x57, "A": 0x41,
    "S": 0x53, "D": 0x44,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}
_GAME_KEYS = frozenset({"ENTER", "ESCAPE", "ESC", "SPACE", "TAB", "UP", "DOWN", "LEFT", "RIGHT", "E", "W", "A", "S", "D"})


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _api():
    global _libraries
    if sys.platform != "win32":
        raise GameWindowError("此插件的游戏窗口控制仅支持 Windows。")
    if _libraries is not None:
        return _libraries
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    signatures = (
        (user, "GetForegroundWindow", [], wintypes.HWND),
        (user, "IsWindow", [wintypes.HWND], wintypes.BOOL),
        (user, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        (user, "IsIconic", [wintypes.HWND], wintypes.BOOL),
        (user, "GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user, "GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user, "ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user, "GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
        (user, "GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user, "SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
        (user, "ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        (user, "SetCursorPos", [ctypes.c_int, ctypes.c_int], wintypes.BOOL),
        (user, "WindowFromPoint", [wintypes.POINT], wintypes.HWND),
        (user, "GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        (user, "SendInput", [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int], wintypes.UINT),
        (user, "GetDC", [wintypes.HWND], wintypes.HDC),
        (user, "ReleaseDC", [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user, "PrintWindow", [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (kernel, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        (kernel, "QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (gdi, "CreateCompatibleDC", [wintypes.HDC], wintypes.HDC),
        (gdi, "CreateCompatibleBitmap", [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        (gdi, "SelectObject", [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        (gdi, "BitBlt", [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD], wintypes.BOOL),
        (gdi, "GetDIBits", [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(_BITMAPINFOHEADER), wintypes.UINT], ctypes.c_int),
        (gdi, "DeleteObject", [wintypes.HANDLE], wintypes.BOOL),
        (gdi, "DeleteDC", [wintypes.HDC], wintypes.BOOL),
    )
    for library, name, arguments, result in signatures:
        fn = getattr(library, name)
        fn.argtypes, fn.restype = arguments, result
    if hasattr(user, "SetThreadDpiAwarenessContext"):
        user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _libraries = user, kernel, gdi
    return _libraries


@contextlib.contextmanager
def _physical_pixels():
    user, _, _ = _api()
    old = None
    if hasattr(user, "SetThreadDpiAwarenessContext"):
        old = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        if old:
            user.SetThreadDpiAwarenessContext(old)


def configure_game_path(path: str | None = None) -> None:
    """Optionally pin discovery to an existing, exact game executable path."""
    global _game_path
    if not path:
        _game_path = None
        return
    candidate = Path(path).expanduser().resolve()
    if candidate.name.casefold() != GAME_EXE or not candidate.is_file():
        raise GameWindowError("游戏路径必须指向已存在的 Buckshot Roulette.exe。")
    _game_path = os.path.normcase(str(candidate))


def _process_path(hwnd: int) -> str:
    user, kernel, _ = _api()
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel.OpenProcess(0x1000, False, pid.value)
    if not process:
        return ""
    try:
        text = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(text))
        if kernel.QueryFullProcessImageNameW(process, 0, text, ctypes.byref(size)):
            return text.value
        return ""
    finally:
        kernel.CloseHandle(process)


def _matches(path: str) -> bool:
    if not path or Path(path).name.casefold() != GAME_EXE:
        return False
    return _game_path is None or os.path.normcase(os.path.abspath(path)) == _game_path


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    user, _, _ = _api()
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user.GetClientRect(hwnd, ctypes.byref(rect)) or not user.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise GameWindowError("无法读取游戏窗口尺寸。")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 100 or height < 100 or width * height > 80_000_000:
        raise GameWindowError("游戏窗口尺寸异常或窗口已最小化。")
    return origin.x, origin.y, width, height


def _assert_game(hwnd: int, foreground: bool = True) -> tuple[int, int, int, int]:
    user, _, _ = _api()
    if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
        raise GameWindowError("无效的游戏窗口句柄。")
    if not user.IsWindow(hwnd) or not _matches(_process_path(hwnd)):
        raise GameWindowError("目标不是已验证的 Buckshot Roulette 游戏进程。")
    if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
        raise GameWindowError("请显示游戏窗口并退出最小化状态。")
    if foreground and user.GetForegroundWindow() != hwnd:
        raise GameWindowError("游戏已失去焦点，操作暂停。请切回游戏后继续。")
    return _rect(hwnd)


def _enum_windows(callback):
    user, _, _ = _api()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    wrapped = callback_type(callback)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.EnumWindows(wrapped, 0)


def _assert_unobscured(hwnd: int, bounds: tuple[int, int, int, int]) -> None:
    """Reject visible top-level windows above the game intersecting its client."""
    user, _, _ = _api()
    x, y, width, height = bounds
    obstruction = []

    def inspect(other, _):
        if other == hwnd:
            return False
        if user.IsWindowVisible(other) and not user.IsIconic(other):
            r = wintypes.RECT()
            if user.GetWindowRect(other, ctypes.byref(r)):
                if max(x, r.left) < min(x + width, r.right) and max(y, r.top) < min(y + height, r.bottom):
                    obstruction.append(int(other))
                    return False
        return True

    _enum_windows(inspect)
    if obstruction:
        raise GameWindowError("游戏画面被其他窗口或桌宠遮挡，请移开遮挡后继续。")


def list_game_windows() -> list[dict]:
    """Discover visible windows by executable identity rather than title."""
    user, _, _ = _api()
    found = []
    with _physical_pixels():
        def inspect(hwnd, _):
            try:
                if not user.IsWindowVisible(hwnd):
                    return True
                path = _process_path(hwnd)
                if not _matches(path):
                    return True
                title = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
                user.GetWindowTextW(hwnd, title, len(title))
                try:
                    _, _, width, height = _rect(hwnd)
                except GameWindowError:
                    width = height = 0
                found.append({"hwnd": int(hwnd), "title": title.value,
                              "process_path": path, "foreground": user.GetForegroundWindow() == hwnd,
                              "minimized": bool(user.IsIconic(hwnd)), "width": width, "height": height})
            except (OSError, ValueError):
                pass
            return True
        _enum_windows(inspect)
    return found


def focus(hwnd: int) -> None:
    """Explicitly bring a verified game window to the foreground."""
    user, _, _ = _api()
    if not user.IsWindow(hwnd) or not _matches(_process_path(hwnd)):
        raise GameWindowError("目标不是已验证的游戏进程。")
    if user.IsIconic(hwnd):
        user.ShowWindow(hwnd, 9)
    user.SetForegroundWindow(hwnd)
    time.sleep(0.12)
    if user.GetForegroundWindow() != hwnd:
        raise GameWindowError("Windows 拒绝切换焦点，请手动切回 Buckshot Roulette。")


def _bitmap_image(hwnd: int, width: int, height: int, render_full: bool):
    """Read a game-owned DC, or ask that game window to render to an offscreen DC."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise GameWindowError(
            "当前 N.E.K.O 宿主缺少可用的 Pillow 图像库，请使用自带 Pillow 的 Windows 桌面版 N.E.K.O 0.9.1 或兼容版本。"
        ) from exc

    user, _, gdi = _api()
    if width < 100 or height < 100 or width * height > 80_000_000:
        raise GameWindowError("截图尺寸异常。")
    source = user.GetDC(hwnd)
    if not source:
        raise GameWindowError("无法获取游戏画面。请尝试窗口化或无边框窗口模式。")
    memory = bitmap = old = None
    try:
        memory = gdi.CreateCompatibleDC(source)
        bitmap = gdi.CreateCompatibleBitmap(source, width, height)
        if not memory or not bitmap:
            raise GameWindowError("无法分配游戏截图缓冲区。")
        old = gdi.SelectObject(memory, bitmap)
        # PW_RENDERFULLCONTENT draws this window without topmost pet/other apps.
        success = (user.PrintWindow(hwnd, memory, 2) if render_full else
                   gdi.BitBlt(memory, 0, 0, width, height, source, 0, 0, 0x00CC0020))
        if not success:
            raise GameWindowError("游戏截图失败，请使用窗口化模式。")
        gdi.SelectObject(memory, old)
        old = None
        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = width, -height
        header.biPlanes, header.biBitCount = 1, 32
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0) != height:
            raise GameWindowError("读取游戏截图失败。")
        return Image.frombuffer("RGB", (width, height), pixels.raw, "raw", "BGRX", 0, 1).copy()
    finally:
        if old and memory:
            gdi.SelectObject(memory, old)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, source)


def _flat_image(image) -> bool:
    return all(low == high for low, high in image.getextrema())


def capture(hwnd: int):
    """Return game-only RGB client pixels, preserving the foreground requirement.

    Prefer a window-owned offscreen render so desktop pets do not contaminate the
    game image. If unsupported by the renderer, use the client DC only while the
    full client area is unobscured, checked both before and after capture.
    """
    user, _, _ = _api()
    with _physical_pixels():
        bounds = _assert_game(hwnd)
        x, y, width, height = bounds
        rect = wintypes.RECT()
        rendered = None
        if user.GetWindowRect(hwnd, ctypes.byref(rect)):
            outer_width, outer_height = rect.right - rect.left, rect.bottom - rect.top
            dx, dy = x - rect.left, y - rect.top
            if 0 <= dx and 0 <= dy and dx + width <= outer_width and dy + height <= outer_height:
                try:
                    outer = _bitmap_image(hwnd, outer_width, outer_height, render_full=True)
                    candidate = outer.crop((dx, dy, dx + width, dy + height))
                    if not _flat_image(candidate):
                        rendered = candidate
                except GameWindowError:
                    pass
        # A changed focus/geometry always discards even a successful render.
        if _assert_game(hwnd) != bounds:
            raise GameWindowError("游戏窗口在截图期间移动或改变尺寸，请重试。")
        if rendered is not None:
            return rendered
        _assert_unobscured(hwnd, bounds)
        result = _bitmap_image(hwnd, width, height, render_full=False)
        if _assert_game(hwnd) != bounds:
            raise GameWindowError("游戏窗口在截图期间移动或改变尺寸，请重试。")
        _assert_unobscured(hwnd, bounds)
        if _flat_image(result):
            raise GameWindowError("游戏返回全黑或空白画面，请使用窗口化或无边框模式。")
        return result


def _send(event: _INPUT):
    user, _, _ = _api()
    if user.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
        raise GameWindowError("Windows 拒绝游戏输入。请确保游戏和 N.E.K.O 的权限等级一致。")


def _assert_modifiers_released():
    user, _, _ = _api()
    if any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
        raise GameWindowError("检测到 Shift/Ctrl/Alt/Windows 键仍按下，已暂停游戏输入。")


def click(hwnd: int, x: float, y: float) -> None:
    """Click at normalized client coordinates; never refocus automatically."""
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise GameWindowError("点击坐标必须是 0 到 1 之间的数字。")
    if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1 and 0 <= y <= 1):
        raise GameWindowError("点击坐标必须位于游戏画面内（0 到 1）。")
    user, _, _ = _api()
    with _physical_pixels():
        bounds = _assert_game(hwnd)
        _assert_modifiers_released()
        left, top, width, height = bounds
        point = wintypes.POINT(left + round(x * (width - 1)), top + round(y * (height - 1)))
        target = user.WindowFromPoint(point)
        if user.GetAncestor(target, 2) != hwnd:
            raise GameWindowError("点击位置被其他窗口遮挡，已取消操作。")
        if not user.SetCursorPos(point.x, point.y):
            raise GameWindowError("无法移动鼠标到游戏画面。")
        if _assert_game(hwnd) != bounds or user.GetAncestor(user.WindowFromPoint(point), 2) != hwnd:
            raise GameWindowError("窗口或焦点已变化，已取消点击。")
        down, up = _INPUT(type=0), _INPUT(type=0)
        down.mi.dwFlags, up.mi.dwFlags = 0x0002, 0x0004
        try:
            _send(down)
        finally:
            _send(up)


def key(hwnd: int, key: str) -> None:
    """Hold a game-navigation key for 80 ms so a rendered frame sees the press."""
    name = str(key).upper()
    if name not in _GAME_KEYS:
        raise GameWindowError("不支持的游戏按键。允许 ENTER/ESCAPE/SPACE/TAB/方向键/WASD/E。")
    with _physical_pixels():
        _assert_game(hwnd)
        _assert_modifiers_released()
        down, up = _INPUT(type=1), _INPUT(type=1)
        down.ki.wVk = up.ki.wVk = _VK[name]
        up.ki.dwFlags = 0x0002
        try:
            _send(down)
            # Godot samples held WASD state per frame. An immediate down/up can
            # vanish between frames. Keep this bounded and always release,
            # including when the caller is interrupted or focus changes.
            time.sleep(.08)
        finally:
            _send(up)


def is_key_down(key: str = "F8") -> bool:
    """Poll a function/game key without installing a keyboard hook."""
    name = str(key).upper()
    if name not in _VK:
        raise GameWindowError("急停按键必须是 F1 到 F12 或受支持的游戏按键。")
    user, _, _ = _api()
    return bool(user.GetAsyncKeyState(_VK[name]) & 0x8000)
