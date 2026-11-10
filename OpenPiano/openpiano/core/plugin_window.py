"""Puts a plug-in's window where it can be used.

The plug-in host (pedalboard) opens the window without choosing a place for it, so its
contents start at the top-left corner of the screen. Windows then adds the title bar above
the contents, which is above the top edge of the screen: the window cannot be moved,
minimised or closed with the mouse. The host offers no way to ask for another place, so on
Windows the window is looked up once it is on screen and moved from here. It is also given a
title that says which plug-in it belongs to, in place of the host's own name.

Elsewhere this does nothing.
"""
from __future__ import annotations

import sys

# How pedalboard 0.9 names the window it opens, and the window class of the framework it is
# built on. Both are needed to tell that window from the ones a plug-in opens by itself.
HOST_WINDOW_TITLE = "Pedalboard"
HOST_WINDOW_CLASS_PREFIX = "JUCE_"

# The window is on screen by the second poll; this only bounds the search if it never shows.
_MAX_POLLS = 1000

Rect = tuple[int, int, int, int]  # left, top, right, bottom


def position_in_area(frame: Rect, area: Rect) -> tuple[int, int]:
    """Top-left corner that centres a window in an area. A window larger than the area is
    lined up with the area's top-left corner instead, so that its title bar stays in reach."""
    left, top, right, bottom = frame
    area_left, area_top, area_right, area_bottom = area
    spare_width = (area_right - area_left) - (right - left)
    spare_height = (area_bottom - area_top) - (bottom - top)
    return area_left + max(0, spare_width // 2), area_top + max(0, spare_height // 2)


class _Win32Windows:
    """The few Win32 calls needed, with their argument types spelled out for 64-bit handles."""

    _MONITOR_DEFAULTTONEAREST = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._rect_type = wintypes.RECT
        self._monitor_info_type = MONITORINFO
        self._enum_callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        # Private copies of the libraries, so that the types set here stay out of other code.
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.EnumThreadWindows.argtypes = [wintypes.DWORD, self._enum_callback_type, wintypes.LPARAM]
        user32.EnumThreadWindows.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.SetWindowTextW.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        self._user32 = user32
        self._kernel32 = kernel32

    def thread_windows(self) -> list[int]:
        """The top-level windows of the calling thread."""
        handles: list[int] = []

        def collect(handle, _unused) -> bool:
            if handle:
                handles.append(int(handle))
            return True

        callback = self._enum_callback_type(collect)
        self._user32.EnumThreadWindows(self._kernel32.GetCurrentThreadId(), callback, 0)
        return handles

    def exists(self, handle: int) -> bool:
        return bool(handle) and bool(self._user32.IsWindow(handle))

    def is_visible(self, handle: int) -> bool:
        return bool(self._user32.IsWindowVisible(handle))

    def _text(self, reader, handle: int) -> str:
        buffer = self._ctypes.create_unicode_buffer(256)
        reader(handle, buffer, len(buffer))
        return buffer.value

    def class_name(self, handle: int) -> str:
        return self._text(self._user32.GetClassNameW, handle)

    def title(self, handle: int) -> str:
        return self._text(self._user32.GetWindowTextW, handle)

    def set_title(self, handle: int, title: str) -> None:
        self._user32.SetWindowTextW(handle, title)

    def frame(self, handle: int) -> Rect | None:
        """The window with its title bar and borders, in screen coordinates."""
        rect = self._rect_type()
        if not self._user32.GetWindowRect(handle, self._ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def work_area(self, handle: int) -> Rect | None:
        """The part of the window's screen that the taskbar leaves free."""
        monitor = self._user32.MonitorFromWindow(handle, self._MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = self._monitor_info_type()
        info.cbSize = self._ctypes.sizeof(info)
        if not self._user32.GetMonitorInfoW(monitor, self._ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom

    def move(self, handle: int, x: int, y: int) -> None:
        self._user32.SetWindowPos(handle, None, int(x), int(y), 0, 0,
                                  self._SWP_NOSIZE | self._SWP_NOZORDER | self._SWP_NOACTIVATE)


def _system_windows():
    if sys.platform != "win32":
        return None
    try:
        return _Win32Windows()
    except Exception:
        return None


class PluginWindowPlacer:
    """Finds the window the plug-in host has just opened on this thread, centres it on the
    screen of ``anchor_window`` and titles it. Create it right before the window is opened
    and call ``poll`` from the window's loop; it does its work once and then costs nothing.
    """

    def __init__(self, title: str = "", anchor_window: int = 0, *, windows=None) -> None:
        self._title = str(title or "")
        self._anchor = int(anchor_window or 0)
        self._windows = windows if windows is not None else _system_windows()
        self._polls = 0
        self._done = self._windows is None
        self._ignored: set[int] = set()
        if not self._done:
            try:
                # Plug-in windows that are already open belong to someone else.
                self._ignored = set(self._host_windows())
            except Exception:
                self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def _host_windows(self) -> list[int]:
        windows = self._windows
        return [
            handle for handle in windows.thread_windows()
            if windows.is_visible(handle)
            and windows.title(handle) == HOST_WINDOW_TITLE
            and windows.class_name(handle).startswith(HOST_WINDOW_CLASS_PREFIX)
        ]

    def poll(self) -> None:
        if self._done:
            return
        self._polls += 1
        try:
            for handle in self._host_windows():
                if handle not in self._ignored:
                    self._done = True
                    self._place(handle)
                    return
        except Exception:
            # Never worth disturbing the plug-in for: the window stays where the host put it.
            self._done = True
            return
        if self._polls >= _MAX_POLLS:
            self._done = True

    def _place(self, handle: int) -> None:
        windows = self._windows
        frame = windows.frame(handle)
        area = windows.work_area(self._anchor) if windows.exists(self._anchor) else None
        if area is None:
            area = windows.work_area(handle)
        if frame is not None and area is not None:
            x, y = position_in_area(frame, area)
            if (x, y) != (frame[0], frame[1]):
                windows.move(handle, x, y)
        if self._title:
            windows.set_title(handle, self._title)
