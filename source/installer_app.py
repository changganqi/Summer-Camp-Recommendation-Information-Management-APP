from __future__ import annotations

import os
import winreg
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from license_keys import activate_license, fetch_network_datetime, validate_key_for_install, validate_saved_license


APP_NAME = "夏令营日程助手"
APP_PUBLISHER = "夏令营日程助手"
APP_VERSION = "1.0.2"
APP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SummerCampPlanner"
INSTALL_FOLDER_NAME = "夏令营日程助手"
INSTALL_MARKER_NAME = ".summer_camp_planner_install"
SOURCE_EXE_NAME = "SummerCampPlanner.exe"
APP_EXE_NAME = "夏令营日程助手.exe"
UNINSTALL_EXE_NAME = "卸载夏令营日程助手.exe"
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
    "user_data",
    "__pycache__",
}


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


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


def validate_installed_runtime(install_dir: Path) -> None:
    app_exe = install_dir / APP_EXE_NAME
    python_dll = install_dir / "_internal" / "python312.dll"
    if not app_exe.exists() or app_exe.stat().st_size < 1024 * 1024:
        raise RuntimeError("主程序文件安装失败，可能被安全软件拦截。请检查 Windows 安全中心的保护历史记录后重新安装。")
    if not python_dll.exists() or python_dll.stat().st_size < 1024 * 1024:
        raise RuntimeError("运行库文件安装失败，可能被安全软件拦截或安装目录残缺。请重新安装，仍然失败请换一个安装目录。")


def create_shortcut(target: Path, shortcut: Path) -> None:
    ps = (
        "$WshShell = New-Object -comObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut}'); "
        f"$Shortcut.TargetPath = '{target}'; "
        f"$Shortcut.WorkingDirectory = '{target.parent}'; "
        "$Shortcut.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], check=False)


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

    def install(self) -> None:
        if self.installing:
            return
        key = self.key_var.get().strip()
        target = resolve_install_dir(self.path_var.get())
        self.set_installing(True)
        self.set_progress(8, "正在安装，请稍候...")
        try:
            network_now = fetch_network_datetime()
        except Exception:
            messagebox.showerror("安装失败", "密钥无效或已过期，请检查网络后重试；仍然失败请联系作者。", parent=self)
            self.progress.configure(value=0)
            self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
            self.set_installing(False)
            return
        ok, message = validate_key_for_install(key, install_dir=target, check_time=True, network_now=network_now)
        if not ok:
            messagebox.showerror("安装失败", "密钥无效或已过期，请检查网络后重试；仍然失败请联系作者。", parent=self)
            self.progress.configure(value=0)
            self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
            self.set_installing(False)
            return
        self.set_progress(28, "正在安装，请稍候...")
        source = resource_dir() / "app_bundle"
        if not source.exists():
            messagebox.showerror("安装失败", "安装包缺少 app_bundle。", parent=self)
            self.progress.configure(value=0)
            self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
            self.set_installing(False)
            return
        try:
            self.set_progress(45, "正在安装，请稍候...")
            target.mkdir(parents=True, exist_ok=True)
            clean_install_dir_private_files(target)
            write_install_marker(target)
            items = list(source.iterdir())
            total = max(1, len(items))
            for index, item in enumerate(items, start=1):
                dest = target / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                self.set_progress(45 + int(index / total * 25), "正在安装，请稍候...")
            uninstaller = resource_dir() / "uninstall_app.exe"
            if uninstaller.exists():
                shutil.copy2(uninstaller, target / UNINSTALL_EXE_NAME)

            source_exe = target / SOURCE_EXE_NAME
            app_exe = target / APP_EXE_NAME
            if source_exe.exists():
                if app_exe.exists():
                    app_exe.unlink()
                source_exe.rename(app_exe)
            validate_installed_runtime(target)

            self.set_progress(78, "正在安装，请稍候...")
            ok, message = activate_license(key, install_dir=target, check_time=True, network_now=network_now)
            if not ok:
                messagebox.showerror("安装失败", "密钥无效或已过期，请检查网络后重试；仍然失败请联系作者。", parent=self)
                self.progress.configure(value=0)
                self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
                self.set_installing(False)
                return
            ok, message = validate_saved_license(install_dir=target, check_time=True, network_now=network_now)
            if not ok:
                messagebox.showerror("安装失败", "安装未完成，请重新运行安装程序；仍然失败请联系作者。", parent=self)
                self.progress.configure(value=0)
                self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
                self.set_installing(False)
                return

            exe = target / APP_EXE_NAME
            desktop = Path.home() / "Desktop" / "夏令营日程助手.lnk"
            create_shortcut(exe, desktop)
            register_uninstaller(target)
            self.set_progress(100, "安装完成，即将关闭...")
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc), parent=self)
            self.progress.configure(value=0)
            self.status_var.set("需要打赏获得密钥，请联系作者闲鱼用户名：满天星的")
            self.set_installing(False)
            return
        self.after(900, self.destroy)


if __name__ == "__main__":
    Installer().mainloop()
