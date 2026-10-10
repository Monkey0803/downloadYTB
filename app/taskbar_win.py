"""Windows 任务栏进度（ITaskbarList3），仅用 ctypes，不引入新依赖。

为什么手写 COM：仓库现有依赖里没有 pywin32 / comtypes，而 ITaskbarList3
的 vtable 顺序是固定 ABI，直接用 ctypes 拼出来比新增依赖更可控。

诚实说明：本文件在 macOS 上开发，**没有在真实 Windows 上运行验证过**。
因此所有逻辑都通过 ``_load_api()`` 这一层可注入的接口表达，单元测试用假件
覆盖「百分比换算、状态迁移、HWND 上溯、失败静默」等逻辑；真正调用 COM 的
那部分（_WinApi）只能靠代码审查，这一点必须如实告知使用者。
"""

import sys

# COM 常量
CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_TASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEA84}"

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2

# ITaskbarList3::SetProgressState
TBPF_NOPROGRESS = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_NORMAL = 0x2
TBPF_ERROR = 0x4
TBPF_PAUSED = 0x8

# SetProgressValue 把进度按 0..total 归一，取 1000 作为分辨率足够平滑
PROGRESS_SCALE = 1000

# FlashWindowEx
FLASHW_ALL = 0x3
FLASHW_TIMERNOFG = 0xC
GWL_STYLE = -16
WS_CHILD = 0x40000000

# ITaskbarList3 vtable 索引（IUnknown 3 + ITaskbarList 5 + ITaskbarList2 1）
_VTBL_HRINIT = 3
_VTBL_SET_PROGRESS_VALUE = 9
_VTBL_SET_PROGRESS_STATE = 10


class _WinApi:
    """真实 COM 调用层。任何失败都应抛出，由上层静默处理。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._ole32 = ctypes.oledll.ole32
        self._user32 = ctypes.windll.user32

        self._ole32.CoInitialize(None)
        guid = self._guid_from_string
        clsid = guid(CLSID_TASKBAR_LIST)
        iid = guid(IID_TASKBAR_LIST3)
        punk = ctypes.c_void_p()
        self._ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(iid), ctypes.byref(punk))
        if not punk.value:
            raise OSError("CoCreateInstance 未返回 ITaskbarList3")
        self._punk = punk
        vtable = ctypes.cast(
            punk, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        self._hr_init = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p)(vtable[_VTBL_HRINIT])
        self._set_value = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
            ctypes.c_ulonglong, ctypes.c_ulonglong)(
                vtable[_VTBL_SET_PROGRESS_VALUE])
        self._set_state = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(
                vtable[_VTBL_SET_PROGRESS_STATE])
        self._hr_init(punk)   # HrInit：必须先调用

    def _guid_from_string(self, text):
        ctypes = self._ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        value = GUID()
        self._ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
        return value

    # ---- 供上层使用的接口（只做机械调用，策略放在 Backend 里便于测试）----
    def set_state(self, hwnd, state):
        self._set_state(self._punk, hwnd, state)

    def set_value(self, hwnd, completed, total):
        self._set_value(self._punk, hwnd, completed, total)

    def is_foreground(self, hwnd):
        return self._user32.GetForegroundWindow() == hwnd

    def flash(self, hwnd):
        ctypes = self._ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          FLASHW_ALL | FLASHW_TIMERNOFG, 3, 0)
        self._user32.FlashWindowEx(ctypes.byref(info))

    def top_level_hwnd(self, hwnd):
        """把 Tk 的子窗口句柄上溯到任务栏按钮所属的顶层窗口。"""
        ctypes = self._ctypes
        seen = set()
        while hwnd and hwnd not in seen:
            seen.add(hwnd)
            style = self._user32.GetWindowLongW(hwnd, GWL_STYLE)
            if not style & WS_CHILD:
                return hwnd
            parent = self._user32.GetParent(hwnd)
            if not parent or parent == hwnd:
                return hwnd
            hwnd = parent
        return hwnd


def _load_api():
    return _WinApi()


def hwnd_for(window):
    """从 Tk 窗口取顶层 HWND；拿不到就返回 None。"""
    if window is None:
        return None
    try:
        return int(window.winfo_id())
    except Exception:
        return None


class Backend:
    """被 taskbar.py 调用的后端：任何失败都不应影响下载。"""

    def __init__(self, api=None, window=None):
        self._api = api
        self._window = window
        self._hwnd = None
        self._last_permille = None

    def available(self):
        return True

    def _ensure(self):
        if self._api is None:
            self._api = _load_api()
        if self._hwnd is None:
            raw = hwnd_for(self._window)
            if raw is None:
                raise OSError("拿不到窗口句柄")
            self._hwnd = self._api.top_level_hwnd(raw)
        return self._api, self._hwnd

    def set_progress(self, percent):
        clip = max(0.0, min(100.0, float(percent)))
        # 同一千分位不重复打扰任务栏（进度钩子调用非常频繁）
        permille = int(round(clip * 10))
        if permille == self._last_permille:
            return
        self._last_permille = permille
        api, hwnd = self._ensure()
        # 0 表示「没有进度」：用 NOPROGRESS，而不是画一条永远空着的进度条
        if clip <= 0:
            api.set_state(hwnd, TBPF_NOPROGRESS)
            return
        api.set_state(hwnd, TBPF_NORMAL)
        api.set_value(hwnd, int(round(clip * 10)), PROGRESS_SCALE)

    def set_done(self, attention=True):
        self._last_permille = None
        api, hwnd = self._ensure()
        api.set_state(hwnd, TBPF_NOPROGRESS)
        if attention and not api.is_foreground(hwnd):
            api.flash(hwnd)

    def clear(self):
        self._last_permille = None
        api, hwnd = self._ensure()
        api.set_state(hwnd, TBPF_NOPROGRESS)


def enabled():
    return sys.platform.startswith("win")
