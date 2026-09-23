from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
import winreg
from base64 import b64encode
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from license_keys import activate_license, fetch_network_datetime, validate_key_for_install, validate_saved_license


APP_NAME = "夏令营日程助手"
APP_PUBLISHER = "夏令营日程助手"
APP_VERSION = "1.5.3"
APP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SummerCampPlanner"
INSTALL_FOLDER_NAME = "夏令营日程助手"
INSTALL_MARKER_NAME = ".summer_camp_planner_install"
SOURCE_EXE_NAME = "SummerCampPlanner.exe"
APP_EXE_NAME = "夏令营日程助手.exe"
UNINSTALL_EXE_NAME = "卸载夏令营日程助手.exe"
INSTALLER_EXE_NAMES = {
    "summercampplannersetup.exe",
    "summercampplannersetup.tmp",
    "summercampplannerinstaller.exe",
}
INSTALL_DIR_DATA_NAMES = {
    "settings.json",
    "summer_camps.sqlite3",
    "summer_camps.db",
    "license.json",
    "license.dat",
    "activation_registry.json",
    "license_diagnostics.log",
    "app.log",
    "debug.log",
    "personal_profile.txt",
    "personal_profile_data.json",
    "user_data",
    "__pycache__",
}
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
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def payload_root() -> Path:
    candidates = [resource_dir()]
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, executable_dir.parent))
    candidates.append(Path(__file__).resolve().parent)

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "app_bundle").is_dir():
            return resolved
    return resource_dir()


def apply_app_icon(window: tk.Misc) -> None:
    icon_path = resource_dir() / "assets" / "app.ico"
    if icon_path.exists():
        try:
            window.iconbitmap(str(icon_path))
        except tk.TclError:
            pass


def default_install_dir() -> Path:
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / INSTALL_FOLDER_NAME
    return Path.home() / INSTALL_FOLDER_NAME


def resolve_install_dir(selected_path: str) -> Path:
    raw = Path(selected_path).expanduser()
    app_folder_names = {INSTALL_FOLDER_NAME, "SummerCampPlanner"}
    if raw.name in app_folder_names:
        return raw
    return raw / INSTALL_FOLDER_NAME


def write_install_marker(install_dir: Path) -> None:
    (install_dir / INSTALL_MARKER_NAME).write_text(APP_NAME, encoding="utf-8")


def clean_install_dir_private_files(install_dir: Path) -> None:
    for name in INSTALL_DIR_DATA_NAMES:
        target = install_dir / name
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except Exception:
            pass


def is_existing_install_dir(install_dir: Path) -> bool:
    return (install_dir / INSTALL_MARKER_NAME).is_file() or any(
        (install_dir / name).is_file() for name in (APP_EXE_NAME, SOURCE_EXE_NAME)
    )


def windows_processes_in_directory(directory: Path) -> list[tuple[int, Path]]:
    if sys.platform != "win32" or not directory.exists():
        return []

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    target = os.path.normcase(os.path.abspath(directory))
    matches: list[tuple[int, Path]] = []
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return matches
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            if pid and pid != os.getpid():
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            if Path(buffer.value).name.casefold() in INSTALLER_EXE_NAMES:
                                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
                                continue
                            process_path = os.path.normcase(os.path.abspath(buffer.value))
                            try:
                                inside_target = os.path.commonpath((target, process_path)) == target
                            except ValueError:
                                inside_target = False
                            if inside_target:
                                matches.append((pid, Path(buffer.value)))
                    finally:
                        kernel32.CloseHandle(handle)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return matches


def close_running_install_processes(install_dir: Path) -> list[Path]:
    processes = windows_processes_in_directory(install_dir)
    if not processes:
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pids = {pid for pid, _path in processes}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def request_window_close(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True

    user32.EnumWindows(request_window_close, 0)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if not windows_processes_in_directory(install_dir):
            return []
        time.sleep(0.2)

    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    failed: list[Path] = []
    for pid, path in windows_processes_in_directory(install_dir):
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if not handle:
            failed.append(path)
            continue
        try:
            if not kernel32.TerminateProcess(handle, 0):
                failed.append(path)
                continue
            kernel32.WaitForSingleObject(handle, 3000)
        finally:
            kernel32.CloseHandle(handle)
    return failed


def remove_directory_with_retry(directory: Path, attempts: int = 10) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if directory.exists():
                shutil.rmtree(directory)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    raise PermissionError(
        "旧版程序文件仍被占用，无法完成更新。请重启电脑后不要打开旧版软件，直接运行安装包；"
        "若仍失败，请将安装目录加入安全软件信任区。"
    ) from last_error


def copy_path_with_retry(source: Path, destination: Path, attempts: int = 8) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    raise PermissionError(
        f"无法写入 {destination.name}，文件被其他程序或安全软件占用。"
        "请重启电脑后直接安装，或将安装目录加入安全软件信任区。"
    ) from last_error


def source_installer_argument() -> Path | None:
    try:
        index = sys.argv.index("--source-installer")
        value = sys.argv[index + 1]
    except (ValueError, IndexError):
        return None
    path = Path(value).expanduser()
    return path if path.suffix.casefold() == ".exe" else None


def schedule_installer_self_delete(path: Path | None) -> None:
    if sys.platform != "win32" or path is None or not path.is_file():
        return
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        return
    escaped = str(path.resolve()).replace("'", "''")
    script = (
        f"$p='{escaped}'; "
        "for($i=0;$i -lt 120;$i++){"
        "try{Remove-Item -LiteralPath $p -Force -ErrorAction Stop;break}"
        "catch{Start-Sleep -Milliseconds 500}}"
    )
    encoded = b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.Popen(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        pass


def validate_installed_runtime(install_dir: Path) -> None:
    app_exe = install_dir / APP_EXE_NAME
    # 安装器与主程序由 build_release.py 使用同一个 Python 构建。
    python_dll = install_dir / "_internal" / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    if not app_exe.exists() or app_exe.stat().st_size < 1024 * 1024:
        raise RuntimeError("主程序文件安装失败，可能被安全软件拦截。请检查 Windows 安全中心的保护历史记录后重新安装。")
    if not python_dll.is_file():
        raise RuntimeError(f"安装目录缺少运行库 {python_dll.name}，请重新获取完整安装包后安装。")
    if python_dll.stat().st_size < 1024 * 1024:
        raise RuntimeError(f"运行库 {python_dll.name} 文件不完整，请重新安装。")


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


def create_shortcut(target: Path, shortcut: Path) -> None:
    shortcut_text = str(shortcut).replace("'", "''")
    target_text = str(target).replace("'", "''")
    workdir_text = str(target.parent).replace("'", "''")
    ps = (
        "$WshShell = New-Object -comObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_text}'); "
        f"$Shortcut.TargetPath = '{target_text}'; "
        f"$Shortcut.WorkingDirectory = '{workdir_text}'; "
        "$Shortcut.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **hidden_subprocess_kwargs(),
    )


def folder_size_mb(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return max(1, total // 1024 // 1024)


def register_uninstaller(install_dir: Path) -> None:
    exe = install_dir / APP_EXE_NAME
    uninstaller = install_dir / UNINSTALL_EXE_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APP_REG_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstaller}"')
        winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, f'"{uninstaller}"')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, folder_size_mb(install_dir) * 1024)


class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} 安装程序")
        apply_app_icon(self)
        self.geometry("620x300")
        self.resizable(False, False)
        self.key_var = tk.StringVar()
        self.path_var = tk.StringVar(value=str(default_install_dir()))
        self.status_var = tk.StringVar(value="需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
        self.installing = False
        self.install_succeeded = False
        self.build()

    def build(self) -> None:
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="安装密钥").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=self.key_var, show="*").grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(body, text="安装目录").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=self.path_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(body, text="浏览", command=self.choose_dir).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(
            body,
            textvariable=self.status_var,
            foreground="#64748b",
            wraplength=500,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 12))
        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e")
        self.exit_button = ttk.Button(buttons, text="退出", command=self.destroy)
        self.exit_button.pack(side="right", padx=(8, 0))
        self.install_button = ttk.Button(buttons, text="安装", command=self.install)
        self.install_button.pack(side="right")

    def set_progress(self, value: int, text: str) -> None:
        self.progress.configure(value=value)
        self.status_var.set(text)
        self.update_idletasks()

    def set_installing(self, installing: bool) -> None:
        self.installing = installing
        state = "disabled" if installing else "normal"
        self.install_button.configure(state=state)
        self.exit_button.configure(state=state)

    def choose_dir(self) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.path_var.get() or str(default_install_dir()))
        if path:
            self.path_var.set(path)

    def reset_after_failure(self) -> None:
        self.progress.configure(value=0)
        self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
        self.set_installing(False)

    def install(self) -> None:
        if self.installing:
            return
        key = self.key_var.get().strip()
        target = resolve_install_dir(self.path_var.get())
        self.set_installing(True)
        self.set_progress(8, "正在连接服务器...")
        try:
            network_now = fetch_network_datetime()
        except Exception as exc:
            messagebox.showerror(
                "安装失败",
                "无法连接服务器，请检查网络或切换/关闭 VPN 后重试。\n\n"
                f"{str(exc)[:800]}",
                parent=self,
            )
            self.reset_after_failure()
            return
        ok, message = validate_key_for_install(key, install_dir=target, check_time=True, network_now=network_now)
        if not ok:
            messagebox.showerror("安装失败", message, parent=self)
            self.reset_after_failure()
            return
        self.set_progress(28, "正在安装，请稍候...")
        package_root = payload_root()
        source = package_root / "app_bundle"
        if not source.exists():
            messagebox.showerror("安装失败", "安装包缺少 app_bundle。", parent=self)
            self.reset_after_failure()
            return
        try:
            self.set_progress(34, "正在检查旧版本...")
            if is_existing_install_dir(target):
                failed_processes = close_running_install_processes(target)
                if failed_processes:
                    raise PermissionError(
                        "旧版软件未能完全退出，请在任务管理器中结束夏令营日程助手后重试。"
                    )
                self.set_progress(40, "正在清理旧版本...")
                remove_directory_with_retry(target)
            self.set_progress(45, "正在安装，请稍候...")
            target.mkdir(parents=True, exist_ok=True)
            clean_install_dir_private_files(target)
            write_install_marker(target)
            items = list(source.iterdir())
            total = max(1, len(items))
            for index, item in enumerate(items, start=1):
                dest = target / item.name
                copy_path_with_retry(item, dest)
                self.set_progress(45 + int(index / total * 25), "正在安装，请稍候...")

            uninstaller = package_root / "uninstall_app.exe"
            if uninstaller.exists():
                copy_path_with_retry(uninstaller, target / UNINSTALL_EXE_NAME)

            source_exe = target / SOURCE_EXE_NAME
            app_exe = target / APP_EXE_NAME
            if source_exe.exists():
                if app_exe.exists():
                    app_exe.unlink()
                source_exe.rename(app_exe)
            validate_installed_runtime(target)

            self.set_progress(78, "正在写入授权...")
            ok, message = activate_license(key, install_dir=target, check_time=True, network_now=network_now)
            if not ok:
                messagebox.showerror("安装失败", message, parent=self)
                self.reset_after_failure()
                return
            ok, message = validate_saved_license(install_dir=target, check_time=True, network_now=network_now)
            if not ok:
                messagebox.showerror("安装失败", "安装未完成，请重新运行安装程序；仍然失败请联系作者。", parent=self)
                self.reset_after_failure()
                return

            exe = target / APP_EXE_NAME
            desktop = Path.home() / "Desktop" / "夏令营日程助手.lnk"
            create_shortcut(exe, desktop)
            register_uninstaller(target)
            self.install_succeeded = True
            self.set_progress(100, "安装完成，即将关闭...")
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc), parent=self)
            self.reset_after_failure()
            return
        self.after(900, self.destroy)


if __name__ == "__main__":
    if acquire_single_instance("Local\\SummerCampPlanner-Installer"):
        source_installer = source_installer_argument()
        installer = Installer()
        installer.mainloop()
        if installer.install_succeeded:
            schedule_installer_self_delete(source_installer)
