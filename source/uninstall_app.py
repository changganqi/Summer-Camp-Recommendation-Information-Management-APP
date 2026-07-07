from __future__ import annotations

import subprocess
import shutil
import sys
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import messagebox

from license_keys import app_data_dir


APP_NAME = "夏令营日程助手"
APP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SummerCampPlanner"
INSTALL_FOLDER_NAMES = {"夏令营日程助手", "SummerCampPlanner"}
INSTALL_MARKER_NAME = ".summer_camp_planner_install"
APP_EXE_NAMES = {"夏令营日程助手.exe", "SummerCampPlanner.exe"}
UNINSTALL_EXE_NAME = "卸载夏令营日程助手.exe"
_SINGLE_INSTANCE_HANDLES: list[object] = []


def acquire_single_instance(name: str) -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(handle)
            return False
        _SINGLE_INSTANCE_HANDLES.append(handle)
    except Exception:
        return True
    return True


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(__file__).resolve().parent


def installed_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_safe_install_dir(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    if resolved.anchor == str(resolved):
        return False
    if resolved.name not in INSTALL_FOLDER_NAMES:
        return False
    if not (resolved / INSTALL_MARKER_NAME).exists():
        return False
    if not (resolved / UNINSTALL_EXE_NAME).exists():
        return False
    return any((resolved / name).exists() for name in APP_EXE_NAMES)


def apply_app_icon(window: tk.Misc) -> None:
    icon_path = resource_dir() / "assets" / "app.ico"
    if icon_path.exists():
        try:
            window.iconbitmap(str(icon_path))
        except tk.TclError:
            pass


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def main() -> None:
    if not acquire_single_instance("Local\\SummerCampPlanner-Uninstaller"):
        return
    root = tk.Tk()
    apply_app_icon(root)
    root.withdraw()
    install_dir = installed_dir()
    data_dir = app_data_dir()
    if not is_safe_install_dir(install_dir):
        messagebox.showerror(
            "卸载失败",
            "当前目录不像夏令营日程助手的安装目录，为避免误删文件，已取消卸载。\n\n"
            f"当前目录：\n{install_dir}",
        )
        return
    if not messagebox.askyesno("确认卸载", f"将删除软件安装目录：\n{install_dir}\n\n同时删除全部用户数据和 AI 设置。继续吗？"):
        return
    desktop_shortcut = Path.home() / "Desktop" / "夏令营日程助手.lnk"
    try:
        if desktop_shortcut.exists():
            desktop_shortcut.unlink()
    except Exception:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, APP_REG_KEY)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    script = Path.home() / "AppData" / "Local" / "Temp" / "_finish_summer_camp_uninstall.bat"
    script.write_text(
        "@echo off\n"
        "ping 127.0.0.1 -n 2 > nul\n"
        f'rd /s /q "{data_dir}"\n'
        f'rd /s /q "{install_dir}"\n'
        'del "%~f0" > nul 2> nul\n',
        encoding="gbk",
    )
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **hidden_subprocess_kwargs(),
    )
    root.destroy()


if __name__ == "__main__":
    main()
