from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "release"
APP_BUNDLE = DIST / "app_bundle"
ICON = ROOT / "assets" / "app.ico"
FORBIDDEN_RELEASE_NAMES = {
    "settings.json",
    "summer_camps.sqlite3",
    "summer_camps.db",
    "license.json",
    "license.dat",
    "activation_registry.json",
    "license_diagnostics.log",
    "personal_profile.txt",
    "private_generate_license.py",
    "private_license_generator.html",
    "summer_note.html",
    "user_data",
    "__pycache__",
}
FORBIDDEN_RELEASE_SUFFIXES = {".log", ".db", ".sqlite", ".sqlite3", ".csv", ".xlsx", ".xls"}
WINDOWS_RUNTIME_DLL_PATTERNS = (
    "libssl*.dll",
    "libcrypto*.dll",
    "vcruntime140*.dll",
    "msvcp140*.dll",
)
PLAYWRIGHT_BROWSER_DIR_NAMES = (
    "chromium-*",
    "chromium_headless_shell-*",
)


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def run_with_env(cmd: list[str], env: dict[str, str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def clean() -> None:
    for path in [ROOT / "build", ROOT / "dist", DIST]:
        if path.exists():
            shutil.rmtree(path)


def is_forbidden_release_path(path: Path) -> bool:
    if path.name in FORBIDDEN_RELEASE_NAMES:
        return True
    if path.suffix.lower() in FORBIDDEN_RELEASE_SUFFIXES:
        return True
    return False


def remove_private_files(path: Path) -> None:
    if not path.exists():
        return
    for item in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if is_forbidden_release_path(item):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)


def assert_clean_release_tree(path: Path) -> None:
    if not path.exists():
        return
    offenders = [item for item in path.rglob("*") if is_forbidden_release_path(item)]
    if offenders:
        details = "\n".join(str(item.relative_to(path)) for item in offenders[:20])
        raise RuntimeError(f"发布包里发现不应打包的本地数据文件：\n{details}")


def windows_runtime_search_dirs() -> list[Path]:
    prefixes = {
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.exec_prefix),
        Path(sys.executable).resolve().parent,
    }
    dirs: list[Path] = []
    for prefix in prefixes:
        dirs.extend(
            [
                prefix,
                prefix / "DLLs",
                prefix / "Library" / "bin",
            ]
        )

    seen: set[str] = set()
    result: list[Path] = []
    for directory in dirs:
        try:
            resolved = str(directory.resolve()).lower()
        except OSError:
            continue
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        result.append(directory)
    return result


def collect_windows_runtime_binaries() -> list[Path]:
    if sys.platform != "win32":
        return []

    found: dict[str, Path] = {}
    for directory in windows_runtime_search_dirs():
        for pattern in WINDOWS_RUNTIME_DLL_PATTERNS:
            for dll in directory.glob(pattern):
                found.setdefault(dll.name.lower(), dll)

    required_prefixes = ("libssl", "libcrypto")
    missing = [prefix for prefix in required_prefixes if not any(name.startswith(prefix) for name in found)]
    if missing:
        searched = "\n".join(str(path) for path in windows_runtime_search_dirs()[:40])
        raise RuntimeError(
            "没有找到 Python SSL 运行所需 DLL："
            + ", ".join(missing)
            + "\n请确认当前 Python/Conda 环境可正常 import ssl。\n已搜索：\n"
            + searched
        )

    return sorted(found.values(), key=lambda path: path.name.lower())


def add_binary_args(paths: list[Path]) -> list[str]:
    args: list[str] = []
    for path in paths:
        args.extend(["--add-binary", f"{path};."])
    return args


def assert_windows_ssl_runtime(path: Path) -> None:
    if sys.platform != "win32":
        return
    if not path.exists():
        raise RuntimeError(f"应用目录不存在：{path}")
    names = {item.name.lower() for item in path.rglob("*") if item.is_file()}
    missing = []
    if not any(name.startswith("libssl") and name.endswith(".dll") for name in names):
        missing.append("libssl*.dll")
    if not any(name.startswith("libcrypto") and name.endswith(".dll") for name in names):
        missing.append("libcrypto*.dll")
    if missing:
        raise RuntimeError("发布包缺少 SSL 运行库：" + ", ".join(missing))


def playwright_package_dir() -> Path:
    try:
        import playwright
    except Exception as exc:
        raise RuntimeError("当前 Python 环境缺少 playwright，无法打包网页抓取功能。") from exc
    return Path(playwright.__file__).resolve().parent / "driver" / "package"


def playwright_local_browsers_dir() -> Path:
    return playwright_package_dir() / ".local-browsers"


def has_playwright_chromium(local_browsers: Path) -> bool:
    if not local_browsers.exists():
        return False
    for pattern in PLAYWRIGHT_BROWSER_DIR_NAMES:
        if any(local_browsers.glob(pattern)):
            continue
        return False
    return True


def ensure_playwright_chromium() -> Path | None:
    if sys.platform != "win32":
        return None
    env = dict(**os.environ, PLAYWRIGHT_BROWSERS_PATH="0")
    local_browsers = playwright_local_browsers_dir()
    if not has_playwright_chromium(local_browsers):
        run_with_env([sys.executable, "-m", "playwright", "install", "chromium"], env)
    if not has_playwright_chromium(local_browsers):
        raise RuntimeError(
            "Playwright Chromium 下载或安装失败，发布包不能缺少内置浏览器。\n"
            f"期望目录：{local_browsers}"
        )
    return local_browsers


def add_data_args(paths: list[tuple[Path, str]]) -> list[str]:
    args: list[str] = []
    for source, target in paths:
        args.extend(["--add-data", f"{source};{target}"])
    return args


def assert_playwright_chromium_runtime(path: Path) -> None:
    if sys.platform != "win32":
        return
    local_browsers = path / "_internal" / "playwright" / "driver" / "package" / ".local-browsers"
    if not has_playwright_chromium(local_browsers):
        raise RuntimeError("发布包缺少 Playwright 内置 Chromium：" + str(local_browsers))


def build() -> None:
    clean()
    if not ICON.exists():
        run([sys.executable, "tools/make_icon.py"])
    runtime_binaries = collect_windows_runtime_binaries()
    playwright_browsers = ensure_playwright_chromium()
    extra_datas: list[tuple[Path, str]] = [(ROOT / "assets", "assets")]
    if playwright_browsers is not None:
        extra_datas.append((playwright_browsers, "playwright/driver/package/.local-browsers"))
    if runtime_binaries:
        print("Including Windows runtime DLLs:")
        for dll in runtime_binaries:
            print(f"  - {dll}")
    if playwright_browsers is not None:
        print(f"Including Playwright browsers: {playwright_browsers}")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--name",
            "SummerCampPlanner",
            "--icon",
            str(ICON),
            *add_data_args(extra_datas),
            *add_binary_args(runtime_binaries),
            "summer_camp_planner.py",
        ]
    )
    DIST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "dist" / "SummerCampPlanner", APP_BUNDLE)
    remove_private_files(APP_BUNDLE)
    assert_clean_release_tree(APP_BUNDLE)
    assert_windows_ssl_runtime(APP_BUNDLE)
    assert_playwright_chromium_runtime(APP_BUNDLE)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--onefile",
            "--name",
            "uninstall_app",
            "--icon",
            str(ICON),
            "--add-data",
            f"{ROOT / 'assets'};assets",
            "uninstall_app.py",
        ]
    )
    shutil.copy2(ROOT / "dist" / "uninstall_app.exe", DIST / "uninstall_app.exe")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--onefile",
            "--name",
            "SummerCampPlannerSetup",
            "--icon",
            str(ICON),
            "--add-data",
            f"{ROOT / 'assets'};assets",
            "--add-data",
            f"{APP_BUNDLE};app_bundle",
            "--add-data",
            f"{DIST / 'uninstall_app.exe'};.",
            "installer_app.py",
        ]
    )
    shutil.copy2(ROOT / "dist" / "SummerCampPlannerSetup.exe", DIST / "SummerCampPlannerSetup.exe")
    assert_clean_release_tree(DIST)
    shutil.rmtree(APP_BUNDLE)
    (DIST / "uninstall_app.exe").unlink(missing_ok=True)
    print(f"release ready: {DIST}")


if __name__ == "__main__":
    build()
